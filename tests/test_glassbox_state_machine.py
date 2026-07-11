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
from track_1_agent_under_test.glassbox.guard import (
    ArgumentAttribution,
    AttributionResponse,
    ClaimExtractionResponse,
    FactualClaim,
    GuardResult,
)


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

    def __init__(self, intents=(), plans=(), drafts=(), refusals=(),
                 attributions=(), claims=()):
        self.queues = {
            "Intent": list(intents),
            "Plan": list(plans),
            "Draft": list(drafts),
            "Refusal": list(refusals),
            "AttributionResponse": list(attributions),
            "ClaimExtractionResponse": list(claims),
        }
        self.calls: list[str] = []

    def __call__(self, messages, schema, model=None, system=None, tools=None,
                 temperature=0.0):
        name = schema.__name__
        self.calls.append(name)
        if not self.queues.get(name):
            raise AssertionError(f"unexpected LLM call for schema {name!r}; "
                                 f"queued: {list(self.queues)}, calls so far: {self.calls}")
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

    def test_required_params_not_checked_at_intake(self):
        # required_params is no longer checked in check() — parameter validation
        # happens at execution time via check_step() to avoid INTAKE false positives.
        m = self.matcher()
        intent = {
            "required_tools": ["get_weather"],
            "required_params": [{"tool": "get_weather", "params": ["nonexistent_param"]}],
            "is_ambiguous": False,
        }
        self.assertEqual(m.check(intent), "covered")

    def test_partial_missing_returns_covered_with_confirmed_missing(self):
        """required_but_missing_tools set but required_tools has covered tool → covered."""
        m = CapabilityMatcher(TOOLS_NO_SUNSHADE)  # sunshade absent from catalog
        intent = {
            "required_tools": ["open_close_sunroof"],
            "required_params": [],
            "required_but_missing_tools": ["open_close_sunshade"],
            "is_ambiguous": False,
        }
        self.assertEqual(m.check(intent), "covered")
        self.assertEqual(intent["confirmed_missing_tools"], ["open_close_sunshade"])

    def test_all_missing_returns_uncovered(self):
        """required_but_missing_tools set and no covered required_tools → uncovered."""
        m = CapabilityMatcher(TOOLS_NO_SUNSHADE)
        intent = {
            "required_tools": [],
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

    def test_partial_missing_proceeds_to_plan(self):
        """required_but_missing_tools with covered required_tools → PLAN, not refusal."""
        fake = FakeLLM(
            intents=[Intent(
                user_request_summary="Check the weather",
                required_tools=["get_weather"],
                required_but_missing_tools=["set_fan_speed"],
                is_state_changing=False,
                is_ambiguous=False,
            )],
            plans=[
                Plan(steps=[step("get_weather", {"location": "Berlin"})]),
                Plan(steps=[], done_reason="done"),
            ],
            drafts=[FAKE_DRAFT],
        )
        ctx, trajectory, action = run_scripted(fake)
        self.assertIsInstance(action, EmitText)
        self.assertEqual(ctx.capability_result, "covered")
        self.assertIn("PLAN", ctx.state_trace)
        executed = [c[0] for batch in trajectory for c in batch]
        self.assertIn("get_weather", executed)

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

    def test_execute_time_unknown_param_is_stripped_and_call_proceeds(self):
        """OI-016 Fix A: a non-schema argument is stripped (not refused).

        The call proceeds with only schema-conform args, and the strip is
        visible in the trace (policy note + ArgumentSchema.unknown layer
        decision) — never silently discarded (Lesson 1a).
        """
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[
                Plan(steps=[step("get_weather",
                                 {"location": "current", "nonexistent_param": 1})]),
                Plan(steps=[], done_reason="request fulfilled"),
            ],
            drafts=[FAKE_DRAFT],
        )
        ctx, trajectory, action = run_scripted(fake)
        self.assertEqual(trajectory, [
            [("get_weather", {"location": "current"}, "call_t1_r1_s0")],
        ])
        self.assertIsInstance(action, EmitText)
        self.assertEqual(action.text, FAKE_DRAFT.response)
        self.assertTrue(any("stripped unknown argument 'nonexistent_param'" in n
                            for n in ctx.policy_notes))
        self.assertTrue(any(d.layer == "ArgumentSchema.unknown"
                            for d in ctx.layer_decisions))


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
        open_close_sunroof → check_step sees it as uncovered → would refusal,
        but Fix 1 redirects to VERIFY because work was already done.
        """
        machine = StateMachine()
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[
                Plan(steps=[step("get_weather", {"location": "here"})]),
                Plan(steps=[step("open_close_sunroof", {"position": "open"})]),
            ],
            drafts=[FAKE_DRAFT],
            claims=[ClaimExtractionResponse(claims=[])],
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
        # Tool removed mid-turn but work was done → goes through VERIFY, not raw refusal
        self.assertIsInstance(action, EmitText)
        self.assertIn("VERIFY", ctx.state_trace)


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
            # C5: FabricationGuard claim extractor finds "42 minutes" in the draft
            claims=[ClaimExtractionResponse(claims=[
                FactualClaim(
                    value="42 minutes",
                    sentence="The estimated arrival time is 42 minutes.",
                )
            ])],
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


# ---------------------------------------------------------------------------
# C7 — FabricationGuard fake tests (Auftrag C)
# ---------------------------------------------------------------------------

TOOLS_WITH_SUNSHADE_NUMERIC = [
    {"function": {
        "name": "open_close_sunshade",
        "description": "Open or close the sunshade by percentage.",
        "parameters": {"properties": {"percentage": {"type": "number"}},
                       "required": ["percentage"]},
    }},
    {"function": {
        "name": "open_close_sunroof",
        "description": "Open or close the sunroof.",
        "parameters": {"properties": {"percentage": {"type": "number"}},
                       "required": ["percentage"]},
    }},
]

TOOLS_WITH_ROUTE = [
    {"function": {
        "name": "set_new_navigation",
        "description": "Set a new navigation route.",
        "parameters": {
            "properties": {
                "destination": {"type": "string"},
                "route_type": {"type": "string"},
            },
            "required": ["destination"],
        },
    }},
]


def _make_wrong_binding_attr(tool: str, value: str, source_entity: str) -> AttributionResponse:
    """Attribution that has correct ledger quote but wrong entity (cross-binding)."""
    return AttributionResponse(attributions=[
        ArgumentAttribution(
            argument_name="percentage",
            argument_value=value,
            source_quote=f"open the {source_entity} {value}%",
            target_entity=source_entity,
        )
    ])


def _make_correct_binding_attr(tool_entity: str, value: str) -> AttributionResponse:
    """Attribution that has correct ledger quote and correct entity."""
    return AttributionResponse(attributions=[
        ArgumentAttribution(
            argument_name="percentage",
            argument_value=value,
            source_quote=f"open the {tool_entity} {value}%",
            target_entity=tool_entity,
        )
    ])


class FabricationGuardC7Test(unittest.TestCase):
    """C7 fake tests for FabricationGuard — no real API calls."""

    # --- C7.1: Sunshade-Fall: Wert im Ledger, falsche Bindung → UNCERTAIN → Senke ---

    def test_wrong_binding_escalates_to_honesty_sink(self):
        """open_close_sunshade(50) where user only said 'sunroof 50%' → UNCERTAIN × 3 rounds → sink."""
        # User asked to open the SUNROOF 50% — value 50 is in the ledger but
        # bound to the wrong entity. FabricationGuard should detect this via C3:
        # source quote mentions 'sunroof', not 'sunshade'.
        #
        # State machine: UNCERTAIN → note → re-plan (×2) → sink on 3rd round.
        # Each round: 2 AttributionResponse calls (C3 + C4 unanimity gate).
        wrong_attr = _make_wrong_binding_attr("sunshade", "50", "sunroof")
        # 3 plan rounds × 2 attribution calls per round = 6 AttributionResponse items
        fake = FakeLLM(
            intents=[Intent(
                user_request_summary="Open the sunroof 50%",
                required_tools=["open_close_sunshade"],
                is_state_changing=True,
                is_ambiguous=False,
            )],
            plans=[
                Plan(steps=[step("open_close_sunshade", {"percentage": 50})]),
                Plan(steps=[step("open_close_sunshade", {"percentage": 50})]),
                Plan(steps=[step("open_close_sunshade", {"percentage": 50})]),
            ],
            attributions=[wrong_attr, wrong_attr,  # round 1: C3 + C4
                          wrong_attr, wrong_attr,  # round 2: C3 + C4
                          wrong_attr, wrong_attr], # round 3: C3 + C4 → exhausted
        )
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("open the sunroof 50%")
        ctx = TurnContext(ledger=ledger, tools=TOOLS_WITH_SUNSHADE_NUMERIC, model="fake")
        machine = StateMachine()
        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)
        # Must end in honest text (sink), NOT a tool call to sunshade
        self.assertIsInstance(action, EmitText)
        # No sunshade call executed
        calls_made = [e.tool_name for e in ctx.ledger.entries if e.kind == "tool_call"]
        self.assertNotIn("open_close_sunshade", calls_made)
        # Provenance rebuttals maxed out
        self.assertEqual(ctx.provenance_rebuttals, 2)
        # Telemetry records UNCERTAIN decisions from FabricationGuard
        uncertain_decisions = [
            d for d in ctx.layer_decisions
            if d.verdict == "UNCERTAIN" and "FabricationGuard" in d.layer
        ]
        self.assertGreater(len(uncertain_decisions), 0)

    # --- C7.2: Korrekt gebundener Wert → PASS (Null-FP!) ---

    def test_correct_binding_passes_without_block(self):
        """open_close_sunshade(50) where user said 'sunshade 50%' → PASS → tool executed."""
        correct_attr = _make_correct_binding_attr("sunshade", "50")
        # 1 plan round: 1 attribution call (C3 passes → no C4 needed)
        fake = FakeLLM(
            intents=[Intent(
                user_request_summary="Open the sunshade 50%",
                required_tools=["open_close_sunshade"],
                is_state_changing=True,
                is_ambiguous=False,
            )],
            plans=[
                Plan(steps=[step("open_close_sunshade", {"percentage": 50})]),
                Plan(steps=[], done_reason="done"),
            ],
            drafts=[FAKE_DRAFT],
            attributions=[correct_attr],  # C3 → PASS → no C4
            claims=[ClaimExtractionResponse(claims=[])],  # C5: no unsupported claims
        )
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("open the sunshade 50%")
        ctx = TurnContext(ledger=ledger, tools=TOOLS_WITH_SUNSHADE_NUMERIC, model="fake")
        machine = StateMachine()
        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)
            # First action should be EmitToolCalls (sunshade call)
            self.assertIsInstance(action, EmitToolCalls)
            self.assertEqual(action.calls[0].tool, "open_close_sunshade")
            for c in action.calls:
                ctx.ledger.add_tool_result(c.tool, '{"status": "ok"}', c.call_id)
            action = machine.resume(ctx)
        # Should complete successfully with text response
        self.assertIsInstance(action, EmitText)
        self.assertEqual(ctx.provenance_rebuttals, 0)
        # Telemetry: FabricationGuard.C3 recorded PASS
        pass_decisions = [
            d for d in ctx.layer_decisions
            if d.verdict == "PASS" and "FabricationGuard.C3" in d.layer
        ]
        self.assertEqual(len(pass_decisions), 1)

    # --- C7.3: Draft mit erfundener Zahl → Satz ersetzt ---

    def test_draft_with_invented_number_gets_sentence_replaced(self):
        """Sanitize replaces a sentence containing a value not in the ledger."""
        from track_1_agent_under_test.glassbox.guard import FabricationGuard
        from track_1_agent_under_test.glassbox.ledger import Ledger as _Ledger

        ledger = _Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("What is the temperature?")
        ledger.add_tool_result("get_climate", '{"temp": "unknown"}', "call_1")

        fake = FakeLLM(
            claims=[ClaimExtractionResponse(claims=[
                FactualClaim(
                    value="22°C",
                    sentence="The current temperature is 22°C.",
                )
            ])],
        )
        draft = "The current temperature is 22°C."
        with patch.object(glassbox_llm, "call_structured", fake):
            result = FabricationGuard().sanitize(draft, ledger, model="fake")
        self.assertNotIn("22°C", result)
        self.assertIn("sorry", result.lower())

    # --- C7.4: Pflicht-Erwähnung fehlt → Satz ergänzt ---

    def test_route_choice_mention_added_when_missing(self):
        """If navigation call in ledger but draft omits route choice → sentence appended."""
        from track_1_agent_under_test.glassbox.guard import FabricationGuard
        from track_1_agent_under_test.glassbox.ledger import Ledger as _Ledger

        ledger = _Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("Navigate to the airport.")
        ledger.add_tool_call("set_new_navigation", {"destination": "airport"}, "call_1")
        ledger.add_tool_result("set_new_navigation", '{"status": "ok"}', "call_1")

        fake = FakeLLM(
            claims=[ClaimExtractionResponse(claims=[])],  # no unsupported claims
        )
        draft = "Navigation to the airport has been set."
        with patch.object(glassbox_llm, "call_structured", fake):
            result = FabricationGuard().sanitize(draft, ledger, model="fake")
        self.assertIn("fastest", result.lower())

    # --- C7.5: Korrekte Route — keine spurlose Ergänzung wenn bereits erwähnt ---

    def test_route_choice_not_added_when_already_mentioned(self):
        """No duplicate route mention when draft already says 'fastest'."""
        from track_1_agent_under_test.glassbox.guard import FabricationGuard
        from track_1_agent_under_test.glassbox.ledger import Ledger as _Ledger

        ledger = _Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("Navigate to the airport.")
        ledger.add_tool_call("set_new_navigation", {"destination": "airport"}, "call_1")
        ledger.add_tool_result("set_new_navigation", '{"status": "ok"}', "call_1")

        fake = FakeLLM(
            claims=[ClaimExtractionResponse(claims=[])],
        )
        draft = "I've set the fastest route to the airport."
        with patch.object(glassbox_llm, "call_structured", fake):
            result = FabricationGuard().sanitize(draft, ledger, model="fake")
        # Should not duplicate "fastest"
        self.assertEqual(result.lower().count("fastest"), 1)

    # --- C7.6: Telemetrie enthält Schicht + Urteil für jeden Fall ---

    def test_telemetry_records_layer_and_verdict(self):
        """layer_decisions contains GuardResult entries for capability and policy layers."""
        fake = FakeLLM(
            intents=[intent_ok()],
            plans=[
                Plan(steps=[step("open_close_sunroof", {"position": "open"})]),
                Plan(steps=[], done_reason="done"),
            ],
            drafts=[FAKE_DRAFT],
            claims=[ClaimExtractionResponse(claims=[])],  # C5
        )
        # open_close_sunroof has string argument "position" — no numeric args → C2 skips
        ctx, _trajectory, action = run_scripted(fake)
        self.assertIsInstance(action, EmitText)
        layers = [d.layer for d in ctx.layer_decisions]
        self.assertIn("CapabilityMatcher", layers)
        self.assertIn("PolicyChecker.preflight", layers)
        self.assertIn("FabricationGuard.C5", layers)
        # All final decisions recorded
        verdicts = {d.verdict for d in ctx.layer_decisions}
        self.assertIn("PASS", verdicts)


class LLMProviderTest(unittest.TestCase):
    """Tests for provider-aware cache hints and transient-error retry/backoff."""

    def test_cache_hints_applied_for_anthropic(self):
        """cache_control is set on system message and last tool for anthropic/ models."""
        from track_1_agent_under_test.glassbox.llm import _apply_cache_hints
        msgs = [{"role": "system", "content": "sys"}]
        tools = [{"function": {"name": "t1"}}, {"function": {"name": "t2"}}]
        _apply_cache_hints(msgs, tools, "anthropic/claude-sonnet-4-6")
        self.assertEqual(msgs[0].get("cache_control"), {"type": "ephemeral"})
        self.assertEqual(tools[-1]["function"].get("cache_control"), {"type": "ephemeral"})
        self.assertNotIn("cache_control", tools[0]["function"])

    def test_cache_hints_skipped_for_vertex(self):
        """cache_control is NOT set for vertex_ai/ models."""
        from track_1_agent_under_test.glassbox.llm import _apply_cache_hints
        msgs = [{"role": "system", "content": "sys"}]
        tools = [{"function": {"name": "t1"}}]
        _apply_cache_hints(msgs, tools, "vertex_ai/claude-sonnet-4-6")
        self.assertNotIn("cache_control", msgs[0])
        self.assertNotIn("cache_control", tools[0]["function"])

    def test_cache_hints_skipped_for_gemini(self):
        """cache_control is NOT set for other providers."""
        from track_1_agent_under_test.glassbox.llm import _apply_cache_hints
        msgs = [{"role": "system", "content": "sys"}]
        _apply_cache_hints(msgs, None, "gemini/gemini-2.5-flash")
        self.assertNotIn("cache_control", msgs[0])

    def test_transient_error_retried_with_backoff(self):
        """A transient RateLimitError triggers retry; succeeds on second attempt."""
        import litellm.exceptions as exc_mod
        from track_1_agent_under_test.glassbox.llm import _raw_completion
        call_count = 0

        def fake_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise exc_mod.RateLimitError(
                    message="rate limit", llm_provider="anthropic", model="test"
                )
            return "ok"

        with patch("track_1_agent_under_test.glassbox.llm.completion", fake_completion), \
             patch("track_1_agent_under_test.glassbox.llm.time.sleep") as mock_sleep:
            result = _raw_completion(model="anthropic/claude-sonnet-4-6", messages=[])
        self.assertEqual(result, "ok")
        self.assertEqual(call_count, 2)
        mock_sleep.assert_called_once_with(2)  # first backoff = 2s

    def test_non_transient_error_not_retried(self):
        """A non-transient AuthenticationError is raised immediately, no retry."""
        import litellm.exceptions as exc_mod
        from track_1_agent_under_test.glassbox.llm import _raw_completion
        call_count = 0

        def fake_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            raise exc_mod.AuthenticationError(
                message="bad key", llm_provider="anthropic", model="test"
            )

        with patch("track_1_agent_under_test.glassbox.llm.completion", fake_completion), \
             patch("track_1_agent_under_test.glassbox.llm.time.sleep") as mock_sleep:
            with self.assertRaises(exc_mod.AuthenticationError):
                _raw_completion(model="anthropic/claude-sonnet-4-6", messages=[])
        self.assertEqual(call_count, 1)
        mock_sleep.assert_not_called()

    def test_transient_error_exhausted_raises(self):
        """After all backoff attempts, the transient error is re-raised."""
        import litellm.exceptions as exc_mod
        from track_1_agent_under_test.glassbox.llm import _raw_completion, _TRANSIENT_MAX_ATTEMPTS

        def fake_completion(**kwargs):
            raise exc_mod.ServiceUnavailableError(
                message="503", llm_provider="anthropic", model="test"
            )

        with patch("track_1_agent_under_test.glassbox.llm.completion", fake_completion), \
             patch("track_1_agent_under_test.glassbox.llm.time.sleep"):
            with self.assertRaises(exc_mod.ServiceUnavailableError):
                _raw_completion(model="anthropic/claude-sonnet-4-6", messages=[])


class SilentRefusalGuardTest(unittest.TestCase):
    """F2: planner returns empty steps without capability_missing for available tools."""

    TOOLS_WITH_TRUNK = TOOLS + [{"function": {
        "name": "open_close_trunk_door",
        "description": "REQUIRES_CONFIRMATION, Vehicle Control: Open or close the trunk door.",
        "parameters": {"properties": {"action": {"type": "string", "enum": ["OPEN", "CLOSE"]}},
                       "required": ["action"]},
    }}]

    def test_silent_refusal_replans_with_available_tools(self):
        """Planner returns empty steps but INTAKE listed a covered tool → one re-plan.
        The re-planned call is then blocked by LLM-POL:004 (REQUIRES_CONFIRMATION)
        which asks for user confirmation — proving the guard fired and the planner
        produced the correct call."""
        intent = Intent(
            user_request_summary="Open the trunk door",
            required_tools=["open_close_trunk_door"],
            is_state_changing=True,
            is_ambiguous=False,
        )
        fake = FakeLLM(
            intents=[intent],
            plans=[
                Plan(steps=[], done_reason="cannot open trunk"),
                Plan(steps=[step("open_close_trunk_door", {"action": "OPEN"})]),
                Plan(steps=[], done_reason="done"),
            ],
            drafts=[FAKE_DRAFT],
        )
        ctx, trajectory, action = run_scripted(fake, tools=self.TOOLS_WITH_TRUNK)
        self.assertIsInstance(action, EmitText)
        self.assertTrue(ctx.silent_refusal_replan)
        self.assertTrue(any("PLAN-GUARD" in n for n in ctx.policy_notes))
        # LLM-POL:004 blocks the call and emits a confirmation question
        self.assertIn("confirmation", action.text.lower())

    def test_silent_refusal_not_triggered_when_no_required_tools(self):
        """INTAKE listed no required_tools → empty plan is legitimate, no re-plan."""
        intent = Intent(
            user_request_summary="Thanks, that's all",
            required_tools=[],
            is_state_changing=False,
            is_ambiguous=False,
        )
        fake = FakeLLM(
            intents=[intent],
            plans=[Plan(steps=[], done_reason="nothing to do")],
            drafts=[FAKE_DRAFT],
        )
        ctx, trajectory, action = run_scripted(fake, tools=self.TOOLS_WITH_TRUNK)
        self.assertFalse(ctx.silent_refusal_replan)
        self.assertEqual(trajectory, [])

    def test_silent_refusal_bounded_to_one_replan(self):
        """Re-plan fires once; second empty plan falls through to VERIFY."""
        intent = Intent(
            user_request_summary="Open the trunk door",
            required_tools=["open_close_trunk_door"],
            is_state_changing=True,
            is_ambiguous=False,
        )
        fake = FakeLLM(
            intents=[intent],
            plans=[
                Plan(steps=[], done_reason="cannot open trunk"),
                Plan(steps=[], done_reason="still cannot"),
            ],
            drafts=[FAKE_DRAFT],
        )
        ctx, trajectory, action = run_scripted(fake, tools=self.TOOLS_WITH_TRUNK)
        self.assertTrue(ctx.silent_refusal_replan)
        self.assertEqual(trajectory, [])


class RefusalRedirectTest(unittest.TestCase):
    """G1 Fix 1: _respond_refusal with executed_signatures → VERIFY/sanitize/C6."""

    def test_refusal_after_execution_goes_through_verify(self):
        """Agent executes tools, then planner uses a missing tool → VERIFY, not refusal.

        This is the hall_32 T1/T2 pattern: the agent got weather successfully,
        but the second plan round tries a removed tool. The response must go
        through VERIFY → sanitize/C6 so false inability claims get caught.
        """
        intent = Intent(
            user_request_summary="Get weather and adjust fan",
            required_tools=["get_weather"],
            is_state_changing=False,
            is_ambiguous=False,
        )
        fake = FakeLLM(
            intents=[intent],
            plans=[
                Plan(steps=[step("get_weather", {"location": "Berlin"})]),
                # second round: planner tries removed tool (filtered by check_step)
                Plan(steps=[step("set_fan_speed", {"level": 2})]),
            ],
            drafts=[Draft(
                claims=[],
                response=(
                    "I'm sorry, but I'm not able to check the weather "
                    "in this vehicle. You'll need to check manually."
                ),
            )],
            claims=[ClaimExtractionResponse(claims=[])],
        )
        machine = StateMachine()
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("Get weather and adjust fan.")
        ctx = TurnContext(ledger=ledger, tools=TOOLS, model="fake")
        trajectory = []
        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)
            while isinstance(action, EmitToolCalls):
                trajectory.append([(c.tool, c.arguments, c.call_id) for c in action.calls])
                for c in action.calls:
                    result = json.dumps({
                        "status": "SUCCESS",
                        "result": {"weather": "sunny"},
                    })
                    ctx.ledger.add_tool_result(c.tool, result, c.call_id)
                action = machine.resume(ctx)

        self.assertIsInstance(action, EmitText)
        self.assertIn("VERIFY", ctx.state_trace)
        # C6 catches the false inability claim about weather (tool succeeded)
        self.assertNotIn("not able to check the weather", action.text)
        executed = [c[0] for batch in trajectory for c in batch]
        self.assertIn("get_weather", executed)

    def test_refusal_without_execution_stays_refusal(self):
        """No tools executed → _respond_refusal fires normally (honest refusal)."""
        intent = Intent(
            user_request_summary="Adjust the fan speed",
            required_tools=["set_fan_speed"],
            is_state_changing=True,
            is_ambiguous=False,
        )
        fake = FakeLLM(
            intents=[intent],
            plans=[Plan(steps=[step("set_fan_speed", {"level": 2})])],
            refusals=[FAKE_REFUSAL],
        )
        ctx, trajectory, action = run_scripted(fake)
        self.assertIsInstance(action, EmitText)
        self.assertEqual(action.text, FAKE_REFUSAL.response)
        self.assertNotIn("VERIFY", ctx.state_trace)
        self.assertEqual(trajectory, [])


# ---------------------------------------------------------------------------
# Unknown-field caveat tests (hall_16 — Lesson 1a deterministic gate)
# ---------------------------------------------------------------------------

from track_1_agent_under_test.glassbox.guard import (
    inject_unknown_caveat,
    _collect_unknown_fields,
)


class UnknownFieldCaveatTest(unittest.TestCase):
    """Test the deterministic gate that injects uncertainty caveats when
    executed actions are in the same domain as unknown-valued tool result fields."""

    @staticmethod
    def _ledger_with_window_unknowns() -> Ledger:
        """Ledger simulating hall_16: window observation has unknown fields, rear window action succeeded."""
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("Close all windows please.")
        ledger.add_tool_call("get_vehicle_window_positions", {}, "c1")
        ledger.add_tool_result("get_vehicle_window_positions", json.dumps({
            "status": "SUCCESS",
            "result": {
                "window_driver_position": "unknown",
                "window_passenger_position": "unknown",
                "window_rear_left": 25,
                "window_rear_right": 100,
            },
        }), "c1")
        ledger.add_tool_call("open_close_window", {"window": "rear_left", "position": "close"}, "c2")
        ledger.add_tool_result("open_close_window", json.dumps({
            "status": "SUCCESS",
            "result": {"window_rear_left": 0},
        }), "c2")
        return ledger

    def test_caveat_injected_on_causal_link(self):
        """(a) Draft omits uncertainty for window domain → caveat appended."""
        ledger = self._ledger_with_window_unknowns()
        executed = {"open_close_window:{\"position\":\"close\",\"window\":\"rear_left\"}"}
        draft = "I've closed the rear left window for you."
        result = inject_unknown_caveat(draft, ledger, executed)
        self.assertIn("unavailable", result.lower())
        self.assertIn("window driver position", result.lower())
        self.assertIn("window passenger position", result.lower())

    def test_no_caveat_when_draft_already_mentions_uncertainty(self):
        """Draft already covers uncertainty → no duplicate caveat."""
        ledger = self._ledger_with_window_unknowns()
        executed = {"open_close_window:{\"position\":\"close\",\"window\":\"rear_left\"}"}
        draft = (
            "I've closed the rear left window. The window driver position "
            "and window passenger position are currently unavailable."
        )
        result = inject_unknown_caveat(draft, ledger, executed)
        self.assertEqual(result, draft)

    def test_no_caveat_when_no_domain_overlap(self):
        """(b) Unknown field in weather domain, action in window domain → no caveat (null-FP)."""
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("Close all windows.")
        ledger.add_tool_call("get_weather", {"location": "Berlin"}, "c1")
        ledger.add_tool_result("get_weather", json.dumps({
            "status": "SUCCESS",
            "result": {"temperature": "unknown", "condition": "sunny"},
        }), "c1")
        ledger.add_tool_call("open_close_window", {"window": "rear_left", "position": "close"}, "c2")
        ledger.add_tool_result("open_close_window", json.dumps({
            "status": "SUCCESS",
            "result": {"window_rear_left": 0},
        }), "c2")
        executed = {"open_close_window:{\"position\":\"close\",\"window\":\"rear_left\"}"}
        draft = "I've closed the rear left window for you."
        result = inject_unknown_caveat(draft, ledger, executed)
        self.assertEqual(result, draft)

    def test_no_caveat_when_no_unknown_fields(self):
        """No unknown fields in any tool result → draft passes through unchanged."""
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("Close the window.")
        ledger.add_tool_call("get_vehicle_window_positions", {}, "c1")
        ledger.add_tool_result("get_vehicle_window_positions", json.dumps({
            "status": "SUCCESS",
            "result": {"window_rear_left": 25, "window_rear_right": 100},
        }), "c1")
        executed = {"open_close_window:{\"position\":\"close\",\"window\":\"rear_left\"}"}
        draft = "I've closed the rear left window."
        result = inject_unknown_caveat(draft, ledger, executed)
        self.assertEqual(result, draft)

    def test_no_caveat_when_no_executions(self):
        """No executed tools → no caveat (pure observation turn)."""
        ledger = self._ledger_with_window_unknowns()
        result = inject_unknown_caveat("The rear left window is at 25%.", ledger, set())
        self.assertEqual(result, "The rear left window is at 25%.")

    def test_hall_30_regression_c6_still_works(self):
        """(c) Regression: C6 inability-contradiction fix (hall_30 pattern) still works
        after unknown-caveat wiring in _verify_and_respond."""
        intent = Intent(
            user_request_summary="Check the weather in Berlin",
            required_tools=["get_weather"],
            is_state_changing=False,
            is_ambiguous=False,
        )
        fake = FakeLLM(
            intents=[intent],
            plans=[
                Plan(steps=[step("get_weather", {"location": "Berlin"})]),
                Plan(steps=[]),
            ],
            drafts=[Draft(response="I'm not able to check the weather. It is sunny in Berlin.")],
            claims=[ClaimExtractionResponse(claims=[])],
        )
        machine = StateMachine()
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("What's the weather in Berlin?")
        ctx = TurnContext(ledger=ledger, tools=TOOLS, model="fake")

        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)
            while isinstance(action, EmitToolCalls):
                for c in action.calls:
                    result = json.dumps({
                        "status": "SUCCESS",
                        "result": {"condition": "sunny", "temperature": 22},
                    })
                    ctx.ledger.add_tool_result(c.tool, result, c.call_id)
                action = machine.resume(ctx)

        self.assertIsInstance(action, EmitText)
        self.assertIn("VERIFY", ctx.state_trace)
        self.assertNotIn("not able to check the weather", action.text)
        self.assertIn("sunny", action.text.lower())


class ValueFlowCheckTest(unittest.TestCase):
    """I1: disambiguation-resolved values must survive to emission."""

    def _ctx_with_provenance(self, *values):
        """Create a TurnContext whose ledger backs the given numeric values."""
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("Adjust the sunshade.")
        result = json.dumps({"status": "SUCCESS",
                             "result": {str(v): v for v in values}})
        ledger.add_tool_call("get_user_preferences", {}, "prov_call")
        ledger.add_tool_result("get_user_preferences", result, "prov_call")
        return TurnContext(ledger=ledger, tools=TOOLS, model="fake")

    def test_mismatch_triggers_replan(self):
        """(a) Resolved value 50, planner sets 100 → re-plan with correction."""
        intent = Intent(
            user_request_summary="Adjust the sunshade",
            required_tools=["open_close_sunshade"],
            is_state_changing=True,
            is_ambiguous=False,
        )
        fake = FakeLLM(
            intents=[intent],
            plans=[
                Plan(steps=[step("open_close_sunshade", {"percentage": 100})]),
                Plan(steps=[step("open_close_sunshade", {"percentage": 50})]),
                Plan(steps=[], done_reason="done"),
            ],
            drafts=[FAKE_DRAFT],
        )
        machine = StateMachine()
        ctx = self._ctx_with_provenance(50, 100)
        ctx.disambiguation_resolved = [("open_close_sunshade", "percentage", 50)]

        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)

        self.assertEqual(ctx.value_flow_rebuttals, 1)
        self.assertTrue(any("VALUE-FLOW" in n for n in ctx.policy_notes))
        self.assertIsInstance(action, EmitToolCalls)
        self.assertEqual(action.calls[0].arguments["percentage"], 50)

    def test_matching_value_passes(self):
        """(b) Resolved value 50, planner sets 50 → PASS (null FP)."""
        intent = Intent(
            user_request_summary="Adjust the sunshade",
            required_tools=["open_close_sunshade"],
            is_state_changing=True,
            is_ambiguous=False,
        )
        fake = FakeLLM(
            intents=[intent],
            plans=[
                Plan(steps=[step("open_close_sunshade", {"percentage": 50})]),
                Plan(steps=[], done_reason="done"),
            ],
            drafts=[FAKE_DRAFT],
        )
        machine = StateMachine()
        ctx = self._ctx_with_provenance(50)
        ctx.disambiguation_resolved = [("open_close_sunshade", "percentage", 50)]

        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)

        self.assertEqual(ctx.value_flow_rebuttals, 0)
        self.assertFalse(any("VALUE-FLOW" in n for n in ctx.policy_notes))
        self.assertIsInstance(action, EmitToolCalls)
        self.assertEqual(action.calls[0].arguments["percentage"], 50)

    def test_no_resolved_value_no_intervention(self):
        """(c) No resolved value (normal base task) → no intervention."""
        intent = Intent(
            user_request_summary="Adjust the sunshade",
            required_tools=["open_close_sunshade"],
            is_state_changing=True,
            is_ambiguous=False,
        )
        fake = FakeLLM(
            intents=[intent],
            plans=[
                Plan(steps=[step("open_close_sunshade", {"percentage": 75})]),
                Plan(steps=[], done_reason="done"),
            ],
            drafts=[FAKE_DRAFT],
        )
        machine = StateMachine()
        ctx = self._ctx_with_provenance(75)

        with patch.object(glassbox_llm, "call_structured", fake):
            action = machine.run_turn(ctx)

        self.assertEqual(ctx.value_flow_rebuttals, 0)
        self.assertEqual(len(ctx.disambiguation_resolved), 0)
        self.assertFalse(any("VALUE-FLOW" in n for n in ctx.policy_notes))
        self.assertIsInstance(action, EmitToolCalls)
        self.assertEqual(action.calls[0].arguments["percentage"], 75)


if __name__ == "__main__":
    unittest.main()
