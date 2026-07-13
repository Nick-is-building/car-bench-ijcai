"""OI-017 unit tests: deterministic tool-argument enum validation (Lesson 1a)
plus the cross-turn tool-execution-error retry bound.

Root cause (D acceptance run, disambiguation_2): the planner sent
open_close_window(window="all windows") — a natural-language phrase instead of
the schema enum token "ALL" — and it was re-emitted 16 times because no bound
covered tool-execution errors and each user turn starts a fresh TurnContext.

All LLM calls are faked (scripted schema instances), so the tests are fully
deterministic and run without API keys.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from track_1_agent_under_test.glassbox import (
    EmitText,
    EmitToolCalls,
    Ledger,
    StateMachine,
    TurnContext,
)
from track_1_agent_under_test.glassbox import llm as glassbox_llm
from track_1_agent_under_test.glassbox.capability import CapabilityIndex
from track_1_agent_under_test.glassbox.state_machine import MAX_PLAN_ROUNDS
from track_1_agent_under_test.glassbox.prompts.intake import Intent
from track_1_agent_under_test.glassbox.prompts.plan import Plan, PlanStep


# open_close_window carries the exact schema enum the evaluator enforces.
TOOLS = [
    {"function": {
        "name": "open_close_window",
        "description": "Moves the specified window to a percentage open/closed.",
        "parameters": {
            "properties": {
                "window": {
                    "type": "string",
                    "enum": ["ALL", "DRIVER", "PASSENGER", "DRIVER_REAR",
                             "PASSENGER_REAR", "RIGHT_REAR", "LEFT_REAR"],
                },
                "percentage": {"type": "number", "minimum": 0, "maximum": 100},
            },
            "required": ["window", "percentage"],
        },
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


def _intent():
    return Intent(
        user_request_summary="Open all windows to 50 percent",
        required_tools=["open_close_window"],
        is_state_changing=True, is_ambiguous=False, value_ambiguities=[],
    )


def _plan(window, percentage=None):
    args = {"window": window}
    if percentage is not None:
        args["percentage"] = percentage
    return Plan(steps=[PlanStep(
        tool="open_close_window", arguments_json=json.dumps(args))])


def _ctx():
    ledger = Ledger()
    ledger.add_system("You are a car assistant.")
    # "50" appears literally so FabricationGuard C2 has ledger provenance
    ledger.add_user_turn("Open all the windows to 50 percent, please.")
    return ledger, TurnContext(ledger=ledger, tools=TOOLS, model="fake")


# ---------------------------------------------------------------------------
# CapabilityIndex.enum_values
# ---------------------------------------------------------------------------

class EnumValuesTest(unittest.TestCase):
    def setUp(self):
        self.idx = CapabilityIndex(TOOLS)

    def test_returns_enum_list(self):
        self.assertEqual(self.idx.enum_values("open_close_window", "window")[0], "ALL")

    def test_none_for_non_enum_param(self):
        self.assertIsNone(self.idx.enum_values("open_close_window", "percentage"))

    def test_none_for_unknown_tool_or_param(self):
        self.assertIsNone(self.idx.enum_values("nope", "window"))
        self.assertIsNone(self.idx.enum_values("open_close_window", "nope"))


# ---------------------------------------------------------------------------
# Ledger.failed_call_signatures
# ---------------------------------------------------------------------------

class FailedSignaturesTest(unittest.TestCase):
    def _fail(self):
        return json.dumps({
            "status": "FAILURE",
            "errors": {"OPEN_CLOSE_WINDOW_003": "Invalid window requested"},
        })

    def test_failure_result_is_reported(self):
        led = Ledger()
        led.add_user_turn("x")
        led.add_tool_call("open_close_window", {"window": "all windows", "percentage": 50}, "c1")
        led.add_tool_result("open_close_window", self._fail(), "c1")
        sigs = led.failed_call_signatures()
        self.assertIn(
            'open_close_window:' + json.dumps(
                {"percentage": 50, "window": "all windows"}, sort_keys=True),
            sigs,
        )

    def test_success_result_absent(self):
        led = Ledger()
        led.add_user_turn("x")
        led.add_tool_call("open_close_window", {"window": "ALL", "percentage": 50}, "c1")
        led.add_tool_result("open_close_window", json.dumps({"status": "SUCCESS"}), "c1")
        self.assertEqual(led.failed_call_signatures(), set())

    def test_plaintext_result_never_counts(self):
        led = Ledger()
        led.add_user_turn("x")
        led.add_tool_call("get_weather", {}, "c1")
        led.add_tool_result("get_weather", "It is sunny.", "c1")
        self.assertEqual(led.failed_call_signatures(), set())

    def test_plainstring_error_counts_as_failure(self):
        # OI-016 Fix B: a raising tool surfaces "Error: ..." as a plain string,
        # NOT the {"status":"FAILURE"} contract — it must still count so the
        # retry bound stops the identical failing call from looping.
        led = Ledger()
        led.add_user_turn("x")
        led.add_tool_call(
            "set_ambient_lights", {"lightcolor": "PURPLE", "on": True}, "c1")
        led.add_tool_result(
            "set_ambient_lights",
            "Error: SetAmbientLights.invoke() got an unexpected keyword argument 'color'",
            "c1")
        sigs = led.failed_call_signatures()
        self.assertIn(
            "set_ambient_lights:" + json.dumps(
                {"lightcolor": "PURPLE", "on": True}, sort_keys=True),
            sigs,
        )

    def test_benign_plainstring_still_not_failure(self):
        # Null-FP: plain strings that merely contain/lead with the letters
        # "error" but are not the "Error:"/"Exception:"/"Traceback (" shape
        # must never be flagged as failures (regression guard for Fix B).
        for text in ("Error-free and sunny.", "The route is clear.",
                     "errors were avoided"):
            led = Ledger()
            led.add_user_turn("x")
            led.add_tool_call("get_weather", {}, "c1")
            led.add_tool_result("get_weather", text, "c1")
            self.assertEqual(led.failed_call_signatures(), set(), msg=text)


# ---------------------------------------------------------------------------
# Enum validation in the plan-execute loop
# ---------------------------------------------------------------------------

class EnumGateWiringTest(unittest.TestCase):
    def test_invalid_enum_replans_then_honest_sink_not_16_retries(self):
        ledger, ctx = _ctx()
        # planner insists on the natural-language phrase every round
        fake = FakeLLM(intents=[_intent()],
                       plans=[_plan("all windows", 50)] * 3)
        machine = StateMachine()
        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)

        self.assertIsInstance(action, EmitText)
        self.assertIn("window", action.text.lower())
        self.assertEqual(ctx.enum_rebuttals, 2)          # bounded at 2
        self.assertLess(ctx.plan_round, MAX_PLAN_ROUNDS)  # nowhere near 16
        # the invalid call was never emitted to the evaluator
        emitted = ledger.get_state_changing_tools_called()
        self.assertNotIn("open_close_window", emitted)
        # a corrective note listing the allowed values was produced
        self.assertTrue(any("INVALID-ARGUMENT" in n for n in ctx.policy_notes))

    def test_valid_enum_passes_null_fp(self):
        # Fix 6: schema requires ("window", "percentage") — both need to be
        # supplied so the required-params guard does not force a re-plan.
        ledger, ctx = _ctx()
        fake = FakeLLM(intents=[_intent()], plans=[_plan("ALL", percentage=50)])
        machine = StateMachine()
        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)

        self.assertIsInstance(action, EmitToolCalls)
        self.assertEqual([c.tool for c in action.calls], ["open_close_window"])
        self.assertEqual(action.calls[0].arguments["window"], "ALL")
        self.assertEqual(ctx.enum_rebuttals, 0)


# ---------------------------------------------------------------------------
# Cross-turn tool-execution-error retry bound
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# OI-016 Fix A — unknown-argument guard in the plan-execute loop
# ---------------------------------------------------------------------------

AMBIENT_TOOLS = [
    {"function": {
        "name": "set_ambient_lights",
        "description": "Turn ambient lights on/off and set the color.",
        "parameters": {
            "properties": {
                "on": {"type": "boolean"},
                "lightcolor": {
                    "type": "string",
                    "enum": ["RED", "PURPLE", "BLUE", "GREEN", "WHITE"],
                },
            },
            "required": ["on", "lightcolor"],
        },
    }},
]


class UnknownArgumentGuardTest(unittest.TestCase):
    def _ctx(self):
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("Set the ambient lights to my usual color.")
        return ledger, TurnContext(ledger=ledger, tools=AMBIENT_TOOLS, model="fake")

    def _intent(self):
        return Intent(
            user_request_summary="Set ambient light color",
            required_tools=["set_ambient_lights"],
            is_state_changing=True, is_ambiguous=False, value_ambiguities=[])

    def test_strips_non_schema_argument_and_emits_valid_call(self):
        # planner adds a hallucinated duplicate `color` alongside `lightcolor`
        ledger, ctx = self._ctx()
        plan = Plan(steps=[PlanStep(
            tool="set_ambient_lights",
            arguments_json=json.dumps(
                {"lightcolor": "PURPLE", "color": "PURPLE", "on": True}))])
        fake = FakeLLM(intents=[self._intent()], plans=[plan])
        machine = StateMachine()
        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)

        self.assertIsInstance(action, EmitToolCalls)
        self.assertEqual([c.tool for c in action.calls], ["set_ambient_lights"])
        args = action.calls[0].arguments
        self.assertEqual(args, {"lightcolor": "PURPLE", "on": True})  # `color` stripped
        self.assertNotIn("color", args)
        # the strip is traceable, never silent
        self.assertTrue(any("stripped unknown argument 'color'" in n
                            for n in ctx.policy_notes))
        self.assertTrue(any(d.layer == "ArgumentSchema.unknown"
                            for d in ctx.layer_decisions))

    def test_only_valid_arguments_pass_unchanged_null_fp(self):
        ledger, ctx = self._ctx()
        plan = Plan(steps=[PlanStep(
            tool="set_ambient_lights",
            arguments_json=json.dumps({"lightcolor": "PURPLE", "on": True}))])
        fake = FakeLLM(intents=[self._intent()], plans=[plan])
        machine = StateMachine()
        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)

        self.assertIsInstance(action, EmitToolCalls)
        self.assertEqual(action.calls[0].arguments, {"lightcolor": "PURPLE", "on": True})
        self.assertFalse(any(d.layer == "ArgumentSchema.unknown"
                             for d in ctx.layer_decisions))


class RetryBoundWiringTest(unittest.TestCase):
    def test_identical_failed_call_is_not_retried(self):
        ledger, ctx = _ctx()
        # an earlier turn already tried this exact call and the tool rejected it
        ledger.add_tool_call(
            "open_close_window", {"window": "ALL", "percentage": 50}, "prev")
        ledger.add_tool_result("open_close_window", json.dumps({
            "status": "FAILURE",
            "errors": {"OPEN_CLOSE_WINDOW_003": "Invalid window requested"},
        }), "prev")

        # the planner produces the identical (valid-enum) call again
        fake = FakeLLM(intents=[_intent()], plans=[_plan("ALL", 50)])
        machine = StateMachine()
        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)

        self.assertIsInstance(action, EmitText)
        self.assertIn("failing", action.text.lower())
        # the failed call was not emitted a second time — only the seeded one exists
        window_calls = [e for e in ledger.get_tool_calls_this_turn()
                        if e.tool_name == "open_close_window"]
        self.assertEqual(len(window_calls), 1)


if __name__ == "__main__":
    unittest.main()
