"""Stufe-6 unit tests: deterministic disambiguation cascade + plan-loop guard.

All LLM calls are faked (injected extractor / scripted schema instances), so the
tests are fully deterministic and run without API keys. Covers both task
subtypes (disambiguation_internal = never ask; disambiguation_user = ask) plus
the Null-FP discipline and the value-flow guarantee (resolved value reaches the
call argument exactly).
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from track_1_agent_under_test.glassbox import (
    EmitToolCalls,
    Ledger,
    StateMachine,
    TurnContext,
)
from track_1_agent_under_test.glassbox import llm as glassbox_llm
from track_1_agent_under_test.glassbox.state_machine import PlannedCall
from track_1_agent_under_test.glassbox.disambiguation import (
    DisambiguationEngine,
    PreferenceSlot,
    _coerce,
)
from track_1_agent_under_test.glassbox.prompts.intake import Intent, ValueAmbiguity
from track_1_agent_under_test.glassbox.prompts.plan import Plan, PlanStep


def _ctx(intent: dict, prefs_in_ledger: bool = False) -> TurnContext:
    ledger = Ledger()
    ledger.add_system("You are a car assistant.")
    ledger.add_user_turn("Set the cabin temperature, please.")
    if prefs_in_ledger:
        ledger.add_tool_result(
            "get_user_preferences",
            json.dumps({"status": "SUCCESS", "result": {
                "vehicle_settings": {"climate_control": ["prefers 22 degrees"]}}}),
            "call_pref0",
        )
    ctx = TurnContext(ledger=ledger, tools=[], model="fake")
    ctx.intent = intent
    return ctx


def _amb(tool="set_climate_temperature", argument="temperature", user_stated=False,
         candidates=None):
    a = {"tool": tool, "argument": argument, "user_stated": user_stated}
    if candidates is not None:
        a["candidates"] = candidates
    return a


# ---------------------------------------------------------------------------
# Pure cascade — resolve_slot (no ledger, no LLM)
# ---------------------------------------------------------------------------

class CascadeTest(unittest.TestCase):
    def setUp(self):
        self.eng = DisambiguationEngine()

    def test_p2_preference_wins_and_never_asks(self):
        # disambiguation_internal: preference default → silent, even if state-changing
        res = self.eng.resolve_slot(
            is_state_changing=True, pref=PreferenceSlot(default="50"),
            heuristic_default=None, context_candidates=None, question="q?")
        self.assertEqual(res.status, "resolved")
        self.assertEqual(res.value, "50")
        self.assertEqual(res.priority, "preference")

    def test_p3_heuristic_default(self):
        res = self.eng.resolve_slot(
            is_state_changing=True, pref=None,
            heuristic_default="fastest", context_candidates=None, question="q?")
        self.assertEqual(res.status, "resolved")
        self.assertEqual(res.value, "fastest")
        self.assertEqual(res.priority, "heuristic")

    def test_p4_single_context_candidate(self):
        res = self.eng.resolve_slot(
            is_state_changing=True, pref=None, heuristic_default=None,
            context_candidates=["driver_window"], question="q?")
        self.assertEqual(res.status, "resolved")
        self.assertEqual(res.value, "driver_window")
        self.assertEqual(res.priority, "context")

    def test_p5_two_candidates_ask(self):
        # disambiguation_user: ≥2 valid candidates → ask
        res = self.eng.resolve_slot(
            is_state_changing=True, pref=None, heuristic_default=None,
            context_candidates=["driver_window", "passenger_window"], question="Which window?")
        self.assertEqual(res.status, "ask")
        self.assertEqual(res.question, "Which window?")

    def test_prohibition_eliminates_candidate(self):
        # pref prohibits "100" → only "50" remains valid → resolved via context
        res = self.eng.resolve_slot(
            is_state_changing=True, pref=PreferenceSlot(prohibited=["100"]),
            heuristic_default=None, context_candidates=["50", "100"], question="q?")
        self.assertEqual(res.status, "resolved")
        self.assertEqual(res.value, "50")

    def test_prohibited_default_is_skipped(self):
        # a preference default that is itself prohibited is not applied
        res = self.eng.resolve_slot(
            is_state_changing=True,
            pref=PreferenceSlot(default="100", prohibited=["100"]),
            heuristic_default="fastest", context_candidates=None, question="q?")
        self.assertEqual(res.value, "fastest")  # fell through to heuristic

    def test_null_fp_non_state_changing_never_asks(self):
        # nothing resolves, but read-only request → do not ask spuriously
        res = self.eng.resolve_slot(
            is_state_changing=False, pref=None, heuristic_default=None,
            context_candidates=None, question="q?")
        self.assertEqual(res.status, "unresolved")


# ---------------------------------------------------------------------------
# _coerce — type coercion of resolved value into the argument's runtime type
# ---------------------------------------------------------------------------

class CoerceTest(unittest.TestCase):
    def test_percent_string_to_int_with_int_sample(self):
        self.assertEqual(_coerce("50%", 100), 50)

    def test_bare_number_without_sample(self):
        self.assertEqual(_coerce("22", None), 22)

    def test_non_numeric_string_kept(self):
        self.assertEqual(_coerce("fastest", None), "fastest")

    def test_float_sample(self):
        self.assertEqual(_coerce("21.5", 20.0), 21.5)


# ---------------------------------------------------------------------------
# Plan-loop pre-flight guard — gather / override / ask / Null-FP
# ---------------------------------------------------------------------------

class PreFlightTest(unittest.TestCase):
    def setUp(self):
        self.eng = DisambiguationEngine()
        self.call = PlannedCall(
            tool="set_climate_temperature", arguments={"temperature": 99}, call_id="c1")

    def test_no_ambiguities_passthrough(self):
        ctx = _ctx({"is_state_changing": True, "value_ambiguities": []})
        out = self.eng.pre_flight(ctx, [self.call])
        self.assertEqual(out.calls, [self.call])
        self.assertIsNone(out.inject_preferences)

    def test_gather_when_preferences_absent(self):
        ctx = _ctx({"is_state_changing": True, "value_ambiguities": [_amb()]})
        out = self.eng.pre_flight(ctx, [self.call])
        self.assertEqual(
            out.inject_preferences,
            {"preference_categories": {"vehicle_settings": {"climate_control": True}}})
        self.assertEqual(out.calls, [])

    def test_value_flow_override(self):
        # D3: parsed preference 22 must reach the call argument exactly.
        ctx = _ctx({"is_state_changing": True, "value_ambiguities": [_amb()]},
                   prefs_in_ledger=True)
        out = self.eng.pre_flight(
            ctx, [self.call],
            extractor=lambda c, t, a: PreferenceSlot(default="22"))
        self.assertEqual(len(out.calls), 1)
        self.assertEqual(out.calls[0].arguments["temperature"], 22)
        self.assertEqual(out.resolved, [("set_climate_temperature", "temperature", 22)])

    def test_ask_when_preferences_silent(self):
        ctx = _ctx({"is_state_changing": True, "value_ambiguities": [_amb()],
                    "clarification_question": "What temperature would you like?"},
                   prefs_in_ledger=True)
        out = self.eng.pre_flight(
            ctx, [self.call], extractor=lambda c, t, a: PreferenceSlot())
        self.assertEqual(out.question, "What temperature would you like?")
        self.assertEqual(out.calls, [])

    def test_null_fp_user_stated_value(self):
        # user explicitly gave the value → not ambiguous → no gather, no ask
        ctx = _ctx({"is_state_changing": True,
                    "value_ambiguities": [_amb(user_stated=True)]})
        out = self.eng.pre_flight(ctx, [self.call])
        self.assertEqual(out.calls, [self.call])
        self.assertIsNone(out.inject_preferences)
        self.assertEqual(out.question, "")

    def test_gather_once_then_ask_no_infinite_loop(self):
        # preferences_gathered already true but none in ledger → do not re-gather;
        # silent preference unavailable → ask (state-changing)
        ctx = _ctx({"is_state_changing": True, "value_ambiguities": [_amb()],
                    "clarification_question": "What temperature?"})
        ctx.preferences_gathered = True
        out = self.eng.pre_flight(ctx, [self.call])
        self.assertIsNone(out.inject_preferences)
        self.assertEqual(out.question, "What temperature?")


# ---------------------------------------------------------------------------
# OI-016 — enum/choice value ambiguity (ambient light color) runs the cascade
# ---------------------------------------------------------------------------

class EnumValueAmbiguityTest(unittest.TestCase):
    """A clear action with an under-specified ENUM value (lightcolor) must flow
    through the same cascade as a numeric value: preference resolves it silently,
    the resolved string reaches the call argument verbatim (no numeric coercion),
    and the gather step targets the right preference category."""

    def setUp(self):
        self.eng = DisambiguationEngine()
        self.call = PlannedCall(
            tool="set_ambient_lights",
            arguments={"on": True, "lightcolor": "RED"},  # planner placeholder
            call_id="c1",
        )

    def test_cascade_resolves_enum_from_preference_never_asks(self):
        res = self.eng.resolve_slot(
            is_state_changing=True,
            pref=PreferenceSlot(default="PURPLE"),
            heuristic_default=None,
            context_candidates=None,
            question="What color?",
        )
        self.assertEqual(res.status, "resolved")
        self.assertEqual(res.value, "PURPLE")
        self.assertEqual(res.priority, "preference")

    def test_enum_string_not_coerced_to_number(self):
        # _coerce must leave an enum color untouched even with a string sample.
        self.assertEqual(_coerce("PURPLE", "RED"), "PURPLE")
        self.assertEqual(_coerce("PURPLE", None), "PURPLE")

    def test_value_flow_override_enum(self):
        ctx = _ctx({"is_state_changing": True,
                    "value_ambiguities": [_amb(tool="set_ambient_lights",
                                               argument="lightcolor")]},
                   prefs_in_ledger=True)
        out = self.eng.pre_flight(
            ctx, [self.call],
            extractor=lambda c, t, a: PreferenceSlot(default="PURPLE"))
        self.assertEqual(len(out.calls), 1)
        self.assertEqual(out.calls[0].arguments["lightcolor"], "PURPLE")
        self.assertEqual(out.calls[0].arguments["on"], True)  # untouched
        self.assertEqual(out.resolved,
                         [("set_ambient_lights", "lightcolor", "PURPLE")])

    def test_gather_targets_vehicle_settings_category(self):
        ctx = _ctx({"is_state_changing": True,
                    "value_ambiguities": [_amb(tool="set_ambient_lights",
                                               argument="lightcolor")]})
        out = self.eng.pre_flight(ctx, [self.call])
        self.assertEqual(
            out.inject_preferences,
            {"preference_categories": {"vehicle_settings": {"vehicle_settings": True}}})
        self.assertEqual(out.calls, [])

    def test_ask_when_preference_silent_state_changing(self):
        # No preference and no context candidate → still asks (Null-FP guard:
        # we never invent a color).
        ctx = _ctx({"is_state_changing": True,
                    "value_ambiguities": [_amb(tool="set_ambient_lights",
                                               argument="lightcolor")],
                    "clarification_question": "What color would you like?"},
                   prefs_in_ledger=True)
        out = self.eng.pre_flight(
            ctx, [self.call], extractor=lambda c, t, a: PreferenceSlot())
        self.assertEqual(out.question, "What color would you like?")
        self.assertEqual(out.calls, [])


AMBIENT_SCHEMA = [
    {"function": {
        "name": "set_ambient_lights",
        "description": "Turns the ambient light on (including the color) or off.",
        "parameters": {
            "properties": {
                "on": {"type": "boolean"},
                "lightcolor": {"type": "string",
                               "enum": ["RED", "PURPLE", "NONE"]},
            },
            "required": ["on", "lightcolor"],
            "additionalProperties": False,
        },
    }},
]


class ResolverSchemaGuardTest(unittest.TestCase):
    """OI-016 (C1): the value-flow resolver must not inject a resolved value
    under a slot name that is absent from the tool schema. The LLM may flag the
    ambiguous slot under a natural-language name ("color") that differs from the
    schema parameter ("lightcolor"); injecting it would add a non-schema argument
    the evaluator rejects with a TypeError."""

    def setUp(self):
        self.eng = DisambiguationEngine()

    def test_invented_slot_name_is_not_injected(self):
        # planner already drafted the schema-correct lightcolor; the resolver
        # tries to inject under the invented name "color" → must be skipped.
        call = PlannedCall(
            tool="set_ambient_lights",
            arguments={"on": True, "lightcolor": "PURPLE"},
            call_id="c1",
        )
        ctx = _ctx({"is_state_changing": True,
                    "value_ambiguities": [_amb(tool="set_ambient_lights",
                                               argument="color")]},
                   prefs_in_ledger=True)
        ctx.tools = AMBIENT_SCHEMA
        out = self.eng.pre_flight(
            ctx, [call], extractor=lambda c, t, a: PreferenceSlot(default="PURPLE"))
        self.assertEqual(len(out.calls), 1)
        self.assertNotIn("color", out.calls[0].arguments)  # invented name dropped
        self.assertEqual(out.calls[0].arguments["lightcolor"], "PURPLE")  # untouched
        self.assertEqual(out.calls[0].arguments["on"], True)
        self.assertEqual(out.resolved, [])  # nothing injected

    def test_valid_schema_slot_still_injected_null_fp(self):
        # regression: a slot flagged under the real schema name resolves normally.
        call = PlannedCall(
            tool="set_ambient_lights",
            arguments={"on": True, "lightcolor": "RED"},  # placeholder
            call_id="c1",
        )
        ctx = _ctx({"is_state_changing": True,
                    "value_ambiguities": [_amb(tool="set_ambient_lights",
                                               argument="lightcolor")]},
                   prefs_in_ledger=True)
        ctx.tools = AMBIENT_SCHEMA
        out = self.eng.pre_flight(
            ctx, [call], extractor=lambda c, t, a: PreferenceSlot(default="PURPLE"))
        self.assertEqual(out.calls[0].arguments["lightcolor"], "PURPLE")
        self.assertEqual(out.resolved,
                         [("set_ambient_lights", "lightcolor", "PURPLE")])


# ---------------------------------------------------------------------------
# State-machine wiring — the guard injects get_user_preferences (gather)
# ---------------------------------------------------------------------------

TOOLS = [
    {"function": {
        "name": "set_climate_temperature",
        "description": "Set the cabin temperature.",
        "parameters": {"properties": {"temperature": {"type": "number"}},
                       "required": ["temperature"]},
    }},
    {"function": {
        "name": "get_user_preferences",
        "description": "Retrieve learned user preferences.",
        "parameters": {"properties": {"preference_categories": {"type": "object"}},
                       "required": ["preference_categories"]},
    }},
]


class FakeLLM:
    def __init__(self, intents=(), plans=()):
        self.queues = {"Intent": list(intents), "Plan": list(plans)}

    def __call__(self, messages, schema, model=None, system=None, tools=None,
                 temperature=0.0):
        name = schema.__name__
        if not self.queues.get(name):
            raise AssertionError(f"unexpected LLM call for schema {name!r}")
        return self.queues[name].pop(0)


# ---------------------------------------------------------------------------
# OI-016 — deterministic PRE-PLAN gather (empty plan → fetch preference first)
# ---------------------------------------------------------------------------

def _pp_ctx(intent: dict, prefs_in_ledger: bool = False) -> TurnContext:
    ledger = Ledger()
    ledger.add_system("You are a car assistant.")
    ledger.add_user_turn("Change the ambient light color, please.")
    if prefs_in_ledger:
        ledger.add_tool_result(
            "get_user_preferences",
            json.dumps({"status": "SUCCESS", "result": {
                "vehicle_settings": {"vehicle_settings": ["prefers PURPLE"]}}}),
            "call_pref0",
        )
    ctx = TurnContext(ledger=ledger, tools=[], model="fake")
    ctx.intent = intent
    return ctx


class PrePlanGatherTest(unittest.TestCase):
    def setUp(self):
        self.eng = DisambiguationEngine()

    def _intent(self, required_params=None):
        return {
            "is_state_changing": True,
            "required_tools": ["set_ambient_lights"],
            "required_params": required_params or [],
        }

    def test_fires_for_unstated_preference_value(self):
        ctx = _pp_ctx(self._intent())
        out = self.eng.pre_plan_gather(ctx)
        self.assertEqual(
            out,
            {"preference_categories": {"vehicle_settings": {"vehicle_settings": True}}})

    def test_no_fire_when_value_user_stated(self):
        ctx = _pp_ctx(self._intent(required_params=[
            {"tool": "set_ambient_lights", "params": ["lightcolor"]}]))
        self.assertIsNone(self.eng.pre_plan_gather(ctx))

    def test_no_fire_when_prefs_already_in_ledger(self):
        ctx = _pp_ctx(self._intent(), prefs_in_ledger=True)
        self.assertIsNone(self.eng.pre_plan_gather(ctx))

    def test_no_fire_when_already_gathered(self):
        ctx = _pp_ctx(self._intent())
        ctx.preferences_gathered = True
        self.assertIsNone(self.eng.pre_plan_gather(ctx))

    def test_no_fire_when_tool_not_in_map(self):
        ctx = _pp_ctx({"is_state_changing": True,
                       "required_tools": ["set_climate_temperature"],
                       "required_params": []})
        self.assertIsNone(self.eng.pre_plan_gather(ctx))

    def test_no_fire_when_not_state_changing(self):
        ctx = _pp_ctx({"is_state_changing": False,
                       "required_tools": ["set_ambient_lights"],
                       "required_params": []})
        self.assertIsNone(self.eng.pre_plan_gather(ctx))


class GatherWiringTest(unittest.TestCase):
    def test_guard_injects_get_user_preferences(self):
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("Set the cabin temperature.")
        ctx = TurnContext(ledger=ledger, tools=TOOLS, model="fake")

        intent = Intent(
            user_request_summary="Set cabin temperature",
            required_tools=["set_climate_temperature"],
            is_state_changing=True, is_ambiguous=False,
            value_ambiguities=[ValueAmbiguity(
                tool="set_climate_temperature", argument="temperature", user_stated=False)],
        )
        plan = Plan(steps=[PlanStep(
            tool="set_climate_temperature",
            arguments_json=json.dumps({"temperature": 99}))])
        fake = FakeLLM(intents=[intent], plans=[plan])

        machine = StateMachine()
        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)

        self.assertIsInstance(action, EmitToolCalls)
        self.assertEqual([c.tool for c in action.calls], ["get_user_preferences"])
        self.assertTrue(ctx.preferences_gathered)

    def test_empty_plan_triggers_pre_plan_gather(self):
        # OI-016: planner emits NO steps because the ambient-light color is
        # unknown (neither user-stated nor guessable). The state machine must
        # gather the preference instead of ending the turn with an empty plan.
        tools = [
            {"function": {
                "name": "set_ambient_lights",
                "description": "Set ambient light color.",
                "parameters": {"properties": {
                    "on": {"type": "boolean"},
                    "lightcolor": {"type": "string",
                                   "enum": ["RED", "PURPLE", "BLUE"]}},
                    "required": ["on", "lightcolor"]},
            }},
            {"function": {
                "name": "get_user_preferences",
                "description": "Retrieve learned user preferences.",
                "parameters": {"properties": {
                    "preference_categories": {"type": "object"}},
                    "required": ["preference_categories"]},
            }},
        ]
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("Change the ambient light color.")
        ctx = TurnContext(ledger=ledger, tools=tools, model="fake")

        intent = Intent(
            user_request_summary="Change ambient light color",
            required_tools=["set_ambient_lights"],
            is_state_changing=True, is_ambiguous=False,
            value_ambiguities=[ValueAmbiguity(
                tool="set_ambient_lights", argument="lightcolor", user_stated=False)],
        )
        empty_plan = Plan(steps=[], done_reason="color unknown")
        fake = FakeLLM(intents=[intent], plans=[empty_plan])

        machine = StateMachine()
        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)

        self.assertIsInstance(action, EmitToolCalls)
        self.assertEqual([c.tool for c in action.calls], ["get_user_preferences"])
        self.assertEqual(
            action.calls[0].arguments,
            {"preference_categories": {"vehicle_settings": {"vehicle_settings": True}}})
        self.assertTrue(ctx.preferences_gathered)


# ---------------------------------------------------------------------------
# Ledger-derived value rules — selection (fastest route) + relative (fan +1)
# ---------------------------------------------------------------------------

from track_1_agent_under_test.glassbox.disambiguation import (  # noqa: E402
    _SELECTION_RULES,
    _SelectionRule,
)

NAV_SCHEMA = [
    {"function": {
        "name": "navigation_replace_final_destination",
        "description": "Replace the final destination.",
        "parameters": {
            "properties": {
                "new_destination_id": {"type": "string"},
                "route_id_leading_to_new_destination": {"type": "string"},
            },
            "required": ["new_destination_id", "route_id_leading_to_new_destination"],
        },
    }},
]

FAN_SCHEMA = [
    {"function": {
        "name": "set_fan_speed",
        "description": "Set the fan speed level.",
        "parameters": {
            "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 5}},
            "required": ["level"],
        },
    }},
]


def _ledger_ctx(tools, tool_results):
    """TurnContext whose ledger holds the given (tool_name, result_dict) pairs."""
    ledger = Ledger()
    ledger.add_system("You are a car assistant.")
    ledger.add_user_turn("Change something, please.")
    for i, (name, result) in enumerate(tool_results):
        ledger.add_tool_result(
            name, json.dumps({"status": "SUCCESS", "result": result}), f"call_{i}")
    ctx = TurnContext(ledger=ledger, tools=tools, model="fake")
    return ctx


class SelectionRuleTest(unittest.TestCase):
    """dis_24 shape: the route id is DERIVED from the routes tool result by the
    documented 'fastest route' heuristic — never invented, never asked."""

    def setUp(self):
        self.eng = DisambiguationEngine()
        self.routes = {"routes": [
            {"route_id": "rll_slow", "duration_hours": 9.0, "distance_km": 700.0},
            {"route_id": "rll_fast", "duration_hours": 7.5, "distance_km": 820.0},
            {"route_id": "rll_mid", "duration_hours": 8.0, "distance_km": 640.0},
        ]}

    def test_selection_picks_fastest_route(self):
        ctx = _ledger_ctx(NAV_SCHEMA,
                          [("get_routes_from_start_to_destination", self.routes)])
        ctx.intent = {"is_state_changing": True, "value_ambiguities": [_amb(
            tool="navigation_replace_final_destination",
            argument="route_id_leading_to_new_destination")]}
        call = PlannedCall(
            tool="navigation_replace_final_destination",
            arguments={"new_destination_id": "loc_x", "route_id_leading_to_new_destination": "PLACEHOLDER"},
            call_id="c1")
        out = self.eng.pre_flight(ctx, [call], extractor=lambda c, t, a: PreferenceSlot())
        self.assertEqual(len(out.calls), 1)
        self.assertEqual(
            out.calls[0].arguments["route_id_leading_to_new_destination"], "rll_fast")
        self.assertEqual(out.calls[0].arguments["new_destination_id"], "loc_x")  # untouched
        self.assertEqual(out.question, "")  # never asks

    def test_tie_break_prefers_shorter_distance(self):
        ties = {"routes": [
            {"route_id": "rll_long", "duration_hours": 7.5, "distance_km": 900.0},
            {"route_id": "rll_short", "duration_hours": 7.5, "distance_km": 500.0},
        ]}
        val = self.eng._select_by_minimum(
            _ledger_ctx([], [("get_routes_from_start_to_destination", ties)]),
            _SELECTION_RULES[("navigation_replace_final_destination",
                              "route_id_leading_to_new_destination")])
        self.assertEqual(val, "rll_short")

    def test_no_source_result_falls_through_to_ask(self):
        # Null-FP: routes tool never ran → derive returns nothing → normal cascade
        # asks (state-changing) rather than inventing a route.
        ctx = _ledger_ctx(NAV_SCHEMA, [])
        ctx.intent = {"is_state_changing": True,
                      "clarification_question": "Which route?",
                      "value_ambiguities": [_amb(
                          tool="navigation_replace_final_destination",
                          argument="route_id_leading_to_new_destination")]}
        call = PlannedCall(
            tool="navigation_replace_final_destination",
            arguments={"new_destination_id": "loc_x", "route_id_leading_to_new_destination": "?"},
            call_id="c1")
        out = self.eng.pre_flight(ctx, [call], extractor=lambda c, t, a: PreferenceSlot())
        self.assertEqual(out.question, "Which route?")
        self.assertEqual(out.calls, [])

    def test_mechanism_is_table_driven_not_hardcoded(self):
        # The selection logic branches on the RULE, not on any tool/domain name:
        # a synthetic rule over synthetic data resolves the same way, proving no
        # per-task answer is baked into the code.
        ctx = _ledger_ctx([], [("some_search_tool", {"items": [
            {"widget_id": "w_big", "price": 30},
            {"widget_id": "w_cheap", "price": 10},
        ]})])
        rule = _SelectionRule(source_tool="some_search_tool", collection="items",
                              id_field="widget_id", minimize="price")
        self.assertEqual(self.eng._select_by_minimum(ctx, rule), "w_cheap")


class RelativeRuleTest(unittest.TestCase):
    """dis_18 shape: 'increase the fan speed a bit' → current(0)+1, read from the
    climate settings result and clamped to the schema bounds."""

    def setUp(self):
        self.eng = DisambiguationEngine()

    def _run(self, current, direction):
        ctx = _ledger_ctx(FAN_SCHEMA,
                          [("get_climate_settings", {"fan_speed": current})])
        slot = {"tool": "set_fan_speed", "argument": "level",
                "user_stated": False, "relative_change": direction}
        ctx.intent = {"is_state_changing": True, "value_ambiguities": [slot]}
        call = PlannedCall(tool="set_fan_speed", arguments={"level": 0}, call_id="c1")
        return self.eng.pre_flight(ctx, [call], extractor=lambda c, t, a: PreferenceSlot())

    def test_increase_from_current(self):
        out = self._run(0, "increase")
        self.assertEqual(out.calls[0].arguments["level"], 1)
        self.assertEqual(out.question, "")

    def test_decrease_from_current(self):
        out = self._run(3, "decrease")
        self.assertEqual(out.calls[0].arguments["level"], 2)

    def test_clamped_to_max(self):
        out = self._run(5, "increase")
        self.assertEqual(out.calls[0].arguments["level"], 5)

    def test_clamped_to_min(self):
        out = self._run(0, "decrease")
        self.assertEqual(out.calls[0].arguments["level"], 0)

    def test_no_relative_change_falls_through_to_ask(self):
        # Null-FP: without a relative_change flag the derive stays silent and the
        # state-changing slot asks — code never guesses a level.
        ctx = _ledger_ctx(FAN_SCHEMA, [("get_climate_settings", {"fan_speed": 0})])
        ctx.intent = {"is_state_changing": True, "clarification_question": "What level?",
                      "value_ambiguities": [_amb(tool="set_fan_speed", argument="level")]}
        call = PlannedCall(tool="set_fan_speed", arguments={"level": 0}, call_id="c1")
        out = self.eng.pre_flight(ctx, [call], extractor=lambda c, t, a: PreferenceSlot())
        self.assertEqual(out.question, "What level?")
        self.assertEqual(out.calls, [])


if __name__ == "__main__":
    unittest.main()
