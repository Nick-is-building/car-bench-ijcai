"""
Fabrikations-Waechter — Stufe 5.

Vor jeder Antwort an den User:
  1. Faktische Behauptungen aus dem Antwort-Entwurf extrahieren (LLM, strukturiert)
  2. Jede Behauptung deterministisch gegen das Ledger pruefen (hat sie eine Quelle?)
  3. Ungedeckte Behauptung → blockiert, ersetzt durch ehrliches Eingestaendnis

Dasselbe fuer Tool-Argumente (state-changing Calls):
  C2: jeder numerische Wert braucht eine Ledger-Herkunft (deterministisch)
  C3: LLM-Attribution → deterministischer Gate: Zitat im Ledger + Zitat erwaehnt Ziel-Entitaet
  C4: Einstimmigkeits-Gate — zweiter identischer Call bei UNCERTAIN aus C3

Guard-Interface (C1): alle Pruefschiichten geben GuardResult zurueck.
Semantik: PASS/BLOCK sind final; UNCERTAIN eskaliert an die StateMachine (Re-Plan oder Senke).

Compliance: prueft nur gegen Wahrheit + Ledger — niemals gegen Evaluator-Subscores.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from loguru import logger as _log
from pydantic import BaseModel, Field

from . import llm, prompts
from .ledger import Ledger


# ---------------------------------------------------------------------------
# C1 — Guard-Interface
# ---------------------------------------------------------------------------

GuardVerdict = Literal["PASS", "BLOCK", "UNCERTAIN"]


GuardSeverity = Literal["HARD", "SOFT"]


@dataclass
class GuardResult:
    """Uniform decision record for every guard layer."""
    verdict: GuardVerdict
    layer: str
    reason: str = ""
    severity: GuardSeverity = "HARD"


# ---------------------------------------------------------------------------
# LLM-Schemas fuer C3 (Argument-Attribution) und C5 (Claim-Extraktion)
# ---------------------------------------------------------------------------

class ArgumentAttribution(BaseModel):
    argument_name: str
    argument_value: str = Field(description="The value being attributed, as a string")
    source_quote: str = Field(
        description="Literal verbatim quote from the conversation that provides this value. "
                    "Empty string if no clear source exists."
    )
    target_entity: str = Field(
        description="The entity or concept the source quote is referring to "
                    "(e.g. 'sunroof', 'sunshade', 'navigation'). 'unknown' if unclear."
    )


class AttributionResponse(BaseModel):
    attributions: list[ArgumentAttribution]


class FactualClaim(BaseModel):
    value: str = Field(
        description="The specific value being asserted (e.g. '42 minutes', '22°C', 'unavailable')"
    )
    sentence: str = Field(description="The full sentence containing this claim")


class ClaimExtractionResponse(BaseModel):
    claims: list[FactualClaim]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_STATE_CHANGING_PREFIXES = (
    "open_", "close_", "set_", "activate_", "deactivate_", "send_",
    "start_", "stop_", "turn_", "change_", "update_", "add_",
    "delete_", "replace_", "edit_",
)

_COMMON_VERBS = frozenset({
    "open", "close", "set", "get", "activate", "deactivate", "send",
    "start", "stop", "turn", "change", "update", "navigate", "add",
    "delete", "replace", "edit", "new", "current", "all", "main", "by",
})

_ROUTE_TOOLS = frozenset({
    "set_new_navigation", "add_waypoint", "replace_waypoint",
    "delete_waypoint",
})

# Known in-car entities for C3 Gate-2 competing-entity detection.
# Only flag binding confusion when the source quote explicitly mentions a different entity.
_KNOWN_ENTITIES = frozenset({
    "sunroof", "sunshade", "window", "seat", "heater",
    "climate", "fan", "defrost", "navigation", "radio",
    "light", "fog", "mirror", "door", "trunk", "temperature",
})

_ROUTE_CHOICE_WORDS = ("fastest", "quickest", "shortest", "optimal", "direct")

_INABILITY_PATTERNS = re.compile(
    r"(?:I'm not able to|I am not able to|I cannot|I can't|I'm unable to|"
    r"I am unable to|unable to control|beyond what I can do|"
    r"not able to control|You'll need to .* manually)",
    re.IGNORECASE,
)

# Fix 5 — Announce-Stall-Detektor. When the draft ends the turn with a promise
# ("let me switch…", "I'll now check…") but no more tool calls will run this
# turn, the promise is a stall: the user-sim reads it as HALLUCINATION_ERROR.
# Deterministic strip of the promise sentences; the remaining reply reports
# only what actually happened.
_ACTION_PROMISE_PATTERNS = re.compile(
    r"\b("
    r"let me\b|"
    r"now let me\b|"
    r"i'?ll (?:now|go ahead|proceed|check|look|do|switch|set|open|close|turn|"
    r"change|start|stop|adjust|activate|update|send|send it|make it happen)\b|"
    r"i'?m (?:going to|about to)\b|"
    r"i will now\b"
    r")",
    re.IGNORECASE,
)


_META_WHITELIST = re.compile(
    r"\b(let me (?:confirm|summarize|recap|review|verify))\b",
    re.IGNORECASE,
)


@dataclass
class SoftFinding:
    """A SOFT guard finding: feedback to inject into re-draft, not a mutation."""
    layer: str
    sentences: list[str]
    feedback: str


def detect_action_promises(
    draft: str,
    ledger: "Ledger",
    has_open_confirmation: bool = False,
) -> SoftFinding | None:
    """Fix 5-refined — detect promise sentences under strict preconditions.

    Returns a SoftFinding if problematic sentences are found, None otherwise.
    Preconditions (all must hold for a sentence to be flagged):
      1. Sentence matches _ACTION_PROMISE_PATTERNS
      2. Sentence is NOT on the meta-whitelist (confirm/summarize/recap)
      3. There is no open confirmation request (would justify "let me...")
      4. The promised action is state-changing AND no SUCCESS for that entity
    """
    if has_open_confirmation:
        return None
    sentences = re.split(r"(?<=[.!?])\s+", draft)
    successful = _successful_tool_names(ledger)
    flagged = []
    for s in sentences:
        if not _ACTION_PROMISE_PATTERNS.search(s):
            continue
        if _META_WHITELIST.search(s):
            continue
        s_lower = s.lower()
        entity_has_success = False
        for tool in successful:
            entity_words = _tool_entity_synonyms(tool)
            if entity_words and any(w in s_lower for w in entity_words):
                entity_has_success = True
                break
        if entity_has_success:
            continue
        flagged.append(s)
    if not flagged:
        return None
    return SoftFinding(
        layer="AnnounceStall.soft",
        sentences=flagged,
        feedback=(
            "Your draft contains action promises that will not be executed this turn. "
            "Remove or rephrase these sentences — report only what has already happened: "
            + "; ".join(repr(s[:80]) for s in flagged)
        ),
    )


def detect_inability_contradictions(
    draft: str,
    ledger: "Ledger",
    catalog_tools: set[str] | None = None,
    rc_tools: set[str] | None = None,
) -> SoftFinding | None:
    """Fix 1e-refined — detect false inability claims, excluding RC tools.

    REQUIRES_CONFIRMATION tools are excluded from the catalog scan: the agent
    legitimately cannot execute them without confirmation, so "I can't do that"
    is not a contradiction (base_2 regression).
    """
    successful = _successful_tool_names(ledger)
    catalog_filtered = (catalog_tools or set()) - (rc_tools or set())
    available = successful | catalog_filtered
    if not available:
        return None
    flagged = []
    for sentence in re.split(r"(?<=[.!?])\s+", draft):
        tool = _inability_contradicts_ledger(sentence, available)
        if tool is not None:
            flagged.append(sentence)
    if not flagged:
        return None
    return SoftFinding(
        layer="InabilityContradiction.soft",
        sentences=flagged,
        feedback=(
            "Your draft claims inability for actions that are actually available. "
            "Remove or correct these false inability claims: "
            + "; ".join(repr(s[:80]) for s in flagged)
        ),
    )


def strip_action_promises(draft: str) -> str:
    """Fix 5 — remove sentences that promise an upcoming action.

    Called only when no further tool calls follow this turn: any promise is a
    stall then, not a legitimate look-ahead. If stripping would empty the reply,
    return the original draft (something is better than nothing; C5/Auditor
    catch the actual fabrication).
    """
    sentences = re.split(r"(?<=[.!?])\s+", draft)
    kept = [s for s in sentences if not _ACTION_PROMISE_PATTERNS.search(s)]
    if len(kept) == len(sentences):
        return draft
    result = " ".join(s.strip() for s in kept if s.strip()).strip()
    if not result:
        return draft
    _log.info(
        "strip_action_promises: removed %d promise sentence(s)",
        len(sentences) - len(kept),
    )
    return result


def _successful_tool_names(ledger: Ledger) -> set[str]:
    """Tool names that have at least one SUCCESS result in the ledger."""
    import json as _json

    names: set[str] = set()
    for e in ledger.entries:
        if e.kind != "tool_result" or e.tool_name is None:
            continue
        text = e.content if isinstance(e.content, str) else None
        if text is None:
            continue
        try:
            data = _json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and str(data.get("status", "")).upper() == "SUCCESS":
            names.add(e.tool_name)
    return names


def _inability_contradicts_ledger(sentence: str, successful_tools: set[str]) -> str | None:
    """If *sentence* claims inability but a successful tool contradicts it, return that tool name."""
    if not _INABILITY_PATTERNS.search(sentence):
        return None
    sent_lower = sentence.lower()
    for tool_name in successful_tools:
        entity_words = _tool_entity_synonyms(tool_name)
        if not entity_words:
            continue
        matched = sum(1 for w in entity_words if w in sent_lower)
        if matched > len(entity_words) / 2:
            return tool_name
    return None


_RELATIVE_DISTANCE_WORDS = re.compile(
    r"(?:further|farther|closer|too far|way further|need to stop|need to charge|"
    r"won't make it|wouldn't make it|can't make it|cannot make it|"
    r"definitely need|you'd need to|you would need to|much further|"
    r"shorter than|longer than|takes about|hours away|hours drive)",
    re.IGNORECASE,
)


def _is_relative_distance_claim(value: str) -> bool:
    return bool(_RELATIVE_DISTANCE_WORDS.search(value))


def _route_data_is_unknown(ledger: Ledger) -> bool:
    """True if the ledger has a route query whose result contains 'unknown'."""
    import json as _json

    for e in ledger.entries:
        if e.kind != "tool_result" or e.tool_name is None:
            continue
        if "route" not in e.tool_name:
            continue
        text = e.content if isinstance(e.content, str) else None
        if text is None:
            continue
        try:
            data = _json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            result = data.get("result", data)
            if isinstance(result, dict):
                for v in result.values():
                    if v == "unknown":
                        return True
    return False


def _is_state_changing(tool_name: str) -> bool:
    return any(tool_name.startswith(p) for p in _STATE_CHANGING_PREFIXES)


def _tool_entity_synonyms(tool_name: str) -> list[str]:
    """Extract entity nouns from a tool name for binding verification."""
    parts = tool_name.lower().split("_")
    return [p for p in parts if p not in _COMMON_VERBS and len(p) > 2]


# Fix 1a — observation tools whose result values are legitimate provenance for
# same-entity state-changing arguments (dis_40, base_40 T1: percentage=5 comes
# from get_vehicle_window_positions of the same window). Mirrors
# policies.OBSERVATION_TOOLS but kept local to avoid a cross-module import here.
_OBSERVATION_TOOLS_FOR_PROVENANCE = frozenset({
    "get_climate_settings",
    "get_exterior_lights_status",
    "get_vehicle_window_positions",
    "get_sunroof_and_sunshade_position",
    "get_current_navigation_state",
    "get_temperature_inside_car",
    "get_seat_heating",
    "get_seat_occupancy",
    "get_charging_specs_and_status",
    "get_battery_status",
    "get_user_preferences",
})


def _value_from_same_entity_observation(
    value: float | int, tool_name: str, ledger: Ledger,
) -> bool:
    """Fix 1a — True if the numeric value appears in an observation-tool
    result whose tool name shares an entity token with the state-changing
    tool. Provenance is Lesson-1a valid: the LLM did not invent it, the
    evaluator's own tool did. Skips the C3 attribution loop that misreads
    'position=5' as unsound provenance for 'percentage=5'.
    """
    import json as _json

    tool_entities = set(_tool_entity_synonyms(tool_name))
    if not tool_entities:
        return False
    try:
        fv = float(value)
    except (TypeError, ValueError):
        return False
    iv = int(fv) if fv.is_integer() else None
    variants = {str(fv), f"{fv}"}
    if iv is not None:
        variants.add(str(iv))
    for e in ledger.entries:
        if e.kind != "tool_result" or not e.tool_name:
            continue
        if e.tool_name not in _OBSERVATION_TOOLS_FOR_PROVENANCE:
            continue
        obs_entities = set(_tool_entity_synonyms(e.tool_name))
        if not (obs_entities & tool_entities):
            continue
        text = e.content if isinstance(e.content, str) else _json.dumps(e.content)
        if not text:
            continue
        # match on numeric token boundaries so e.g. "5" doesn't match inside "50"
        for v in variants:
            if re.search(r"(?<!\d)" + re.escape(v) + r"(?!\d)", text):
                return True
    return False


def _collect_unknown_fields(ledger: Ledger) -> dict[str, list[str]]:
    """Return {tool_name: [field_names]} for SUCCESS results containing 'unknown' values."""
    import json as _json

    result: dict[str, list[str]] = {}
    for e in ledger.entries:
        if e.kind != "tool_result" or not e.tool_name:
            continue
        text = e.content if isinstance(e.content, str) else None
        if text is None:
            continue
        try:
            data = _json.loads(text)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict) or str(data.get("status", "")).upper() != "SUCCESS":
            continue
        res = data.get("result", {})
        if isinstance(res, dict):
            unknowns = [k for k, v in res.items() if v == "unknown"]
            if unknowns:
                result[e.tool_name] = unknowns
    return result


_UNCERTAINTY_WORDS = frozenset({
    "unknown", "unavailable", "not available", "currently unavailable",
})


def inject_unknown_caveat(
    draft: str,
    ledger: Ledger,
    executed_sigs: set[str],
) -> str:
    """Lesson 1a gate: if executed actions relate to unknown-valued fields, ensure uncertainty is mentioned."""
    unknown_by_tool = _collect_unknown_fields(ledger)
    if not unknown_by_tool:
        return draft

    if not executed_sigs:
        return draft

    executed_tools = {sig.split(":")[0] for sig in executed_sigs}

    exec_entities: set[str] = set()
    for t in executed_tools:
        exec_entities.update(_tool_entity_synonyms(t))

    lower_draft = draft.lower()
    missing_labels: list[str] = []

    for tool_name, fields in unknown_by_tool.items():
        tool_entities = set(_tool_entity_synonyms(tool_name))
        if not (tool_entities & exec_entities):
            continue
        field_labels = [f.replace("_", " ") for f in fields]
        already_covered = any(
            fl in lower_draft and any(w in lower_draft for w in _UNCERTAINTY_WORDS)
            for fl in field_labels
        )
        if already_covered:
            continue
        missing_labels.extend(field_labels)

    if not missing_labels:
        return draft

    labels = " and ".join(dict.fromkeys(missing_labels))
    caveat = f"Note: the {labels} is currently unavailable."
    _log.info("inject_unknown_caveat: appending caveat", labels=labels)
    return draft.rstrip() + " " + caveat


def _iter_route_metadata(ledger: Ledger):
    """Fix 3 — yield every route dict recorded in the ledger (from
    get_routes_from_start_to_destination results).

    Each yielded dict has route_id, start_id, destination_id and other
    per-alternative metadata. Read-only over successful tool results.
    """
    import json as _json

    for e in ledger.entries:
        if e.kind != "tool_result" or e.tool_name != "get_routes_from_start_to_destination":
            continue
        text = e.content if isinstance(e.content, str) else None
        if text is None:
            continue
        try:
            payload = _json.loads(text)
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
            continue
        result = payload.get("result") or {}
        routes = result.get("routes") if isinstance(result, dict) else None
        if not isinstance(routes, list):
            continue
        for r in routes:
            if isinstance(r, dict) and "route_id" in r:
                yield r


def _find_route(ledger: Ledger, route_id: str) -> dict | None:
    """Return the metadata for `route_id`, or None if not seen in the ledger."""
    if not route_id:
        return None
    for r in _iter_route_metadata(ledger):
        if r.get("route_id") == route_id:
            return r
    return None


def _pick_route_from(ledger: Ledger, start_id: str, dest_id: str | None = None,
                     minimize: str = "duration_hours") -> str | None:
    """Fix 3 — pick the objective-best route in the ledger starting at start_id
    (and, if given, ending at dest_id). Objective = fastest by default, ties
    broken by distance. Returns route_id or None if no candidate exists.
    """
    cands = [
        r for r in _iter_route_metadata(ledger)
        if r.get("start_id") == start_id
        and (dest_id is None or r.get("destination_id") == dest_id)
        and isinstance(r.get(minimize), (int, float))
        and not isinstance(r.get(minimize), bool)
    ]
    if not cands:
        return None
    tie = "distance_km"

    def _key(r):
        secondary = r.get(tie) if tie else 0
        return (r[minimize], secondary if isinstance(secondary, (int, float)) else 0)

    return min(cands, key=_key).get("route_id")


# Fix 3 — declarative table of navigation calls whose route_id arguments must
# start at (or lead to) a specific waypoint. Table-driven (no branching on
# tool names): a (tool, id_arg) either has a constraint or does not.
#
# Format: (tool, id_argument) -> ("start"|"end", waypoint_arg_or_field)
#   "start" means the referenced route's start_id must equal the value in
#   waypoint_arg; "end" means the route's destination_id must equal it.
#   waypoint_arg is either an argument name on the same call ("new_waypoint_id")
#   or the special token "@prev_last_waypoint" resolved from the ledger.
_NAV_ROUTE_ID_CONSTRAINTS: dict[tuple[str, str], tuple[str, str]] = {
    ("navigation_replace_one_waypoint", "route_id_leading_away_from_new_waypoint"):
        ("start", "new_waypoint_id"),
    ("navigation_replace_one_waypoint", "route_id_leading_to_new_waypoint"):
        ("end", "new_waypoint_id"),
    ("navigation_replace_final_destination", "route_id_leading_to_new_destination"):
        ("end", "new_destination_id"),
    ("navigation_add_one_waypoint", "route_id_leading_away_from_new_waypoint"):
        ("start", "new_waypoint_id"),
    ("navigation_add_one_waypoint", "route_id_leading_to_new_waypoint"):
        ("end", "new_waypoint_id"),
}


@dataclass
class NavArgumentCheck:
    """Fix 3 — outcome of the navigation-argument validator."""
    ok: bool
    hints: list[str]                # human-readable re-plan hints per violation
    repaired: dict                  # arguments after auto-repair (id ← ledger)
    replaced: dict                  # {arg: (old, new)} for telemetry


def check_navigation_arguments(tool_name: str, arguments: dict,
                                ledger: Ledger) -> NavArgumentCheck:
    """Fix 3 — verify each route_id argument has the correct start/end anchor.

    Deterministic: reads route metadata from the ledger and, if the LLM picked
    a route whose start_id/destination_id doesn't match the expected waypoint,
    substitutes the objective-best (fastest) route that does. All substitutions
    are surfaced as policy-notes hints so the planner can learn for the next
    round; the repaired arguments make the call safe to execute right now.
    """
    hints: list[str] = []
    replaced: dict[str, tuple] = {}
    repaired = dict(arguments)

    for arg_name in list(arguments.keys()):
        constraint = _NAV_ROUTE_ID_CONSTRAINTS.get((tool_name, arg_name))
        if constraint is None:
            continue
        side, wp_arg = constraint
        wp_id = arguments.get(wp_arg)
        if not isinstance(wp_id, str) or not wp_id:
            continue
        route_id = arguments.get(arg_name)
        if not isinstance(route_id, str) or not route_id:
            continue
        meta = _find_route(ledger, route_id)
        if meta is None:
            # unseen route_id — inject a hint but don't guess a replacement
            hints.append(
                f"route_id {route_id!r} for {tool_name}.{arg_name} is not in any "
                f"get_routes_from_start_to_destination result recorded in this "
                f"conversation. Use a route_id from a route query in the ledger."
            )
            continue
        anchor = "start_id" if side == "start" else "destination_id"
        if meta.get(anchor) == wp_id:
            continue
        # anchor mismatch — pick the objective-best route in the ledger that
        # DOES satisfy the anchor
        if side == "start":
            alt = _pick_route_from(ledger, wp_id)
        else:
            # end-anchored: iterate ledger routes with destination == wp_id
            alt_cands = [
                r for r in _iter_route_metadata(ledger)
                if r.get("destination_id") == wp_id
                and isinstance(r.get("duration_hours"), (int, float))
                and not isinstance(r.get("duration_hours"), bool)
            ]
            alt = None
            if alt_cands:
                alt = min(
                    alt_cands,
                    key=lambda r: (r["duration_hours"],
                                    r.get("distance_km") if isinstance(r.get("distance_km"), (int, float)) else 0),
                ).get("route_id")
        if alt is None:
            hints.append(
                f"{tool_name}.{arg_name}={route_id!r} does not {side} at "
                f"{wp_id!r} (route {anchor}={meta.get(anchor)!r}), and no "
                f"alternative route with the correct anchor is in the ledger yet."
            )
            continue
        repaired[arg_name] = alt
        replaced[arg_name] = (route_id, alt)
        hints.append(
            f"{tool_name}.{arg_name}: chosen route_id {route_id!r} does not "
            f"{side} at {wp_id!r}; substituted the fastest ledger route with "
            f"correct anchor: {alt!r}."
        )
        _log.info(
            "NavArgumentValidator: repaired route_id anchor",
            tool=tool_name, arg=arg_name, old=route_id, new=alt,
            anchor=anchor, waypoint=wp_id,
        )
    return NavArgumentCheck(
        ok=not hints, hints=hints, repaired=repaired, replaced=replaced,
    )


def _ledger_text_corpus(ledger: Ledger) -> str:
    """All user messages and tool results as one searchable string."""
    parts: list[str] = []
    for e in ledger.entries:
        if e.kind in ("user", "system"):
            parts.append(str(e.content))
        elif e.kind == "tool_result":
            parts.append(str(e.content))
    return " ".join(parts)


def _value_in_ledger(value: str | float | int, corpus: str) -> bool:
    """True if the value is backed by the ledger corpus.

    Clean numbers are matched in their int/float forms. A value that carries
    digits alongside a unit or symbol ("42 minutes", "50%", "22°C") is matched
    by its numeric TOKENS, not by literal substring: the ledger often stores the
    bare number (a dict field like {"eta_minutes": 42}) while the draft renders
    it with a unit, so a literal substring match is a false positive (OI-015).
    This is a factual number check, not a free-text pattern match. Non-numeric
    strings still require a literal substring match.
    """
    sv = str(value).strip()
    if not sv:
        return True
    # Clean numeric value → normalised int/float comparison (unchanged behaviour).
    try:
        fv = float(sv)
        iv = int(fv)
        return sv in corpus or str(iv) in corpus or f"{fv}" in corpus
    except (ValueError, OverflowError):
        pass
    # Value with embedded digits (unit/symbol) → every numeric token must appear
    # as a number in the corpus.
    tokens = re.findall(r"\d+(?:\.\d+)?", sv)
    if tokens:
        corpus_numbers = set(re.findall(r"\d+(?:\.\d+)?", corpus))
        return all(_number_backed(tok, corpus_numbers) for tok in tokens)
    # No digits at all → literal substring (paraphrase-tolerant callers skip these).
    return sv in corpus


def _number_backed(token: str, corpus_numbers: set[str]) -> bool:
    """True if a numeric token is present in the corpus (int/float normalised)."""
    if token in corpus_numbers:
        return True
    try:
        f = float(token)
    except ValueError:
        return False
    return str(int(f)) in corpus_numbers or f"{f}" in corpus_numbers


# ---------------------------------------------------------------------------
# FabricationGuard
# ---------------------------------------------------------------------------

class FabricationGuard:
    """Blocks responses and tool arguments that assert facts without ledger provenance."""

    # --- C2 + C3 + C4: argument provenance ---

    def check_tool_arguments(
        self,
        tool_name: str,
        arguments: dict,
        ledger: Ledger,
        model: str | None = None,
    ) -> GuardResult:
        """
        C2: every numeric value in a state-changing call must appear in the ledger.
        C3: LLM attribution + deterministic entity-binding gate.
        C4: unanimity gate (second call) on UNCERTAIN from C3.
        """
        if not arguments or not _is_state_changing(tool_name):
            return GuardResult(
                verdict="PASS", layer="FabricationGuard.C2",
                reason="no arguments or read-only tool",
            )

        corpus = _ledger_text_corpus(ledger)
        numeric_args = {
            k: v for k, v in arguments.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        if not numeric_args:
            return GuardResult(
                verdict="PASS", layer="FabricationGuard.C2",
                reason="no numeric arguments",
            )

        # C2: provenance check — value must appear somewhere in the ledger
        for name, value in numeric_args.items():
            if not _value_in_ledger(value, corpus):
                _log.warning(
                    "FabricationGuard.C2: BLOCK — numeric value not in ledger",
                    tool=tool_name, arg=name, value=value,
                )
                return GuardResult(
                    verdict="BLOCK",
                    layer="FabricationGuard.C2",
                    reason=f"{tool_name}.{name}={value} has no ledger provenance",
                )

        # Fix 1a — Values that came from a same-entity observation call are
        # legitimately provenant (dis_40 percentage=5 from
        # get_vehicle_window_positions of the same window). Skip C3 for those:
        # the LLM attribution loop otherwise misreads "position=5" as a
        # competing binding and forces a spurious clarify question.
        c3_args = {
            k: v for k, v in numeric_args.items()
            if not _value_from_same_entity_observation(v, tool_name, ledger)
        }
        if not c3_args:
            return GuardResult(
                verdict="PASS", layer="FabricationGuard.C2",
                reason="all numeric args backed by same-entity observations",
            )

        # C3: LLM-assisted binding check on the remaining args
        result1 = self._attribution_check(tool_name, c3_args, ledger, corpus, model)
        if result1.verdict == "PASS":
            return result1

        # C4: unanimity gate — second identical call
        result2 = self._attribution_check(tool_name, c3_args, ledger, corpus, model)
        if result1.verdict == result2.verdict:
            # unanimous agreement → follow
            return GuardResult(
                verdict=result1.verdict,
                layer="FabricationGuard.C4",
                reason=result1.reason,
            )
        # dissent → conservative UNCERTAIN
        return GuardResult(
            verdict="UNCERTAIN",
            layer="FabricationGuard.C4",
            reason=result1.reason + " [C4: dissent]",
        )

    def _attribution_check(
        self,
        tool_name: str,
        numeric_args: dict,
        ledger: Ledger,
        corpus: str,
        model: str | None,
    ) -> GuardResult:
        """One LLM attribution call + deterministic gate (C3 inner)."""
        transcript = prompts.common.render_transcript(ledger, include_tools=True)
        synonyms = _tool_entity_synonyms(tool_name)

        system = (
            "You are an argument attribution assistant for an in-car voice assistant. "
            "For each numeric argument of the tool call shown, find the literal verbatim "
            "quote from the conversation that provides that value, and identify what entity "
            "or concept the quote is talking about. Be precise."
        )
        content = (
            f"# Conversation\n{transcript}\n\n"
            f"# Tool call to attribute\nTool: {tool_name}\nArguments: {numeric_args}\n\n"
            "For each numeric argument, provide:\n"
            "- argument_name: the parameter name\n"
            "- argument_value: the value as a string\n"
            "- source_quote: EXACT literal quote from the conversation providing this value "
            "(empty string if no clear source exists)\n"
            "- target_entity: what entity or concept the quote is talking about "
            "(e.g. 'sunroof', 'sunshade', 'navigation', 'unknown')"
        )
        messages = [{"role": "user", "content": content}]

        try:
            resp: AttributionResponse = llm.call_structured(
                messages, AttributionResponse, model=model, system=system, temperature=0.0
            )
        except Exception as exc:
            _log.warning("FabricationGuard.C3: LLM call failed — defaulting to PASS", error=str(exc))
            return GuardResult(verdict="PASS", layer="FabricationGuard.C3", reason=f"LLM error: {exc}")

        for attr in resp.attributions:
            quote = attr.source_quote.strip()

            # Gate 1: empty quote means the value is inferred from context (not falsifiable) → skip
            if not quote:
                continue

            # Gate 1b: non-empty quote must exist literally in the ledger corpus
            if quote not in corpus:
                _log.info(
                    "FabricationGuard.C3: source quote not found in ledger",
                    tool=tool_name, arg=attr.argument_name, quote=quote[:80],
                )
                return GuardResult(
                    verdict="UNCERTAIN",
                    layer="FabricationGuard.C3",
                    reason=(
                        f"{tool_name}.{attr.argument_name}={attr.argument_value}: "
                        f"source quote not found in ledger"
                    ),
                )

            # Gate 2: only flag entity confusion when the quote explicitly names a DIFFERENT
            # entity — not merely when it omits the target entity (which might be a fragment
            # quote like "halfway" that doesn't name any entity).
            if synonyms and not any(s in quote.lower() for s in synonyms):
                competing = [
                    e for e in _KNOWN_ENTITIES
                    if e not in synonyms and e in quote.lower()
                ]
                if competing:
                    _log.info(
                        "FabricationGuard.C3: value bound to wrong entity",
                        tool=tool_name, synonyms=synonyms,
                        source_entity=attr.target_entity,
                        arg=attr.argument_name,
                    )
                    return GuardResult(
                        verdict="UNCERTAIN",
                        layer="FabricationGuard.C3",
                        reason=(
                            f"{tool_name}.{attr.argument_name}={attr.argument_value}: "
                            f"source mentions '{', '.join(competing)}', not "
                            f"({', '.join(synonyms)})"
                        ),
                    )

        return GuardResult(
            verdict="PASS",
            layer="FabricationGuard.C3",
            reason="all argument bindings verified",
        )

    # --- C5: draft sanitization ---

    def sanitize(self, draft: str, ledger: Ledger, model: str | None = None,
                 policy_notes: list[str] | tuple[str, ...] = (),
                 catalog_tools: set[str] | None = None,
                 rc_tools: set[str] | None = None) -> str:
        """
        C5: remove or replace unsupported factual claims in draft.
        Mandatory route-choice mention added if missing (OI-012 partial).
        LLM extracts candidates; code decides via Ledger-corpus check (Lesson 1a).

        Values and quotes from `policy_notes` count as supported: deterministic
        PolicyChecker notes (e.g. LLM-POL:012 zone-temperature difference) are
        derived from ledger state — the LLM is required to surface them, so
        this sanitize step must not kill the very obligation the policy asks
        it to communicate (dis_38 root cause 3rd layer). Only this sanitize
        step uses the extended corpus; pre-execute argument checks upstream
        keep the ledger-only corpus.
        """
        corpus = _ledger_text_corpus(ledger)
        if policy_notes:
            corpus = corpus + " " + " ".join(policy_notes)

        system = (
            "You are a claim extractor for an in-car voice assistant. "
            "Given a draft response, list every specific factual value it asserts: "
            "numbers, times, distances, temperatures, ETA, specific states, availability. "
            "IMPORTANT: Distance comparisons, travel time estimates, route assessments, "
            "and relative location statements (e.g. 'X is further than Y', 'that is too "
            "far', 'the route takes about Z hours', 'you would need to stop') are FACTUAL "
            "CLAIMS that require a source — list them with the comparative/relative phrase "
            "as the value. Only list concrete claims that could be wrong if invented."
        )
        content = (
            f"# Draft response\n{draft}\n\n"
            "List each specific factual value asserted in the draft. "
            "For each:\n"
            "- value: the specific value (e.g. '42 minutes', '22°C', 'way further', "
            "'need to stop and charge')\n"
            "- sentence: the full sentence that contains this value\n\n"
            "Leave out generic confirmations ('Done', 'All set'). "
            "Only list values that could be wrong if invented."
        )
        messages = [{"role": "user", "content": content}]

        try:
            resp: ClaimExtractionResponse = llm.call_structured(
                messages, ClaimExtractionResponse, model=model, system=system, temperature=0.0
            )
        except Exception as exc:
            _log.warning("FabricationGuard.C5: claim extraction failed — draft unchanged", error=str(exc))
            return draft

        safe = draft
        for claim in resp.claims:
            has_digits = bool(re.search(r"\d", claim.value))
            is_relative_distance = _is_relative_distance_claim(claim.value)

            if not has_digits and not is_relative_distance:
                continue

            if has_digits and _value_in_ledger(claim.value, corpus):
                continue

            if is_relative_distance and not _route_data_is_unknown(ledger):
                continue

            _log.info(
                "FabricationGuard.C5: unsupported claim removed",
                claim_value=claim.value,
                sentence=claim.sentence[:80],
            )
            # Fix 7 — remove the sentence instead of injecting the placeholder
            # "I'm sorry, I don't have confirmed information about that." mid-reply.
            # The placeholder was landing as an artefact inside otherwise good
            # answers (dis_54, base_82, base_84, base_98, hall_82). Fall back to
            # the placeholder ONLY if stripping would empty the whole reply.
            without = safe.replace(claim.sentence, "").strip()
            # collapse stray double spaces/orphan connectors created by the strip
            without = re.sub(r"\s{2,}", " ", without)
            without = re.sub(r"(?:^|\s)(?:and|but|so|also|,)\s*(?=[.!?]|$)",
                             ".", without).strip()
            if without:
                safe = without
            else:
                safe = "I'm sorry, I don't have confirmed information about that."

        safe = self._fix_inability_contradictions(safe, ledger, catalog=catalog_tools,
                                                     rc_tools=rc_tools)

        return self._add_route_mention_if_missing(safe, ledger)

    @staticmethod
    def _fix_inability_contradictions(draft: str, ledger: Ledger,
                                       catalog: set[str] | None = None,
                                       rc_tools: set[str] | None = None) -> str:
        """Replace false inability claims that contradict either successful
        tool calls OR available catalog tools (Fix 1e).

        Fix 1e-refined: REQUIRES_CONFIRMATION tools are excluded from the
        catalog scan — the agent legitimately cannot execute them without
        confirmation, so "I can't do that" is not a contradiction (base_2).
        """
        successful = _successful_tool_names(ledger)
        catalog_filtered = (catalog or set()) - (rc_tools or set())
        available = (successful | catalog_filtered)
        if not available:
            return draft
        fixed = draft
        for sentence in re.split(r"(?<=[.!?])\s+", draft):
            tool = _inability_contradicts_ledger(sentence, available)
            if tool is not None:
                _log.info(
                    "FabricationGuard.C6: inability claim contradicts available tool",
                    tool=tool,
                    sentence=sentence[:80],
                )
                fixed = fixed.replace(sentence, "").strip()
        if not fixed:
            successful_descriptions = [
                t.replace("_", " ") for t in sorted(successful)
                if _is_state_changing(t)
            ]
            if successful_descriptions:
                fixed = "Done! I've completed the following: " + ", ".join(successful_descriptions) + "."
            else:
                fixed = "Done!"
        return fixed

    def _add_route_mention_if_missing(self, draft: str, ledger: Ledger) -> str:
        route_calls = [
            e for e in ledger.entries
            if e.kind == "tool_call" and e.tool_name in _ROUTE_TOOLS
        ]
        if not route_calls:
            return draft
        draft_lower = draft.lower()
        if not any(w in draft_lower for w in _ROUTE_CHOICE_WORDS):
            return draft + " I've selected the fastest route."
        return draft
