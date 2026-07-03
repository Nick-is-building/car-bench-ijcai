"""Stufe-2+3 unit tests: resumable state machine, capability check, determinism.

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
    CapabilityMatcher,
    EmitText,
    EmitToolCalls,
    Ledger,
    StateMachine,
    TurnContext,
)
from track_1_agent_under_test.glassbox import llm as glassbox_llm
from track_1_agent_under_test.glassbox import state_machine as sm
from track_1_agent_under_test.glassbox.prompts.intake import Intent
from track_1_agent_under_test.glassbox.prompts.plan import Plan, PlanStep
from track_1_agent_under_test.glassbox.prompts.verify import Draft
from track_1_agent_under_test.glassbox.prompts.capability_check import Refusal


TOOLS = [
    {"function": {
        "name": "get_weather",
        "description": "Get current weather.",
        "parameters": {"properties": {"location": {"type": "string"}},
                       "required": ["location"]},
    }},
    {"function": {
        "name": "open_close_sunshade",
        "description": "Open or close the sunshade.",
        "parameters": {"properties": {"percentage": {"type": "number"}},
                       "required": ["percentage"]},
    }},
    {"function": {
        "name": "open_close_sunroof",
        "description": "Open or close the sunroof.",
        "parameters": {"properties": {"position": {"type": "string"}},
                       "required": ["position"]},
    }},
]

TOOLS_NO_SUNSHADE = [t for t in TOOLS if t["function"]["name"] != "open_close_sunshade"]

FAKE_REFUSAL = Refusal(response="Sorry, I'm unable to do that with the controls available.")


def step(tool: str, args: dict) -> PlanStep:
    return PlanStep(tool=tool, arguments_json=json.dumps(args, sort_keys=True))


def intent_ok() -> Intent:
    return Intent(
        user_request_summary="Open the sunroof",
        required_tools=["open_close_sunroof"],
        is_state_changing=True,
        is_ambiguous=False,
    )


class FakeLLM:
    """Scripted replacement for llm.call_structured, keyed by schema name."""

    def __init__(self, intents=(), plans=(), drafts=(), refusals=()):
        self.queues = {
            "Intent": list(intents),
            "Plan": list(plans),
            "Draft": list(drafts),
            "Refusal": list(refusals),
        }
        self.calls: list[str] = []

    def __call__(self, messages, schema, model=None, system=None, tools=None,
                 temperature=0.0):
        name = schema.__name__
        self.calls.append(name)
        if not self.queues.get(name):
            raise AssertionError(f"unexpected LLM call for schema {name}")
        return self.queues[name].pop(0)


def new_ctx() -> TurnContext:
    ledger = Ledger()
    ledger.add_system("You are a car assistant. Follow the policies.")
    ledger.add_user_turn("Open the sunroof, please.")
    return TurnContext(ledger=ledger, tools=TOOLS, model="fake")


def run_scripted(fake: FakeLLM, tools=None):
    """Drive one full turn; simulate the evaluator answering tool calls."""
    machine = StateMachine()
    base = new_ctx()
    ctx = TurnContext(ledger=base.ledger, tools=tools if tools is not None else TOOLS, model="fake")
    trajectory = []
    with patch.object(glassbox_llm, "call_structured", fake):
        action = machine.run_turn(ctx)
        while isinstance(action, EmitToolCalls):
            trajectory.append([(c.tool, c.arguments, c.call_id) for c in action.calls])
            for c in action.calls:
                ctx.ledger.add_tool_result(c.tool, f"ok:{c.tool}", c.call_id)
            action = machine.resume(ctx)
    return ctx, trajectory, action


# ---------------------------------------------------------------------------
# Stufe 3 — CapabilityMatcher.check() unit tests (no LLM, deterministic)
# ---------------------------------------------------------------------------

class CapabilityMatcherTest(unittest.TestCase):
    """Pure unit tests for CapabilityMatcher.check() — no state machine involved."""

    def matcher(self):
        return CapabilityMatcher(TOOLS)

    def test_covered_simple(self):
        m = self.matcher()
        intent = {"required_tools": ["get_weather"], "required_params": [],
                  "is_ambiguous": False}
        self.assertEqual(m.check(intent), "covered")

    def test_covered_with_known_param(self):
        m = self.matcher()
        intent = {
            "required_tools": ["get_weather"],
            "required_params": [{"tool": "get_weather", "params": ["location"]}],
            "is_ambiguous": False,
        }
        self.assertEqual(m.check(intent), "covered")

    def test_covered_empty_intent(self):
        m = self.matcher()
        self.assertEqual(m.check({"required_tools": [], "required_params": [],
                                   "is_ambiguous": False}), "covered")

    def test_uncovered_missing_tool(self):
        m = self.matcher()
        intent = {"required_tools": ["fly_to_moon"], "required_params": [],
                  "is_ambiguous": False}
        self.assertEqual(m.check(intent), "uncovered")

    def test_uncovered_missing_parameter(self):
        m = self.matcher()
        intent = {
            "required_tools": ["get_weather"],
            "required_params": [{"tool": "get_weather", "params": ["nonexistent_param"]}],
            "is_ambiguous": False,
        }
        self.assertEqual(m.check(intent), "uncovered")

    def test_uncovered_required_but_missing_tool(self):
        """required_but_missing_tools set → uncovered even if required_tools all exist."""
        m = CapabilityMatcher(TOOLS_NO_SUNSHADE)  # sunshade absent from catalog
        intent = {
            "required_tools": ["open_close_sunroof"],
            "required_params": [],
            "required_but_missing_tools": ["open_close_sunshade"],
            "is_ambiguous": False,
        }
        self.assertEqual(m.check(intent), "uncovered")

    def test_ambiguous(self):
        m = self.matcher()
        intent = {"required_tools": [], "required_params": [],
                  "is_ambiguous": True}
        self.assertEqual(m.check(intent), "ambiguous")

    def test_ambiguous_takes_priority_over_missing_tool(self):
        m = self.matcher()
        intent = {"required_tools": ["fly_to_moon"], "required_params": [],
                  "is_ambiguous": True}
        self.assertEqual(m.check(intent), "ambiguous")


# ---------------------------------------------------------------------------
# Stufe 3 — integration: intake-time capability check triggers refusal path
# ---------------------------------------------------------------------------

class CapabilityCheckIntegrationTest(unittest.TestCase):

    def test_intake_missing_required_tool_yields_llm_refusal(self):
        """Intent lists a tool absent from the index → CAPABILITY_CHECK → refusal."""
        fake = FakeLLM(
            intents=[Intent(
                user_request_summary="Fly to the moon",
                required_tools=["fly_to_moon"],
                is_state_changing=True,
                is_ambiguous=False,
            )],
            refusals=[FAKE_REFUSAL],
        )
        ctx, trajectory, action = run_scripted(fake)
        self.assertEqual(trajectory, [])
        self.assertIsInstance(action, EmitText)
        self.assertEqual(action.text, FAKE_REFUSAL.response)
        self.assertIn("CAPABILITY_CHECK", ctx.state_trace)
        self.assertNotIn("PLAN", ctx.state_trace)

    def test_intake_missing_but_required_tool_yields_llm_refusal(self):
        """required_but_missing_tools non-empty → CAPABILITY_CHECK → refusal."""
        fake = FakeLLM(
            intents=[Intent(
                user_request_summary="Open the sunroof",
                required_tools=["open_close_sunroof"],
                required_but_missing_tools=["open_close_sunshade"],
                is_state_changing=True,
                is_ambiguous=False,
            )],
            refusals=[FAKE_REFUSAL],
        )
        ctx, trajectory, action = run_scripted(fake, tools=TOOLS_NO_SUNSHADE)
        self.assertEqual(trajectory, [])
        self.assertIsInstance(action, EmitText)
        self.assertEqual(action.text, FAKE_REFUSAL.response)
        self.assertEqual(ctx.capability_result, "uncovered")

    def test_planner_capability_missing_signal_yields_refusal(self):
        """Planner sets capability_missing=True → state machine routes to refusal."""
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[Plan(steps=[], capability_missing=True,
                        done_reason="missing_capability: open_close_sunshade")],
            refusals=[FAKE_REFUSAL],
        )
        ctx, trajectory, action = run_scripted(fake)
        self.assertEqual(trajectory, [])
        self.assertIsInstance(action, EmitText)
        self.assertEqual(action.text, FAKE_REFUSAL.response)
        self.assertTrue(ctx.capability_missing)

    def test_aut_pol_005_sunroof_without_sunshade_yields_refusal(self):
        """AUT-POL:005 guard: sunroof planned, open_close_sunshade absent → capability_missing refusal."""
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("Open the sunroof please.")
        ctx = TurnContext(ledger=ledger, tools=TOOLS_NO_SUNSHADE, model="fake")
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[Plan(steps=[step("open_close_sunroof", {"position": "open"})])],
            refusals=[FAKE_REFUSAL],
        )
        machine = StateMachine()
        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)
        self.assertIsInstance(action, EmitText)
        self.assertEqual(action.text, FAKE_REFUSAL.response)
        self.assertTrue(ctx.capability_missing)

    def test_execute_time_missing_tool_yields_refusal(self):
        """check_step catches tool absent from index during EXECUTE → refusal."""
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[Plan(steps=[step("fly_to_moon", {"speed": "fast"})])],
            refusals=[FAKE_REFUSAL],
        )
        ctx, trajectory, action = run_scripted(fake)
        self.assertEqual(trajectory, [])
        self.assertIsInstance(action, EmitText)
        self.assertEqual(action.text, FAKE_REFUSAL.response)

    def test_execute_time_missing_param_yields_refusal(self):
        """check_step catches missing param during EXECUTE → refusal."""
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[Plan(steps=[step("get_weather", {"nonexistent_param": 1})])],
            refusals=[FAKE_REFUSAL],
        )
        _, trajectory, action = run_scripted(fake)
        self.assertEqual(trajectory, [])
        self.assertIsInstance(action, EmitText)
        self.assertEqual(action.text, FAKE_REFUSAL.response)


# ---------------------------------------------------------------------------
# Stufe 2 — happy path (unchanged behaviour)
# ---------------------------------------------------------------------------

class HappyPathTest(unittest.TestCase):
    def make_fake(self) -> FakeLLM:
        return FakeLLM(
            intents=[intent_ok()],
            plans=[
                Plan(steps=[step("get_weather", {"location": "current"})]),
                Plan(steps=[step("open_close_sunroof", {"position": "open"})]),
                Plan(steps=[], done_reason="request fulfilled"),
            ],
            drafts=[Draft(response="Sunroof is open, enjoy the sunshine!")],
        )

    def test_full_turn_trajectory(self):
        ctx, trajectory, action = run_scripted(self.make_fake())

        self.assertIsInstance(action, EmitText)
        self.assertEqual(action.text, "Sunroof is open, enjoy the sunshine!")
        self.assertEqual(trajectory, [
            [("get_weather", {"location": "current"}, "call_t1_r1_s0")],
            [("open_close_sunroof", {"position": "open"}, "call_t1_r2_s0")],
        ])
        self.assertEqual(ctx.state_trace, [
            "INTAKE", "CAPABILITY_CHECK",
            "PLAN", "POLICY_CHECK", "EXECUTE",
            "PLAN", "POLICY_CHECK", "EXECUTE",
            "PLAN", "VERIFY", "RESPOND", "DONE",
        ])
        kinds = [e.kind for e in ctx.ledger.entries]
        self.assertEqual(kinds.count("tool_call"), 2)
        self.assertEqual(kinds.count("tool_result"), 2)
        self.assertEqual(kinds[-1], "agent")

    def test_identical_trajectories_across_runs(self):
        results = [run_scripted(self.make_fake()) for _ in range(3)]
        traces = [ctx.state_trace for ctx, _, _ in results]
        trajectories = [t for _, t, _ in results]
        finals = [a.text for _, _, a in results]
        self.assertEqual(traces[0], traces[1])
        self.assertEqual(traces[1], traces[2])
        self.assertEqual(trajectories[0], trajectories[1])
        self.assertEqual(trajectories[1], trajectories[2])
        self.assertEqual(finals[0], finals[1])
        self.assertEqual(finals[1], finals[2])


# ---------------------------------------------------------------------------
# Stufe 2 — idempotency and bounds (unchanged behaviour)
# ---------------------------------------------------------------------------

class IdempotencyTest(unittest.TestCase):
    def test_duplicate_step_is_skipped(self):
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[
                Plan(steps=[step("get_weather", {"location": "current"})]),
                Plan(steps=[
                    step("get_weather", {"location": "current"}),
                    step("open_close_sunroof", {"position": "open"}),
                ]),
                Plan(steps=[]),
            ],
            drafts=[Draft(response="Done.")],
        )
        _, trajectory, action = run_scripted(fake)
        self.assertEqual([[c[0] for c in batch] for batch in trajectory],
                         [["get_weather"], ["open_close_sunroof"]])
        self.assertIsInstance(action, EmitText)

    def test_planner_loop_of_duplicates_ends_turn(self):
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[
                Plan(steps=[step("get_weather", {"location": "current"})]),
                Plan(steps=[step("get_weather", {"location": "current"})]),
            ],
            drafts=[Draft(response="The weather looks fine.")],
        )
        _, trajectory, action = run_scripted(fake)
        self.assertEqual(len(trajectory), 1)
        self.assertIsInstance(action, EmitText)
        self.assertEqual(fake.calls.count("Plan"), 2)

    def test_max_plan_rounds_bound(self):
        plans = [Plan(steps=[step("get_weather", {"location": f"city{i}"})])
                 for i in range(sm.MAX_PLAN_ROUNDS + 5)]
        fake = FakeLLM(intents=[intent_ok()], plans=plans,
                       drafts=[Draft(response="Stopped.")])
        ctx, trajectory, action = run_scripted(fake)
        self.assertEqual(len(trajectory), sm.MAX_PLAN_ROUNDS)
        self.assertIsInstance(action, EmitText)
        self.assertTrue(ctx.plan_bound_hit)

    def test_bound_not_flagged_on_normal_completion(self):
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[
                Plan(steps=[step("get_weather", {"location": "current"})]),
                Plan(steps=[], done_reason="done"),
            ],
            drafts=[Draft(response="Done.")],
        )
        ctx, _, action = run_scripted(fake)
        self.assertIsInstance(action, EmitText)
        self.assertFalse(ctx.plan_bound_hit)

    def test_bound_allows_nine_sequential_actions(self):
        plans = [Plan(steps=[step("open_close_sunroof", {"position": f"{i*10}"})])
                 for i in range(9)]
        plans.append(Plan(steps=[], done_reason="all nine done"))
        fake = FakeLLM(intents=[intent_ok()], plans=plans,
                       drafts=[Draft(response="All done.")])
        ctx, trajectory, action = run_scripted(fake)
        self.assertEqual(len(trajectory), 9)
        self.assertIsInstance(action, EmitText)
        self.assertFalse(ctx.plan_bound_hit)


# ---------------------------------------------------------------------------
# Stufe 2 — clarification path (unchanged behaviour)
# ---------------------------------------------------------------------------

class SafetyPathTest(unittest.TestCase):
    def test_ambiguous_intent_asks_intake_question(self):
        fake = FakeLLM(
            intents=[Intent(
                user_request_summary="Open something",
                is_state_changing=True,
                is_ambiguous=True,
                ambiguity_reason="sunroof or window unclear",
                clarification_question="Do you want the sunroof or the window opened?",
            )],
        )
        ctx, trajectory, action = run_scripted(fake)
        self.assertEqual(trajectory, [])
        self.assertIsInstance(action, EmitText)
        self.assertEqual(action.text,
                         "Do you want the sunroof or the window opened?")
        self.assertEqual(fake.calls, ["Intent"])
        self.assertIn("CLARIFY", ctx.state_trace)


class PlanStepSchemaTest(unittest.TestCase):
    def test_arguments_json_must_be_object(self):
        with self.assertRaises(ValueError):
            PlanStep(tool="x", arguments_json="[1,2]")
        with self.assertRaises(ValueError):
            PlanStep(tool="x", arguments_json="not json")


if __name__ == "__main__":
    unittest.main()
