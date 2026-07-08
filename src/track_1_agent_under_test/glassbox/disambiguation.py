"""
Disambiguierungs-Motor — Stufe 6.

Zwei Task-Untertypen:
  disambiguation_internal — NIE fragen, intern aus Praeferenzen/Kontext loesen
  disambiguation_user     — MUSS fragen, wenn mehr als ein gueltiger Kandidat bleibt

Architektur (ADR-0005): der Motor laeuft als Pre-Flight-Guard in der PLAN-Schleife,
NICHT als separater Pre-Plan-Schritt. Praeferenzen (Prioritaet 2) und Kontext
(Prioritaet 4) liegen erst nach aktivem Abruf im Ledger, deshalb kann der Guard —
wie AUT-POL:009 in Stufe 4 — einen get_user_preferences-Call injizieren und den
state-changing Call zurueckstellen, bis die Praeferenz vorliegt.

Auflösungs-Kaskade (deterministisch, feste Reihenfolge aus wiki.md):
  0. Policy-Regeln (Prohibition schliesst Kandidaten aus)
  1. Expliziter User-Request (Intake-Flag user_stated) → Slot nicht mehrdeutig
  2. Gelernte Praeferenzen (get_user_preferences) → STILL anwenden
  3. Heuristische Defaults (z. B. Multi-Stop = fastest) → STILL anwenden
  4. Kontext (Fahrzeugzustand) ergibt genau einen Kandidaten → STILL anwenden
  5. Sonst, state-changing + ≥2 gueltige Kandidaten → EINE gezielte Rueckfrage

Lesson 1a: das LLM liefert nur Kandidaten (Intake flaggt mehrdeutige Slots; eine
enge Extraktion strukturiert die freitextliche Praeferenz in {default, prohibited}).
Der Code ENTSCHEIDET per Map-Lookup und ueberschreibt den aufgeloesten Wert direkt
im Call-Argument (Value-Flow-Garantie) — nie dem Planner-LLM ueberlassen.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from loguru import logger as _log


# --- heuristic defaults (Prioritaet 3): policy-dokumentierte Standardwerte ---
# Nur eindeutige, in wiki.md benannte Defaults. Keine erfundenen Werte.
# Schluessel: (tool, argument) → Default-Wert (str; spaeter typ-koerziert).
_HEURISTIC_DEFAULTS: dict[tuple[str, str], str] = {
    # Multi-Stop-Routen: fastest, wenn keine Praeferenz/kein expliziter Wunsch
    ("get_routes_from_start_to_destination", "route_type"): "fastest",
}

# tool → get_user_preferences-Kategorie, die fuer diesen Slot relevant ist.
# Bestimmt, welche Kategorie beim Injizieren angefragt wird (breit, keine Antwort erzwungen).
_TOOL_PREF_CATEGORY: dict[str, tuple[str, str]] = {
    "open_close_sunroof": ("vehicle_settings", "vehicle_settings"),
    "open_close_sunshade": ("vehicle_settings", "vehicle_settings"),
    "control_window": ("vehicle_settings", "vehicle_settings"),
    "set_climate_temperature": ("vehicle_settings", "climate_control"),
    "get_routes_from_start_to_destination": ("navigation_and_routing", "route_selection"),
}


@dataclass
class PreferenceSlot:
    """Structured preference for one ambiguous slot (LLM extraction output).

    default: value to apply silently, or None if the preferences say nothing.
    prohibited: values the user never wants (eliminated as candidates).
    """
    default: str | None = None
    prohibited: list[str] = field(default_factory=list)


@dataclass
class SlotResolution:
    """Outcome of the cascade for one ambiguous (tool, argument) slot."""
    status: str                 # "resolved" | "ask" | "unresolved"
    value: str | None = None
    question: str = ""
    priority: str = ""          # which cascade layer decided (for telemetry)


@dataclass
class PreFlightDisambiguation:
    """What the plan loop must do with a batch of planned calls."""
    calls: list = field(default_factory=list)          # calls with resolved args
    inject_preferences: dict | None = None             # get_user_preferences args, or None
    question: str = ""                                  # non-empty → BLOCK + Rückfrage
    resolved: list = field(default_factory=list)        # (tool, arg, value) telemetry


def _coerce(value: str, sample: object) -> object:
    """Coerce a preference/default string to the argument's runtime type.

    Uses a sibling sample value from the planned call when available, else
    parses a bare number ("50%" → 50). Conservative: unknown → return string.
    """
    if value is None:
        return value
    text = value.strip()
    if isinstance(sample, bool):
        return text.lower() in {"true", "yes", "on", "1"}
    if isinstance(sample, int) and not isinstance(sample, bool):
        m = re.search(r"-?\d+", text)
        return int(m.group()) if m else text
    if isinstance(sample, float):
        m = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(m.group()) if m else text
    # no typed sample — parse a bare percentage/number if that is all it is
    m = re.fullmatch(r"(-?\d+)\s*%?", text)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(-?\d+\.\d+)\s*%?", text)
    if m:
        return float(m.group(1))
    return text


class DisambiguationEngine:
    """Deterministic resolution cascade (Lesson 1a: code decides)."""

    # --- pure cascade (unit-tested with fakes, no LLM, no ledger) ---

    def resolve_slot(
        self,
        *,
        is_state_changing: bool,
        pref: PreferenceSlot | None,
        heuristic_default: str | None,
        context_candidates: list[str] | None,
        question: str,
    ) -> SlotResolution:
        """Apply priorities 0/2/3/4/5 for a single ambiguous slot.

        Priority 1 (explicit user request) is handled upstream: an explicitly
        stated value means the slot is not ambiguous and never reaches here.
        Ranking of valid candidates is forbidden — only elimination.
        """
        prohibited = {p.strip().lower() for p in (pref.prohibited if pref else [])}

        def _ok(v: str) -> bool:
            return v.strip().lower() not in prohibited

        # Priority 2: learned preference default — apply SILENTLY.
        if pref and pref.default is not None and _ok(pref.default):
            return SlotResolution(status="resolved", value=pref.default, priority="preference")

        # Priority 3: unambiguous heuristic/policy default — apply SILENTLY.
        if heuristic_default is not None and _ok(heuristic_default):
            return SlotResolution(status="resolved", value=heuristic_default, priority="heuristic")

        # Priority 4: context yields exactly one valid candidate — apply SILENTLY.
        valid = [c for c in (context_candidates or []) if _ok(c)]
        if len(valid) == 1:
            return SlotResolution(status="resolved", value=valid[0], priority="context")

        # Priority 5: state-changing with ≥2 valid candidates (or none known) → ask.
        if is_state_changing:
            return SlotResolution(status="ask", question=question, priority="user")

        return SlotResolution(status="unresolved", priority="none")

    # --- plan-loop pre-flight guard (runtime; uses ledger + narrow LLM extraction) ---

    def pre_flight(
        self,
        ctx: "TurnContext",                         # type: ignore[name-defined]
        calls: list,
        *,
        extractor: Callable[..., PreferenceSlot] | None = None,
    ) -> PreFlightDisambiguation:
        """Resolve ambiguous value slots on the planned batch.

        - Missing preferences that could help → inject get_user_preferences, defer.
        - Resolvable → override the argument in the call (value-flow guarantee).
        - Genuinely open + state-changing → return a clarification question (BLOCK).
        """
        ambiguities = ctx.intent.get("value_ambiguities", []) if ctx.intent else []
        if not ambiguities:
            return PreFlightDisambiguation(calls=calls)

        prefs_available = ctx.ledger.has_tool_result("get_user_preferences")
        by_tool: dict[str, list[dict]] = {}
        for a in ambiguities:
            if not a.get("user_stated") and a.get("tool") and a.get("argument"):
                by_tool.setdefault(a["tool"], []).append(a)

        if not by_tool:
            return PreFlightDisambiguation(calls=calls)

        # Gather step: if a preference could resolve this and none is in the
        # ledger yet, retrieve it first and defer the state-changing calls.
        if not prefs_available and not getattr(ctx, "preferences_gathered", False):
            pref_args = self._preference_request_for(by_tool.keys())
            if pref_args is not None:
                return PreFlightDisambiguation(calls=[], inject_preferences=pref_args)

        extractor = extractor or self._default_extractor
        out_calls = []
        resolved: list[tuple] = []
        for call in calls:
            slots = by_tool.get(call.tool)
            if not slots:
                out_calls.append(call)
                continue
            new_args = dict(call.arguments)
            for slot in slots:
                arg = slot["argument"]
                pref = extractor(ctx, call.tool, arg) if prefs_available else None
                heuristic = _HEURISTIC_DEFAULTS.get((call.tool, arg))
                question = (
                    slot.get("question")
                    or ctx.intent.get("clarification_question", "").strip()
                    or "Could you tell me the exact value you'd like me to use?"
                )
                res = self.resolve_slot(
                    is_state_changing=bool(ctx.intent.get("is_state_changing", True)),
                    pref=pref,
                    heuristic_default=heuristic,
                    context_candidates=slot.get("candidates"),
                    question=question,
                )
                if res.status == "ask":
                    _log.info(
                        "Disambiguation: user clarification required",
                        tool=call.tool, argument=arg, priority=res.priority,
                    )
                    return PreFlightDisambiguation(calls=[], question=res.question)
                if res.status == "resolved":
                    new_args[arg] = _coerce(res.value, call.arguments.get(arg))
                    resolved.append((call.tool, arg, new_args[arg]))
                    _log.info(
                        "Disambiguation: resolved silently",
                        tool=call.tool, argument=arg,
                        value=new_args[arg], priority=res.priority,
                    )
            out_calls.append(self._with_args(call, new_args))
        return PreFlightDisambiguation(calls=out_calls, resolved=resolved)

    # --- helpers ---

    def _preference_request_for(self, tools) -> dict | None:
        categories: dict[str, dict[str, bool]] = {}
        for tool in tools:
            cat = _TOOL_PREF_CATEGORY.get(tool)
            if cat:
                categories.setdefault(cat[0], {})[cat[1]] = True
        if not categories:
            return None
        return {"preference_categories": categories}

    def _with_args(self, call, new_args: dict):
        from .state_machine import PlannedCall
        return PlannedCall(
            tool=call.tool,
            arguments=new_args,
            call_id=call.call_id,
            rationale=call.rationale,
        )

    def _default_extractor(self, ctx, tool: str, argument: str) -> PreferenceSlot:
        from .prompts import clarify
        try:
            return clarify.extract_preference(ctx, tool, argument)
        except NotImplementedError:
            return PreferenceSlot()


try:
    from .state_machine import TurnContext
    from .ledger import Ledger
except ImportError:
    pass
