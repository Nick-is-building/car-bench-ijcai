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
FAKE_DRAFT = Draft(response="Done, all set.")


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
        """Verified claim (named tool truly absent) → refusal."""
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[Plan(steps=[], capability_missing=True,
                        missing_tools=["open_close_sunshade"],
                        done_reason="missing_capability: open_close_sunshade")],
            refusals=[FAKE_REFUSAL],
        )
        ctx, trajectory, action = run_scripted(fake, tools=TOOLS_NO_SUNSHADE)
        self.assertEqual(trajectory, [])
        self.assertIsInstance(action, EmitText)
        self.assertEqual(action.text, FAKE_REFUSAL.response)
        self.assertTrue(ctx.capability_missing)

    def test_false_capability_claim_is_rebutted_and_replanned(self):
        """B6 root cause: claim names a tool that EXISTS → no refusal, re-plan."""
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[
                Plan(steps=[], capability_missing=True,
                     missing_tools=["open_close_sunroof"]),
                Plan(steps=[step("open_close_sunroof", {"position": "open"})]),
                Plan(steps=[], done_reason="done"),
            ],
            drafts=[FAKE_DRAFT],
        )
        ctx, trajectory, action = run_scripted(fake)
        self.assertFalse(ctx.capability_missing)
        self.assertEqual(ctx.capability_rebuttals, 1)
        self.assertTrue(any("PLAN-GUARD" in n for n in ctx.policy_notes))
        self.assertEqual([c[0] for batch in trajectory for c in batch],
                         ["open_close_sunroof"])
        self.assertIsInstance(action, EmitText)

    def test_unnamed_capability_claim_is_bounded_then_turn_ends(self):
        """Repeated unverifiable claims never refuse and never loop forever."""
        claim = Plan(steps=[], capability_missing=True)  # no missing_tools
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[claim, claim.model_copy(deep=True), claim.model_copy(deep=True)],
            drafts=[FAKE_DRAFT],
        )
        ctx, trajectory, action = run_scripted(fake)
        self.assertFalse(ctx.capability_missing)
        self.assertEqual(ctx.capability_rebuttals, 2)
        self.assertEqual(trajectory, [])
        self.assertIsInstance(action, EmitText)  # honest verify, not refusal
        self.assertNotEqual(action.text, FAKE_REFUSAL.response)

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


# ---------------------------------------------------------------------------
# A1.1 — Mid-Conversation-Entziehung: CapabilityIndex pro Turn neu gebaut
# ---------------------------------------------------------------------------

TOOLS_NO_SUNROOF = [t for t in TOOLS if t["function"]["name"] != "open_close_sunroof"]


class MidConversationToolRemovalTest(unittest.TestCase):
    """Verify that CapabilityIndex is rebuilt from the *current* tool list on
    each new user turn (new TurnContext), so mid-conversation tool withdrawal
    is caught deterministically without any LLM call."""

    def test_capability_index_rebuilt_per_turn_not_cached_on_machine(self):
        """StateMachine holds no cached CapabilityMatcher — each run_turn/
        resume call creates a fresh index from ctx.tools.

        Turn 1: sunroof present → full happy-path completes.
        Turn 2: new TurnContext with sunroof absent → CAPABILITY_CHECK → refusal.
        """
        machine = StateMachine()

        # Turn 1: sunroof present → execute, then verify/respond
        fake1 = FakeLLM(
            intents=[intent_ok()],
            plans=[
                Plan(steps=[step("open_close_sunroof", {"position": "open"})]),
                Plan(steps=[], done_reason="done"),
            ],
            drafts=[Draft(response="Sunroof opened.")],
        )
        ledger1 = Ledger()
        ledger1.add_system("You are a car assistant.")
        ledger1.add_user_turn("Open the sunroof.")
        ctx1 = TurnContext(ledger=ledger1, tools=TOOLS, model="fake")
        with patch.object(glassbox_llm, "call_structured", fake1):
            action1 = machine.run_turn(ctx1)
            self.assertIsInstance(action1, EmitToolCalls)
            for c in action1.calls:
                ctx1.ledger.add_tool_result(c.tool, "ok", c.call_id)
            action1 = machine.resume(ctx1)
        self.assertIsInstance(action1, EmitText)
        self.assertEqual(action1.text, "Sunroof opened.")

        # Turn 2: sunroof removed from catalog → new TurnContext → fresh index
        fake2 = FakeLLM(
            intents=[intent_ok()],
            refusals=[FAKE_REFUSAL],
        )
        ledger2 = Ledger()
        ledger2.add_system("You are a car assistant.")
        ledger2.add_user_turn("Open the sunroof again.")
        ctx2 = TurnContext(ledger=ledger2, tools=TOOLS_NO_SUNROOF, model="fake")
        with patch.object(glassbox_llm, "call_structured", fake2):
            action2 = machine.run_turn(ctx2)
        self.assertIsInstance(action2, EmitText)
        self.assertEqual(action2.text, FAKE_REFUSAL.response)
        self.assertEqual(ctx2.capability_result, "uncovered")

    def test_resume_uses_ctx_tools_not_stale_first_turn_tools(self):
        """Within a turn: resume() builds matcher from ctx.tools.
        If caller updates ctx.tools before resume(), the new index takes effect.

        Flow: run_turn emits get_weather call (succeeds). Before resume, caller
        removes open_close_sunroof from ctx.tools. Planner then requests
        open_close_sunroof → check_step sees it as uncovered → refusal.
        """
        machine = StateMachine()
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[
                Plan(steps=[step("get_weather", {"location": "here"})]),
                Plan(steps=[step("open_close_sunroof", {"position": "open"})]),
            ],
            refusals=[FAKE_REFUSAL],
        )
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("Open the sunroof.")
        ctx = TurnContext(ledger=ledger, tools=TOOLS, model="fake")
        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)
            self.assertIsInstance(action, EmitToolCalls)
            for c in action.calls:
                ctx.ledger.add_tool_result(c.tool, "ok", c.call_id)
            # Simulate evaluator withdrawing open_close_sunroof before resume
            ctx.tools = TOOLS_NO_SUNROOF
            action = machine.resume(ctx)
        # Planner wanted open_close_sunroof but it's gone from updated index → refusal
        self.assertIsInstance(action, EmitText)
        self.assertEqual(action.text, FAKE_REFUSAL.response)


# ---------------------------------------------------------------------------
# A1.2 — Result-Feld-Entzug: dokumentierter Stub-Test (OI-001)
#
# Tool-Schemas definieren kein responses/result-Schema. has_result_field()
# kann daher NICHT auf Schema-Basis implementiert werden.
# Abdeckung erfolgt über Stufe-5-FabricationGuard (Auftrag C).
# Dieser Test wird grün sobald guard.sanitize() Fabrication blockiert.
# ---------------------------------------------------------------------------

TOOLS_WITH_NAV = TOOLS + [
    {"function": {
        "name": "get_navigation_eta",
        "description": "Get estimated time of arrival.",
        "parameters": {"properties": {"destination": {"type": "string"}},
                       "required": ["destination"]},
    }}
]


@unittest.skip("OI-001: Stufe-5 FabricationGuard not yet implemented — "
               "tool schemas carry no result-field schema, so CapabilityIndex "
               "cannot detect missing result fields; FabricationGuard will catch "
               "fabricated values against the Ledger (Auftrag C).")
class ResultFieldEntzugTest(unittest.TestCase):
    """When the evaluator removes a field from a tool's runtime result
    (hallucination_missing_tool_response task type), the agent must not
    fabricate a concrete value for the missing field."""

    def test_missing_result_field_yields_honest_admission_not_fabricated_value(self):
        """Tool result has 'eta' replaced with 'unknown'. A fabricating Draft
        would claim a concrete ETA — the FabricationGuard must block that and
        produce an honest admission instead."""
        fake = FakeLLM(
            intents=[Intent(
                user_request_summary="What is the ETA to the airport?",
                required_tools=["get_navigation_eta"],
                is_state_changing=False,
                is_ambiguous=False,
            )],
            plans=[
                Plan(steps=[step("get_navigation_eta", {"destination": "airport"})]),
                Plan(steps=[], done_reason="done"),
            ],
            # Draft attempts to fabricate a concrete value for the missing field
            drafts=[Draft(response="The estimated arrival time is 42 minutes.")],
        )
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("How long to the airport?")
        ctx = TurnContext(ledger=ledger, tools=TOOLS_WITH_NAV, model="fake")
        machine = StateMachine()
        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)
            self.assertIsInstance(action, EmitToolCalls)
            for c in action.calls:
                # Evaluator returns result with eta field set to "unknown"
                ctx.ledger.add_tool_result(
                    c.tool,
                    '{"eta": "unknown", "route": "highway"}',
                    c.call_id,
                )
            action = machine.resume(ctx)
        self.assertIsInstance(action, EmitText)
        # Must NOT contain a fabricated concrete time
        self.assertNotIn("42 minutes", action.text)
        # Must contain an honest admission that the information is unavailable
        self.assertTrue(
            any(phrase in action.text.lower() for phrase in
                ["sorry", "unavailable", "don't have", "unable", "unknown"]),
            f"Expected honest admission, got: {action.text!r}",
        )


class OI011FuzzyGateTest(unittest.TestCase):
    """OI-011 hardening: fuzzy PLAN-GUARD (H-R1) and INTAKE-REBUTTAL (H-R2)."""

    def test_fuzzy_plan_guard_invents_close_name_replans_then_succeeds(self):
        """(a) Planner invents close alias → fuzzy re-plan note → LLM uses correct name → no refusal."""
        # "open_close_sunroof_v2" is not in TOOLS but scores ~0.947 against
        # "open_close_sunroof" → above FUZZY_THRESHOLD=0.80 → re-plan, not refusal.
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[
                Plan(capability_missing=True,
                     missing_tools=["open_close_sunroof_v2"],
                     done_reason="missing: open_close_sunroof_v2"),
                Plan(steps=[step("open_close_sunroof", {"position": "open"})]),
                Plan(steps=[], done_reason="done"),
            ],
            drafts=[FAKE_DRAFT],
        )
        ctx, trajectory, action = run_scripted(fake)
        self.assertIsInstance(action, EmitText)
        self.assertNotEqual(action.text, FAKE_REFUSAL.response)
        self.assertFalse(ctx.capability_missing)
        self.assertEqual(ctx.capability_rebuttals, 1)
        self.assertTrue(any("open_close_sunroof_v2" in n for n in ctx.policy_notes))
        executed = [c[0] for batch in trajectory for c in batch]
        self.assertIn("open_close_sunroof", executed)

    def test_fuzzy_plan_guard_genuinely_missing_no_match_refuses_immediately(self):
        """(b) Planner reports tool with no catalog neighbour → immediate refusal (hallucination guard preserved)."""
        # "fly_to_moon" has no fuzzy match in TOOLS → genuine missing → refusal on round 1.
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[Plan(capability_missing=True,
                        missing_tools=["fly_to_moon"],
                        done_reason="missing: fly_to_moon")],
            refusals=[FAKE_REFUSAL],
        )
        ctx, trajectory, action = run_scripted(fake)
        self.assertIsInstance(action, EmitText)
        self.assertEqual(action.text, FAKE_REFUSAL.response)
        self.assertTrue(ctx.capability_missing)
        self.assertEqual(trajectory, [])

    def test_intake_rebuttal_fuzzy_match_re_extracts_correct_intent(self):
        """(c) Intake lists unknown tool with fuzzy match → one re-extract → correct tool used."""
        # First intent has "open_close_sunroof_v2" (not in catalog, fuzzy → "open_close_sunroof").
        # Re-extract returns the corrected intent → capability check passes.
        fake = FakeLLM(
            intents=[
                Intent(
                    user_request_summary="Open the sunroof",
                    required_tools=["open_close_sunroof_v2"],
                    is_state_changing=True,
                    is_ambiguous=False,
                ),
                Intent(
                    user_request_summary="Open the sunroof",
                    required_tools=["open_close_sunroof"],
                    is_state_changing=True,
                    is_ambiguous=False,
                ),
            ],
            plans=[
                Plan(steps=[step("open_close_sunroof", {"position": "open"})]),
                Plan(steps=[], done_reason="done"),
            ],
            drafts=[FAKE_DRAFT],
        )
        ctx, trajectory, action = run_scripted(fake)
        self.assertIsInstance(action, EmitText)
        self.assertNotEqual(action.text, FAKE_REFUSAL.response)
        self.assertTrue(ctx.intake_rebuttal_done)
        executed = [c[0] for batch in trajectory for c in batch]
        self.assertIn("open_close_sunroof", executed)

    def test_intake_rebuttal_no_fuzzy_match_stays_uncovered(self):
        """(d) Intake lists unknown tool with no catalog neighbour → no re-extract → refusal."""
        # "fly_to_moon" has no fuzzy match in TOOLS → intake stays uncovered.
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
        self.assertIsInstance(action, EmitText)
        self.assertEqual(action.text, FAKE_REFUSAL.response)
        self.assertFalse(ctx.intake_rebuttal_done)
        self.assertEqual(ctx.capability_result, "uncovered")
        self.assertEqual(trajectory, [])


if __name__ == "__main__":
    unittest.main()
