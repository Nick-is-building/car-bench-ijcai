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

import json
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
    "set_ambient_lights": ("vehicle_settings", "vehicle_settings"),
    "set_climate_temperature": ("vehicle_settings", "climate_control"),
    "get_routes_from_start_to_destination": ("navigation_and_routing", "route_selection"),
}


# tool → the single preference-driven value argument. Gates the deterministic
# PRE-PLAN preference gather (OI-016): kept intentionally minimal so the pre-plan
# retrieval only fires for tools whose value legitimately comes from a stored
# preference and where the planner otherwise cannot draft a call at all (e.g. the
# ambient-light color). Tools resolved by the normal planner-first cascade are NOT
# listed here — that keeps the blast radius tiny.
_TOOL_PREF_VALUE_ARG: dict[str, str] = {
    "set_ambient_lights": "lightcolor",
}


# --- ledger-derived value rules (Prioritaet 4: Kontext ergibt genau einen Wert) ---
# Some value slots cannot be filled by a preference/default/candidate list: the
# value must be DERIVED from an earlier tool result already in the ledger. Two
# families, both Lesson 1a (the LLM only flags that a value is missing; code
# computes it from ledger data via a documented rule — never invents it):
#   selection — pick the objective-best id from a prior result list;
#   relative  — a current reading +/- one step for an up/down adjustment.
# Keyed by (tool, schema-argument), like the maps above: the tool name is
# configuration of a generic mechanism, not a hardcoded task answer.

@dataclass(frozen=True)
class _SelectionRule:
    source_tool: str            # ledger tool whose result holds the candidates
    collection: str             # key under result holding the list ("" → result IS the list)
    id_field: str               # field on each candidate to inject
    minimize: str               # numeric field to minimize (objective criterion)
    tie_break: str | None = None  # secondary numeric field to minimize on ties


@dataclass(frozen=True)
class _RelativeRule:
    source_tool: str            # ledger tool whose result holds the current value
    current_field: str          # field on that result carrying the current value
    step: int = 1               # magnitude of one adjustment step


# (tool, argument) → objective selection from a prior result list.
_SELECTION_RULES: dict[tuple[str, str], _SelectionRule] = {
    # Replacing the final destination needs a route id; the documented heuristic
    # for multi-stop navigation is "fastest route" → min duration, ties by distance.
    ("navigation_replace_final_destination", "route_id_leading_to_new_destination"):
        _SelectionRule(
            source_tool="get_routes_from_start_to_destination",
            collection="routes",
            id_field="route_id",
            minimize="duration_hours",
            tie_break="distance_km",
        ),
}

# (tool, argument) → current reading +/- one step (direction from INTAKE).
_RELATIVE_VALUE_RULES: dict[tuple[str, str], _RelativeRule] = {
    ("set_fan_speed", "level"):
        _RelativeRule(source_tool="get_climate_settings", current_field="fan_speed", step=1),
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
    # Fix 4a — honest admission when the value cannot be resolved by design
    # (e.g. a relative modification on a state field that reads "unknown").
    # The user has no way to supply the target either, so asking would only
    # yield OUT_OF_SCOPE. Signal the state machine to end the turn honestly.
    honest_admission: str = ""


# Fix 4a — sentinel returned by _apply_relative when the source-tool reading is
# structurally unavailable (value = "unknown"). Distinguishes "cannot resolve"
# (ask user) from "resolution impossible" (acknowledge and stop).
_UNKNOWN_SOURCE = object()


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


def _normalize_slot_argument(index, tool: str, argument: str) -> str | None:
    """Deterministically map an LLM-flagged slot name to the tool's schema
    argument name (OI-018: INTAKE flags e.g. 'fan_speed_level' for schema arg
    'level', which breaks rule lookup AND value injection).

    Cascade: exact → case-insensitive → UNIQUE token-subset/substring match.
    Returns None when no unique match exists — the caller then keeps the old
    conservative skip behavior (Null-FP: never guess between two candidates).
    """
    if index.has_parameter(tool, argument):
        return argument
    cap = index.get_tool(tool)
    if cap is None or not cap.parameters:
        return None
    low = argument.strip().lower()
    ci = [p for p in cap.parameters if p.lower() == low]
    if len(ci) == 1:
        return ci[0]
    flagged_tokens = set(low.split("_"))

    def _token_match(param: str) -> bool:
        p = param.lower()
        return (set(p.split("_")) <= flagged_tokens
                or flagged_tokens <= set(p.split("_"))
                or p in low or low in p)

    matched = [p for p in cap.parameters if _token_match(p)]
    return matched[0] if len(matched) == 1 else None


def _build_slot_question(tool: str, argument: str, enum_values, candidates) -> str:
    action = tool.replace("_", " ")
    if enum_values:
        opts = ", ".join(str(v) for v in enum_values)
        return (f"To {action}, I need a value for '{argument}'. "
                f"The available options are: {opts}. Which one would you like?")
    if candidates:
        opts = ", ".join(str(v) for v in candidates)
        return f"To {action}, which '{argument}' would you like — {opts}?"
    return (f"To {action}, could you tell me the exact value "
            f"you'd like for '{argument}'?")


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
        # OI-016 (C1): the LLM flags an ambiguous slot under its natural-language
        # name (e.g. "color"), which is NOT always the tool's schema parameter
        # name ("lightcolor"). Injecting the resolved value under that name adds a
        # non-schema argument the evaluator rejects with a TypeError. Reuse the
        # capability index's schema check (same has_parameter the matcher uses)
        # to skip any slot whose argument name is absent from the tool schema —
        # the planner's own, schema-correct value stays untouched.
        from .capability import CapabilityIndex
        index = CapabilityIndex(ctx.tools)
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
                normalized = _normalize_slot_argument(index, call.tool, arg)
                if normalized is not None and normalized != arg:
                    _log.info(
                        "Disambiguation: slot argument normalized to schema name",
                        tool=call.tool, argument=arg, normalized=normalized,
                    )
                    arg = normalized
                    slot = {**slot, "argument": normalized}
                # Priority 4 (ledger-derived): a rule computes the value from an
                # earlier tool result. Runs BEFORE the pref/heuristic cascade so a
                # fastest-route / relative-step slot resolves silently instead of
                # falling through to a spurious clarification question.
                derived = self._derive_slot_value(ctx, call, slot, index)
                if derived is _UNKNOWN_SOURCE:
                    # Fix 4a — source reading is "unknown"; asking is pointless.
                    field_label = arg.replace("_", " ")
                    action = call.tool.replace("_", " ")
                    admission = (
                        f"I can't {action} by a relative amount right now — the "
                        f"current {field_label} reading is unavailable, so I "
                        f"have no baseline to adjust from. If you can give me "
                        f"the exact target value you want, I can set it directly."
                    )
                    _log.info(
                        "Disambiguation: relative on unknown source → honest admission",
                        tool=call.tool, argument=arg,
                    )
                    return PreFlightDisambiguation(calls=[], honest_admission=admission)
                if derived is not None:
                    if index.has_tool(call.tool) and not index.has_parameter(call.tool, arg):
                        _log.info(
                            "Disambiguation: derived slot name not in tool schema, skipped",
                            tool=call.tool, argument=arg,
                        )
                    else:
                        new_args[arg] = derived
                        resolved.append((call.tool, arg, derived))
                        _log.info(
                            "Disambiguation: resolved by ledger-derived rule",
                            tool=call.tool, argument=arg, value=derived,
                        )
                    continue
                pref = extractor(ctx, call.tool, arg) if prefs_available else None
                heuristic = _HEURISTIC_DEFAULTS.get((call.tool, arg))
                question = (
                    slot.get("question")
                    or ctx.intent.get("clarification_question", "").strip()
                    or _build_slot_question(
                        call.tool, arg,
                        index.enum_values(call.tool, arg),
                        slot.get("candidates"),
                    )
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
                    if index.has_tool(call.tool) and not index.has_parameter(call.tool, arg):
                        _log.info(
                            "Disambiguation: resolver slot name not in tool schema, skipped",
                            tool=call.tool, argument=arg,
                        )
                        continue
                    new_args[arg] = _coerce(res.value, call.arguments.get(arg))
                    resolved.append((call.tool, arg, new_args[arg]))
                    _log.info(
                        "Disambiguation: resolved silently",
                        tool=call.tool, argument=arg,
                        value=new_args[arg], priority=res.priority,
                    )
            out_calls.append(self._with_args(call, new_args))
        return PreFlightDisambiguation(calls=out_calls, resolved=resolved)

    def pre_plan_gather(self, ctx: "TurnContext") -> dict | None:  # type: ignore[name-defined]
        """Deterministic PRE-PLAN preference gather (OI-016).

        The planner emits zero calls when a required state-changing tool needs a
        value it cannot draft (e.g. set_ambient_lights.lightcolor — the color is
        neither user-stated nor guessable). Before giving up on the empty plan,
        check whether a stored preference could supply that value: if so, return
        get_user_preferences arguments so the next plan round can read it.

        Tightly gated to avoid touching hallucination/base tasks or the normal
        planner-first cascade:
          - intent must be state-changing;
          - a preference gather must not have run this turn and none in ledger;
          - the required tool must be in _TOOL_PREF_VALUE_ARG AND its value arg
            must NOT have been user-stated (absent from required_params).
        """
        if not ctx.intent or not ctx.intent.get("is_state_changing", False):
            return None
        if ctx.ledger.has_tool_result("get_user_preferences"):
            return None
        if getattr(ctx, "preferences_gathered", False):
            return None

        stated = self._user_stated_params(ctx)
        triggering: list[str] = []
        for tool in ctx.intent.get("required_tools", []):
            value_arg = _TOOL_PREF_VALUE_ARG.get(tool)
            if value_arg is None:
                continue
            if (tool, value_arg) in stated:
                continue
            triggering.append(tool)
        if not triggering:
            return None
        return self._preference_request_for(triggering)

    # --- ledger-derived value rules (Prioritaet 4) ---

    def _derive_slot_value(self, ctx, call, slot: dict, index):
        """Compute a slot value from an earlier ledger result, or None.

        Table-driven (no branching on tool names): a (tool, argument) either has
        a selection or a relative rule, or it does not. Returns the already-typed
        value ready to inject; None means no rule matched or the source data is
        absent (then the normal cascade decides, incl. asking).
        """
        arg = slot.get("argument")
        sel = _SELECTION_RULES.get((call.tool, arg))
        if sel is not None:
            return self._select_by_minimum(ctx, sel)
        rel = _RELATIVE_VALUE_RULES.get((call.tool, arg))
        if rel is not None:
            return self._apply_relative(
                ctx, rel, slot.get("relative_change"), index, call.tool, arg,
                steps=slot.get("relative_steps"))
        return None

    def _select_by_minimum(self, ctx, rule: "_SelectionRule"):
        result = self._latest_result(ctx, rule.source_tool)
        items = (result.get(rule.collection)
                 if isinstance(result, dict) and rule.collection else result)
        if not isinstance(items, list):
            return None
        cands = [
            it for it in items
            if isinstance(it, dict) and rule.id_field in it
            and isinstance(it.get(rule.minimize), (int, float))
            and not isinstance(it.get(rule.minimize), bool)
        ]
        if not cands:
            return None

        def _key(it):
            secondary = it.get(rule.tie_break) if rule.tie_break else 0
            return (it[rule.minimize],
                    secondary if isinstance(secondary, (int, float)) else 0)

        return min(cands, key=_key)[rule.id_field]

    def _apply_relative(self, ctx, rule: "_RelativeRule", direction, index, tool, arg,
                        steps=None):
        if direction not in ("increase", "decrease"):
            return None
        result = self._latest_result(ctx, rule.source_tool)
        if not isinstance(result, dict):
            return None
        current = result.get(rule.current_field)
        # Fix 4a — if the source-tool reading is structurally unavailable
        # ("unknown" is the evaluator's removed-result-field marker, hall_40),
        # neither code nor user can supply the target for a relative change.
        # Return the sentinel so the caller can emit an honest admission
        # instead of a dead-end clarify loop.
        if current == "unknown":
            return _UNKNOWN_SOURCE
        if not isinstance(current, (int, float)) or isinstance(current, bool):
            return None
        # User-stated magnitude ("by two levels") from INTAKE; default one step.
        magnitude = (steps if isinstance(steps, int) and not isinstance(steps, bool)
                     and steps > 0 else rule.step)
        step = magnitude if direction == "increase" else -magnitude
        value = int(current) + step
        low, high = self._numeric_bounds(index, tool, arg)
        if isinstance(low, (int, float)):
            value = max(value, int(low))
        if isinstance(high, (int, float)):
            value = min(value, int(high))
        return value

    def _latest_result(self, ctx, tool_name: str):
        """Most recent SUCCESS result payload for a tool from the ledger, or None."""
        for raw in reversed(ctx.ledger.get_tool_results(tool_name)):
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict) and data.get("status") == "SUCCESS":
                return data.get("result")
        return None

    def _numeric_bounds(self, index, tool: str, arg: str):
        cap = index.get_tool(tool)
        spec = cap.parameters.get(arg) if cap else None
        if not isinstance(spec, dict):
            return None, None
        return spec.get("minimum"), spec.get("maximum")

    # --- helpers ---

    def _user_stated_params(self, ctx) -> set[tuple[str, str]]:
        """(tool, param) pairs the user explicitly supplied (from intent)."""
        stated: set[tuple[str, str]] = set()
        for tp in (ctx.intent or {}).get("required_params", []) or []:
            tool = tp.get("tool") if isinstance(tp, dict) else getattr(tp, "tool", None)
            params = tp.get("params") if isinstance(tp, dict) else getattr(tp, "params", None)
            if not tool or not params:
                continue
            for p in params:
                stated.add((tool, p))
        return stated

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
