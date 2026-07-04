"""
Policy-Compiler — Stufe 4 (ADR-0004).

EINE deklarative Regel-Tabelle (RULES) mit generischen Regeltypen.
PolicyChecker.pre_flight() iteriert generisch ueber die Tabelle gegen
Ledger + geplanten Batch. Tool-Namen existieren NUR in den Daten
(Regel-Eintraege, Effekt-Tabelle, Parser-Whitelist), nie im Kontrollfluss.

Klassifikation der 19 Policies (A/B/C) und Regeltyp-Semantik: ADR-0004.

Compliance-Grenze: Pruefung nur gegen Wahrheit, Ledger-Herkunft und die
19 veroeffentlichten Policies — niemals gegen nachgebildete Evaluator-Subscores.

Null-FP-Disziplin: Ein unbekannter Zustand fuehrt nie zu Block oder Ablehnung,
hoechstens zur Injektion eines Beobachtungs-Calls (mit Schleifenschutz).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from .ledger import Ledger


PolicyKind = Literal["AUT", "LLM", "BOTH"]


@dataclass
class PolicyViolation:
    policy_id: str
    kind: PolicyKind
    reason: str


@dataclass
class Policy:
    policy_id: str
    kind: PolicyKind
    description: str


# All 19 policies from wiki.md, canonically ordered (metadata / documentation)
ALL_POLICIES: list[Policy] = [
    Policy("LLM-POL:002", "LLM",
           "Metric system: distance in km/m, temperature in °C, datetime in 24h format."),
    Policy("LLM-POL:004", "LLM",
           "REQUIRES_CONFIRMATION tools: list details and get explicit 'yes' before executing."),
    Policy("AUT-POL:005", "AUT",
           "Sunroof can only be opened if sunshade is already fully open or opening in parallel."),
    Policy("LLM-POL:007", "LLM",
           "Window open >25% with AC ON: prompt for confirmation and warn about energy inefficiency."),
    Policy("LLM-POL:008", "BOTH",
           "Weather-gated actions (sunroof, fog lights): require explicit 'yes' in adverse conditions."),
    Policy("AUT-POL:009", "AUT",
           "Weather must be checked manually before sunroof-open / fog-light activation."),
    Policy("AUT-POL:010", "AUT",
           "Window defrost front/all: fan speed >=2, airflow includes WINDSHIELD, AC on."),
    Policy("AUT-POL:011", "AUT",
           "AC set to ON: close windows open >20%, set fan speed 1 if currently 0."),
    Policy("LLM-POL:012", "LLM",
           "Single-zone temperature set with >3°C diff to other zones: inform user."),
    Policy("AUT-POL:013", "AUT",
           "Fog lights activation: low beams on if off, high beams off if on."),
    Policy("AUT-POL:014", "AUT",
           "High beam headlights cannot be activated if fog lights are already on."),
    Policy("AUT-POL:016", "AUT",
           "Route start must always be the current car location."),
    Policy("AUT-POL:017", "AUT",
           "Waypoint edit tools only usable when navigation system is active with a route."),
    Policy("AUT-POL:018", "AUT",
           "Active navigation: use edit tools (not set_new_navigation); edits never in parallel."),
    Policy("AUT-POL:019", "AUT",
           "Route needs start + destination; destination not deletable without intermediate stop."),
    Policy("LLM-POL:021", "LLM",
           "Route presented in detail with toll roads: must inform user about toll."),
    Policy("LLM-POL:022", "LLM",
           "Multi-stop route, no selection: take fastest per segment, inform, offer alternatives."),
    Policy("AUT-POL:023", "AUT",
           "Calendar entries only requestable for the current day."),
    Policy("AUT-POL:024", "AUT",
           "Weather only requestable for the current day at a specified time."),
]


# ---------------------------------------------------------------------------
# Task context — deterministic parse of CURRENT_LOCATION / DATETIME from the
# evaluator's system prompt (JSON literals; raw_decode handles nested braces).
# ---------------------------------------------------------------------------

@dataclass
class TaskContext:
    current_location: dict | None = None   # {"id","name","position"}
    now: dict | None = None                # {"year","month","day","hour","minute"}


def parse_task_context(ledger: Ledger) -> TaskContext:
    text = ""
    for e in ledger.entries:
        if e.kind == "system":
            text = str(e.content)
            break
    ctx = TaskContext()
    decoder = json.JSONDecoder()
    for attr, marker in (("current_location", "CURRENT_LOCATION"), ("now", "DATETIME")):
        m = re.search(re.escape(marker) + r"\s*=\s*", text)
        if m:
            try:
                obj, _ = decoder.raw_decode(text[m.end():])
                if isinstance(obj, dict):
                    setattr(ctx, attr, obj)
            except ValueError:
                pass
    return ctx


# ---------------------------------------------------------------------------
# Known vehicle/navigation state — derived ONLY from the ledger (ADR-0004).
# Absent key = unknown. Unknown never blocks (Null-FP-Disziplin).
# ---------------------------------------------------------------------------

_INC = "__increment__"
_DEC = "__decrement__"

_WINDOW_FIELDS = {
    "DRIVER": ("window_driver_position",),
    "PASSENGER": ("window_passenger_position",),
    "DRIVER_REAR": ("window_driver_rear_position",),
    "PASSENGER_REAR": ("window_passenger_rear_position",),
    "ALL": ("window_driver_position", "window_passenger_position",
            "window_driver_rear_position", "window_passenger_rear_position"),
}

# tool → (args → state updates). Data, not control flow.
TOOL_EFFECTS: dict[str, Callable[[dict], dict]] = {
    "set_fog_lights": lambda a: {"fog_lights": a.get("on")},
    "set_head_lights_low_beams": lambda a: {"head_lights_low_beams": a.get("on")},
    "set_head_lights_high_beams": lambda a: {"head_lights_high_beams": a.get("on")},
    "set_air_conditioning": lambda a: {"air_conditioning": a.get("on")},
    "set_fan_speed": lambda a: {"fan_speed": a.get("level")},
    "set_fan_airflow_direction": lambda a: {"fan_airflow_direction": a.get("direction")},
    "set_air_circulation": lambda a: {"air_circulation": a.get("mode")},
    "open_close_sunshade": lambda a: {"sunshade_position": a.get("percentage")},
    "open_close_sunroof": lambda a: {"sunroof_position": a.get("percentage")},
    "open_close_window": lambda a: {
        f: a.get("percentage") for f in _WINDOW_FIELDS.get(a.get("window", ""), ())
    },
    "set_window_defrost": lambda a: (
        {"window_front_defrost": a.get("on"), "window_rear_defrost": a.get("on")}
        if a.get("defrost_window") == "ALL" else
        {"window_front_defrost": a.get("on")} if a.get("defrost_window") == "FRONT" else
        {"window_rear_defrost": a.get("on")} if a.get("defrost_window") == "REAR" else {}
    ),
    "set_new_navigation": lambda a: {
        "navigation_active": True,
        "nav_waypoint_count": len(a.get("route_ids", [])) + 1 if a.get("route_ids") else None,
    },
    "delete_current_navigation": lambda a: {"navigation_active": False, "nav_waypoint_count": 0},
    "navigation_add_one_waypoint": lambda a: {"nav_waypoint_count": _INC},
    "navigation_delete_waypoint": lambda a: {"nav_waypoint_count": _DEC},
    "navigation_delete_destination": lambda a: {"nav_waypoint_count": _DEC},
}

# get-tools whose SUCCESS result payload is merged into the known state
OBSERVATION_TOOLS = frozenset({
    "get_climate_settings",
    "get_exterior_lights_status",
    "get_vehicle_window_positions",
    "get_sunroof_and_sunshade_position",
    "get_current_navigation_state",
})

# result field → derived state entry (for non-scalar payload fields)
_RESULT_DERIVED: dict[str, tuple[str, Callable[[Any], Any]]] = {
    "waypoints_id": ("nav_waypoint_count", len),
}


def _parse_result_payload(content: Any) -> dict | None:
    """Parse a tool result into {"status": ..., "result": {...}} — else None."""
    payload = content
    for _ in range(2):  # results arrive JSON-encoded, sometimes twice
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                return None
    return payload if isinstance(payload, dict) else None


def derive_known_state(ledger: Ledger) -> dict[str, Any]:
    """Fold ledger chronologically: observation results + effects of successful
    state-changing calls. Unparseable or FAILURE results leave state unknown."""
    state: dict[str, Any] = {}
    call_args: dict[str, tuple[str | None, dict]] = {}
    for e in ledger.entries:
        if e.kind == "tool_call" and e.tool_call_id:
            args = e.content if isinstance(e.content, dict) else {}
            call_args[e.tool_call_id] = (e.tool_name, args)
        elif e.kind == "tool_result":
            payload = _parse_result_payload(e.content)
            if payload is None or payload.get("status") != "SUCCESS":
                continue
            tool = e.tool_name
            if tool in OBSERVATION_TOOLS:
                result = payload.get("result")
                if isinstance(result, dict):
                    for k, v in result.items():
                        if isinstance(v, (str, int, float, bool)):
                            state[k] = v
                        derived = _RESULT_DERIVED.get(k)
                        if derived and isinstance(v, list):
                            state[derived[0]] = derived[1](v)
            effect = TOOL_EFFECTS.get(tool or "")
            if effect:
                _, args = call_args.get(e.tool_call_id or "", (tool, {}))
                _apply_effect(state, effect(args))
    return state


def _apply_effect(state: dict, updates: dict) -> None:
    for k, v in updates.items():
        if v == _INC:
            if isinstance(state.get(k), int):
                state[k] += 1
        elif v == _DEC:
            if isinstance(state.get(k), int):
                state[k] -= 1
        elif v is None:
            state.pop(k, None)
        else:
            state[k] = v


def _project(state: dict, calls: list) -> dict:
    """Known state plus the effects of the (not yet executed) batch calls."""
    projected = dict(state)
    for c in calls:
        effect = TOOL_EFFECTS.get(c.tool)
        if effect:
            _apply_effect(projected, effect(c.arguments))
    return projected


# ---------------------------------------------------------------------------
# Rule table — generic rule types (ADR-0004). One entry per deterministic
# guard; policy semantics live here as DATA.
# ---------------------------------------------------------------------------

@dataclass
class CompanionSpec:
    state_field: str
    needs: Callable[[Any], bool]        # known value → companion call required?
    companion_tool: str
    companion_args: dict
    inject_when_unknown: bool = False   # True: inject without observing (safe/idempotent)


@dataclass
class CompanionAvailableRule:
    policy_id: str
    trigger_tool: str
    companion_tool: str
    reason: str
    when: Callable[[dict], bool] | None = None
    satisfied_by_state: Callable[[dict], bool] | None = None


@dataclass
class StateCompanionRule:
    policy_id: str
    trigger_tool: str
    observe_tool: str | None
    companions: list[CompanionSpec]
    when: Callable[[dict], bool] | None = None


@dataclass
class StatePreconditionRule:
    policy_id: str
    trigger_tool: str
    required_fields: tuple[str, ...]
    predicate: Callable[[dict], bool]   # over projected state; fields guaranteed known
    observe_tool: str | None
    block_reason: str
    when: Callable[[dict], bool] | None = None


@dataclass
class PriorObservationRule:
    policy_id: str
    trigger_tool: str
    observe_tool: str
    build_args: Callable[[TaskContext], dict | None]  # None → not constructible → pass
    when: Callable[[dict], bool] | None = None


@dataclass
class ValueBoundRule:
    policy_id: str
    trigger_tool: str
    check: Callable[[dict, TaskContext], str | None]  # violation reason | None
    when: Callable[[dict], bool] | None = None


@dataclass
class NoParallelRule:
    policy_id: str
    group: frozenset[str]
    reason: str


@dataclass
class ObligationNoteRule:
    policy_id: str
    trigger_tool: str
    note: Callable[[dict, dict], str | None]  # (args, projected state) → note | None
    when: Callable[[dict], bool] | None = None


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _is_opening(a: dict) -> bool:
    # percentage==0 is a close; absent/non-numeric counts as opening (conservative)
    return _num(a.get("percentage")) != 0.0


def _is_opening_strict(a: dict) -> bool:
    p = _num(a.get("percentage"))
    return p is not None and p > 0


def _is_on(a: dict) -> bool:
    return a.get("on") is True


def _weather_args(tc: TaskContext) -> dict | None:
    if not (tc.current_location and tc.now):
        return None
    loc_id = tc.current_location.get("id")
    month, day, hour = tc.now.get("month"), tc.now.get("day"), tc.now.get("hour")
    if loc_id is None or month is None or day is None or hour is None:
        return None
    return {"location_or_poi_id": loc_id, "month": month, "day": day,
            "time_hour_24hformat": hour}


def _current_day_bound(param_month: str, param_day: str) -> Callable[[dict, TaskContext], str | None]:
    def check(args: dict, tc: TaskContext) -> str | None:
        if not tc.now:
            return None
        for param, key in ((param_month, "month"), (param_day, "day")):
            want = tc.now.get(key)
            got = args.get(param)
            if want is not None and got is not None and got != want:
                return f"{param}={got} requested, but only the current day ({key}={want}) is allowed"
        return None
    return check


NAV_EDIT_TOOLS = frozenset({
    "navigation_add_one_waypoint",
    "navigation_delete_waypoint",
    "navigation_replace_one_waypoint",
    "navigation_replace_final_destination",
    "navigation_delete_destination",
})

_NAV_STATE_TOOL = "get_current_navigation_state"

# THE rule table. Order matters and is deterministic:
# refusals → hard blocks / preconditions → observation gates → companions →
# batch constraints → notes.
RULES: list[Any] = [
    # --- AUT-POL:005 availability aspect (exactly the deleted Stufe-3 guard) ---
    CompanionAvailableRule(
        policy_id="AUT-POL:005",
        trigger_tool="open_close_sunroof",
        companion_tool="open_close_sunshade",
        when=_is_opening,
        satisfied_by_state=lambda s: _num(s.get("sunshade_position")) == 100.0,
        reason="Sunroof can only be opened if the sunshade is fully opened or "
               "opened in parallel; sunshade control is not available.",
    ),
    # --- AUT-POL:023 / 024 — current-day value bounds ---
    ValueBoundRule(
        policy_id="AUT-POL:023",
        trigger_tool="get_entries_from_calendar",
        check=_current_day_bound("month", "day"),
    ),
    ValueBoundRule(
        policy_id="AUT-POL:024",
        trigger_tool="get_weather",
        check=_current_day_bound("month", "day"),
    ),
    # --- AUT-POL:014 — high beams forbidden while fog lights on ---
    StatePreconditionRule(
        policy_id="AUT-POL:014",
        trigger_tool="set_head_lights_high_beams",
        when=_is_on,
        required_fields=("fog_lights",),
        predicate=lambda s: s["fog_lights"] is not True,
        observe_tool="get_exterior_lights_status",
        block_reason="High beam headlights cannot be activated while the fog "
                     "lights are on (reduced visibility in foggy conditions).",
    ),
    # --- AUT-POL:017 — waypoint edit tools require active navigation ---
    *[
        StatePreconditionRule(
            policy_id="AUT-POL:017",
            trigger_tool=tool,
            required_fields=("navigation_active",),
            predicate=lambda s: s["navigation_active"] is True,
            observe_tool=_NAV_STATE_TOOL,
            block_reason="Waypoint and destination edit tools can only be used "
                         "while the navigation system is active with a route set.",
        )
        for tool in sorted(NAV_EDIT_TOOLS)
    ],
    # --- AUT-POL:018 — a NEW navigation only while navigation is inactive ---
    StatePreconditionRule(
        policy_id="AUT-POL:018",
        trigger_tool="set_new_navigation",
        required_fields=("navigation_active",),
        predicate=lambda s: s["navigation_active"] is not True,
        observe_tool=_NAV_STATE_TOOL,
        block_reason="Navigation is already active: use the waypoint/destination "
                     "edit tools instead of setting a new navigation.",
    ),
    # --- AUT-POL:019 — destination/waypoint deletion needs an intermediate stop ---
    *[
        StatePreconditionRule(
            policy_id="AUT-POL:019",
            trigger_tool=tool,
            required_fields=("nav_waypoint_count",),
            predicate=lambda s: isinstance(s["nav_waypoint_count"], int)
                                and s["nav_waypoint_count"] >= 3,
            observe_tool=_NAV_STATE_TOOL,
            block_reason="The route must always keep at least a start and a "
                         "destination; without an intermediate stop nothing can "
                         "be deleted from it.",
        )
        for tool in ("navigation_delete_destination", "navigation_delete_waypoint")
    ],
    # --- AUT-POL:009 — weather must be checked before sunroof-open / fog lights ---
    PriorObservationRule(
        policy_id="AUT-POL:009",
        trigger_tool="open_close_sunroof",
        when=_is_opening_strict,
        observe_tool="get_weather",
        build_args=_weather_args,
    ),
    PriorObservationRule(
        policy_id="AUT-POL:009",
        trigger_tool="set_fog_lights",
        when=_is_on,
        observe_tool="get_weather",
        build_args=_weather_args,
    ),
    # --- AUT-POL:005 value aspect — sunshade must be FULLY open (100) ---
    StateCompanionRule(
        policy_id="AUT-POL:005",
        trigger_tool="open_close_sunroof",
        when=_is_opening_strict,
        observe_tool=None,  # parallel full-open is always policy-conform
        companions=[
            CompanionSpec(
                state_field="sunshade_position",
                needs=lambda v: _num(v) != 100.0,
                companion_tool="open_close_sunshade",
                companion_args={"percentage": 100},
                inject_when_unknown=True,
            ),
        ],
    ),
    # --- AUT-POL:010 — window defrost companions ---
    StateCompanionRule(
        policy_id="AUT-POL:010",
        trigger_tool="set_window_defrost",
        when=lambda a: _is_on(a) and a.get("defrost_window") in ("FRONT", "ALL"),
        observe_tool="get_climate_settings",
        companions=[
            CompanionSpec(
                state_field="fan_speed",
                needs=lambda v: isinstance(v, (int, float)) and v < 2,
                companion_tool="set_fan_speed",
                companion_args={"level": 2},
            ),
            CompanionSpec(
                state_field="fan_airflow_direction",
                needs=lambda v: "WINDSHIELD" not in str(v),
                companion_tool="set_fan_airflow_direction",
                companion_args={"direction": "WINDSHIELD"},
            ),
            CompanionSpec(
                state_field="air_conditioning",
                needs=lambda v: v is not True,
                companion_tool="set_air_conditioning",
                companion_args={"on": True},
            ),
        ],
    ),
    # --- AUT-POL:011 — AC ON companions ---
    StateCompanionRule(
        policy_id="AUT-POL:011",
        trigger_tool="set_air_conditioning",
        when=_is_on,
        observe_tool="get_vehicle_window_positions",
        companions=[
            CompanionSpec(
                state_field=window_field,
                needs=lambda v: isinstance(v, (int, float)) and v > 20,
                companion_tool="open_close_window",
                companion_args={"window": window_name, "percentage": 0},
            )
            for window_name, window_field in (
                ("DRIVER", "window_driver_position"),
                ("PASSENGER", "window_passenger_position"),
                ("DRIVER_REAR", "window_driver_rear_position"),
                ("PASSENGER_REAR", "window_passenger_rear_position"),
            )
        ],
    ),
    StateCompanionRule(
        policy_id="AUT-POL:011",
        trigger_tool="set_air_conditioning",
        when=_is_on,
        observe_tool="get_climate_settings",
        companions=[
            CompanionSpec(
                state_field="fan_speed",
                needs=lambda v: v == 0,
                companion_tool="set_fan_speed",
                companion_args={"level": 1},
            ),
        ],
    ),
    # --- AUT-POL:013 — fog light companions ---
    StateCompanionRule(
        policy_id="AUT-POL:013",
        trigger_tool="set_fog_lights",
        when=_is_on,
        observe_tool="get_exterior_lights_status",
        companions=[
            CompanionSpec(
                state_field="head_lights_low_beams",
                needs=lambda v: v is not True,
                companion_tool="set_head_lights_low_beams",
                companion_args={"on": True},
            ),
            CompanionSpec(
                state_field="head_lights_high_beams",
                needs=lambda v: v is True,
                companion_tool="set_head_lights_high_beams",
                companion_args={"on": False},
            ),
        ],
    ),
    # --- AUT-POL:018 — waypoint edits never in parallel ---
    NoParallelRule(
        policy_id="AUT-POL:018",
        group=NAV_EDIT_TOOLS,
        reason="Waypoint/destination edit tools must run sequentially, never in "
               "parallel (waypoint order indexes would be confused).",
    ),
    # --- LLM-POL:007 — semantic rest gets a marked obligation note ---
    ObligationNoteRule(
        policy_id="LLM-POL:007",
        trigger_tool="open_close_window",
        note=lambda a, s: (
            "LLM-POL:007: window is being opened beyond 25% while the AC is ON — "
            "the reply MUST warn about energy inefficiency and the action needs "
            "user confirmation."
            if (_num(a.get("percentage")) or 0) > 25 and s.get("air_conditioning") is True
            else None
        ),
    ),
]


# ---------------------------------------------------------------------------
# Pre-flight engine — generic iteration over RULES. No tool names below.
# ---------------------------------------------------------------------------

@dataclass
class Injection:
    tool: str
    arguments: dict
    policy_id: str


@dataclass
class PreFlightResult:
    kept: list = field(default_factory=list)         # original call objects, order kept
    injected: list[Injection] = field(default_factory=list)
    deferred: list = field(default_factory=list)     # postponed to a later plan round
    blocked: list[PolicyViolation] = field(default_factory=list)
    missing_capability: list[PolicyViolation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class _Env:
    def __init__(self, index, ledger: Ledger, result: PreFlightResult):
        self.index = index
        self.ledger = ledger
        self.result = result
        self.state = derive_known_state(ledger)
        self.task_ctx = parse_task_context(ledger)
        turn_calls = ledger.get_tool_calls_this_turn()
        self.tools_called_this_turn = {e.tool_name for e in turn_calls}
        self.signatures_this_turn = {
            f"{e.tool_name}:{json.dumps(e.content, sort_keys=True)}"
            for e in turn_calls if isinstance(e.content, dict)
        }

    # --- shared helpers used by the evaluators ---

    def projected(self) -> dict:
        batch = list(self.result.kept) + [
            _InjectedCall(i.tool, i.arguments) for i in self.result.injected
        ]
        return _project(self.state, batch)

    def inject(self, tool: str, arguments: dict, policy_id: str, note: str) -> None:
        sig = f"{tool}:{json.dumps(arguments, sort_keys=True)}"
        already = (
            sig in self.signatures_this_turn
            or any(i.tool == tool and i.arguments == arguments for i in self.result.injected)
        )
        if already:
            return
        self.result.injected.append(Injection(tool, arguments, policy_id))
        self.result.notes.append(note)

    def defer(self, call, policy_id: str, why: str) -> None:
        self.result.kept.remove(call)
        self.result.deferred.append(call)
        self.result.notes.append(
            f"{policy_id}: deferred {call.tool} to a later plan round — {why}"
        )

    def can_observe(self, observe_tool: str) -> bool:
        """Loop protection: observe only once per turn, and only if available."""
        return (
            self.index.has_tool(observe_tool)
            and observe_tool not in self.tools_called_this_turn
            and not any(i.tool == observe_tool for i in self.result.injected)
        )


class _InjectedCall:
    def __init__(self, tool: str, arguments: dict):
        self.tool = tool
        self.arguments = arguments


def _triggers(rule, env: _Env) -> list:
    return [
        c for c in list(env.result.kept)
        if c.tool == rule.trigger_tool and (rule.when is None or rule.when(c.arguments))
    ]


def _eval_companion_available(rule: CompanionAvailableRule, env: _Env) -> None:
    for call in _triggers(rule, env):
        satisfied = (
            env.index.has_tool(rule.companion_tool)
            or any(c.tool == rule.companion_tool for c in env.result.kept)
            or rule.companion_tool in env.tools_called_this_turn
            or (rule.satisfied_by_state is not None and rule.satisfied_by_state(env.state))
        )
        if not satisfied:
            env.result.missing_capability.append(
                PolicyViolation(rule.policy_id, "AUT", rule.reason)
            )
            return


def _eval_value_bound(rule: ValueBoundRule, env: _Env) -> None:
    for call in _triggers(rule, env):
        reason = rule.check(call.arguments, env.task_ctx)
        if reason:
            env.result.kept.remove(call)
            env.result.blocked.append(PolicyViolation(rule.policy_id, "AUT", reason))


def _eval_state_precondition(rule: StatePreconditionRule, env: _Env) -> None:
    for call in _triggers(rule, env):
        if call not in env.result.kept:
            continue
        projected = env.projected()
        unknown = [f for f in rule.required_fields if f not in projected]
        if unknown:
            if rule.observe_tool and env.can_observe(rule.observe_tool):
                env.inject(rule.observe_tool, {}, rule.policy_id,
                           f"{rule.policy_id}: observing {rule.observe_tool} to "
                           f"verify precondition for {call.tool}")
                env.defer(call, rule.policy_id,
                          f"precondition on {', '.join(unknown)} not yet verifiable")
            else:
                env.result.notes.append(
                    f"{rule.policy_id}: precondition for {call.tool} on "
                    f"{', '.join(unknown)} could not be verified deterministically"
                )
            continue
        if not rule.predicate(projected):
            env.result.kept.remove(call)
            env.result.blocked.append(
                PolicyViolation(rule.policy_id, "AUT", rule.block_reason)
            )


def _eval_prior_observation(rule: PriorObservationRule, env: _Env) -> None:
    for call in _triggers(rule, env):
        if call not in env.result.kept:
            continue
        if env.ledger.has_tool_result(rule.observe_tool):
            continue
        args = rule.build_args(env.task_ctx)
        if args is None or not env.can_observe(rule.observe_tool):
            env.result.notes.append(
                f"{rule.policy_id}: required {rule.observe_tool} check before "
                f"{call.tool} could not be injected deterministically"
            )
            continue
        env.inject(rule.observe_tool, args, rule.policy_id,
                   f"{rule.policy_id}: {rule.observe_tool} must be checked before "
                   f"{call.tool} is executed")
        env.defer(call, rule.policy_id,
                  f"{rule.observe_tool} result required first")


def _eval_state_companion(rule: StateCompanionRule, env: _Env) -> None:
    for call in _triggers(rule, env):
        if call not in env.result.kept:
            continue
        projected = env.projected()
        needs_observation = [
            spec for spec in rule.companions
            if spec.state_field not in projected and not spec.inject_when_unknown
        ]
        if needs_observation:
            if rule.observe_tool and env.can_observe(rule.observe_tool):
                env.inject(rule.observe_tool, {}, rule.policy_id,
                           f"{rule.policy_id}: observing {rule.observe_tool} to "
                           f"determine required companion actions for {call.tool}")
                env.defer(call, rule.policy_id,
                          f"companion actions depend on {rule.observe_tool} result")
            else:
                fields = ", ".join(s.state_field for s in needs_observation)
                env.result.notes.append(
                    f"{rule.policy_id}: companion conditions for {call.tool} on "
                    f"{fields} could not be verified deterministically"
                )
            continue
        for spec in rule.companions:
            value = projected.get(spec.state_field)
            required = spec.state_field not in projected or spec.needs(value)
            if not required:
                continue
            if not env.index.has_tool(spec.companion_tool):
                env.result.missing_capability.append(PolicyViolation(
                    rule.policy_id, "AUT",
                    f"{call.tool} requires the companion action "
                    f"{spec.companion_tool}, which is not available in this car.",
                ))
                return
            env.inject(spec.companion_tool, dict(spec.companion_args), rule.policy_id,
                       f"{rule.policy_id}: injected companion "
                       f"{spec.companion_tool} for {call.tool}")
            projected = env.projected()


def _eval_no_parallel(rule: NoParallelRule, env: _Env) -> None:
    seen = False
    for call in list(env.result.kept):
        if call.tool not in rule.group:
            continue
        if not seen:
            seen = True
            continue
        env.defer(call, rule.policy_id, rule.reason)


def _eval_obligation_note(rule: ObligationNoteRule, env: _Env) -> None:
    for call in _triggers(rule, env):
        note = rule.note(call.arguments, env.projected())
        if note and note not in env.result.notes:
            env.result.notes.append(note)


_EVALUATORS: dict[type, Callable[[Any, _Env], None]] = {
    CompanionAvailableRule: _eval_companion_available,
    ValueBoundRule: _eval_value_bound,
    StatePreconditionRule: _eval_state_precondition,
    PriorObservationRule: _eval_prior_observation,
    StateCompanionRule: _eval_state_companion,
    NoParallelRule: _eval_no_parallel,
    ObligationNoteRule: _eval_obligation_note,
}


class PolicyChecker:
    """Pre-flight check before each planned batch (Stufe 4, ADR-0004).

    Iterates generically over RULES; may refuse (missing capability), block
    (hard policy violation), inject corrective/observation calls, or defer
    calls to a later plan round. Tool names appear only in the rule data.
    """

    def pre_flight(self, calls: list, ledger: Ledger, index) -> PreFlightResult:
        result = PreFlightResult(kept=list(calls))
        env = _Env(index, ledger, result)
        for rule in RULES:
            _EVALUATORS[type(rule)](rule, env)
            if result.missing_capability:
                break
        return result
