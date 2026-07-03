"""Stufe-2 unit tests: resumable state machine, determinism, idempotency.

All LLM calls are faked (scripted schema instances), so the tests are fully
deterministic and run without API keys. The real prompt-module code paths
(arguments_json parsing, transcript rendering) are exercised.
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
from track_1_agent_under_test.glassbox import state_machine as sm
from track_1_agent_under_test.glassbox.prompts.intake import Intent
from track_1_agent_under_test.glassbox.prompts.plan import Plan, PlanStep
from track_1_agent_under_test.glassbox.prompts.verify import Draft


TOOLS = [
    {"function": {
        "name": "get_weather",
        "description": "Get current weather.",
        "parameters": {"properties": {"location": {"type": "string"}},
                       "required": ["location"]},
    }},
    {"function": {
        "name": "open_close_sunroof",
        "description": "Open or close the sunroof.",
        "parameters": {"properties": {"position": {"type": "string"}},
                       "required": ["position"]},
    }},
]


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

    def __init__(self, intents=(), plans=(), drafts=()):
        self.queues = {
            "Intent": list(intents),
            "Plan": list(plans),
            "Draft": list(drafts),
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


def run_scripted(fake: FakeLLM):
    """Drive one full turn; simulate the evaluator answering tool calls."""
    machine = StateMachine()
    ctx = new_ctx()
    trajectory = []
    with patch.object(glassbox_llm, "call_structured", fake):
        action = machine.run_turn(ctx)
        while isinstance(action, EmitToolCalls):
            trajectory.append([(c.tool, c.arguments, c.call_id) for c in action.calls])
            for c in action.calls:
                ctx.ledger.add_tool_result(c.tool, f"ok:{c.tool}", c.call_id)
            action = machine.resume(ctx)
    return ctx, trajectory, action


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
        # fixed state sequence, resumed at PLAN after each result batch
        self.assertEqual(ctx.state_trace, [
            "INTAKE", "CAPABILITY_CHECK",
            "PLAN", "POLICY_CHECK", "EXECUTE",
            "PLAN", "POLICY_CHECK", "EXECUTE",
            "PLAN", "VERIFY", "RESPOND", "DONE",
        ])
        # ledger provenance: 2 calls, 2 results, final agent response
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


class IdempotencyTest(unittest.TestCase):
    def test_duplicate_step_is_skipped(self):
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[
                Plan(steps=[step("get_weather", {"location": "current"})]),
                # planner repeats the identical call plus one new call
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
                # planner loops: only the already-executed call again
                Plan(steps=[step("get_weather", {"location": "current"})]),
            ],
            drafts=[Draft(response="The weather looks fine.")],
        )
        _, trajectory, action = run_scripted(fake)
        self.assertEqual(len(trajectory), 1)
        self.assertIsInstance(action, EmitText)
        self.assertEqual(fake.calls.count("Plan"), 2)

    def test_max_plan_rounds_bound(self):
        # planner always produces a new distinct call → bound must stop it
        plans = [Plan(steps=[step("get_weather", {"location": f"city{i}"})])
                 for i in range(sm.MAX_PLAN_ROUNDS + 5)]
        fake = FakeLLM(intents=[intent_ok()], plans=plans,
                       drafts=[Draft(response="Stopped.")])
        _, trajectory, action = run_scripted(fake)
        self.assertEqual(len(trajectory), sm.MAX_PLAN_ROUNDS)
        self.assertIsInstance(action, EmitText)


class SafetyPathTest(unittest.TestCase):
    def test_unknown_tool_yields_honest_refusal(self):
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[Plan(steps=[step("fly_to_moon", {"speed": "fast"})])],
        )
        ctx, trajectory, action = run_scripted(fake)
        self.assertEqual(trajectory, [])
        self.assertIsInstance(action, EmitText)
        self.assertEqual(action.text, sm.FALLBACK_UNAVAILABLE)
        self.assertEqual(
            [e for e in ctx.ledger.entries if e.kind == "tool_call"], [])

    def test_unknown_parameter_yields_honest_refusal(self):
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[Plan(steps=[step("get_weather", {"nonexistent_param": 1})])],
        )
        _, trajectory, action = run_scripted(fake)
        self.assertEqual(trajectory, [])
        self.assertEqual(action.text, sm.FALLBACK_UNAVAILABLE)

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
        self.assertEqual(fake.calls, ["Intent"])  # no plan, no draft
        self.assertIn("CLARIFY", ctx.state_trace)


class PlanStepSchemaTest(unittest.TestCase):
    def test_arguments_json_must_be_object(self):
        with self.assertRaises(ValueError):
            PlanStep(tool="x", arguments_json="[1,2]")
        with self.assertRaises(ValueError):
            PlanStep(tool="x", arguments_json="not json")


if __name__ == "__main__":
    unittest.main()
