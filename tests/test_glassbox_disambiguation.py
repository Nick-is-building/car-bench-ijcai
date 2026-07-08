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


if __name__ == "__main__":
    unittest.main()
