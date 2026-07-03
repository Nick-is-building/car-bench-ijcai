"""
Zustandsmaschine — Stufe 2.

INTAKE → CAPABILITY_CHECK → (CLARIFY | PLAN) → POLICY_CHECK → EXECUTE → VERIFY → RESPOND

Das LLM laeuft nie frei: jeder Zustand hat ein eigenes enges Prompt-Modul mit
Temperatur 0 und JSON-Schema-Output. Die Maschine ist RESUMIERBAR, weil das
A2A-Protokoll multi-turn ist: Tool-Calls werden als Aktion an die A2A-Schicht
zurueckgegeben (EmitToolCalls), der Evaluator fuehrt sie aus und schickt die
Ergebnisse in einer neuen Nachricht — dann setzt `resume()` am EXECUTE-Punkt
fort, statt bei INTAKE neu zu starten.

PLAN→POLICY_CHECK→EXECUTE laeuft als begrenzte Schleife (siehe ADR-0002):
der Planner liefert pro Runde nur die sofort ausfuehrbaren Schritte; Schritte,
deren Argumente von Ergebnissen abhaengen, kommen in der naechsten Runde.
Leerer Plan = Turn-Toolarbeit abgeschlossen → VERIFY → RESPOND.

Idempotenz: deterministische call_ids (turn/runde/index), und ein identischer
(tool, argumente)-Call wird innerhalb eines Turns nie doppelt ausgefuehrt
(vgl. das "Sunroof zweimal geoeffnet"-Beispiel im Benchmark-Repo).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, auto

from .ledger import Ledger


class State(Enum):
    INTAKE = auto()
    CAPABILITY_CHECK = auto()
    CLARIFY = auto()
    PLAN = auto()
    POLICY_CHECK = auto()
    EXECUTE = auto()
    VERIFY = auto()
    RESPOND = auto()
    DONE = auto()


# Upper bound for PLAN→EXECUTE rounds per user turn — last-resort loop stop,
# NOT a task-size budget. Published train tasks have up to 9 ground-truth
# actions (verified across all three splits, see ADR-0003), and read calls
# (get_weather, get_*_state) consume rounds on top of that, so the bound must
# sit well above 9+reads. Real planner loops are caught earlier by the
# duplicate-signature detection. TurnContext.plan_bound_hit flags if this
# bound ever binds so dev runs surface it.
MAX_PLAN_ROUNDS = 16

# Deterministic fallback texts (used only while later Stufen are stubs, or as
# last-resort safety). Honest, no fabricated alternatives.
FALLBACK_UNAVAILABLE = (
    "I'm sorry, I can't do that with the controls available in this car."
)
FALLBACK_POLICY_BLOCK = (
    "I'm sorry, I can't do that because it would conflict with a vehicle policy."
)


@dataclass
class PlannedCall:
    tool: str
    arguments: dict
    call_id: str
    rationale: str = ""

    @property
    def signature(self) -> str:
        return f"{self.tool}:{json.dumps(self.arguments, sort_keys=True)}"


class Action:
    """Base class for what the A2A layer must do next."""


@dataclass
class EmitToolCalls(Action):
    calls: list[PlannedCall]


@dataclass
class EmitText(Action):
    text: str


@dataclass
class TurnContext:
    """Everything the state machine needs for one user turn.

    Persists across A2A messages within the turn (tool-call round trips).
    """
    ledger: Ledger
    tools: list[dict]
    model: str
    current_state: State = State.INTAKE

    intent: dict = field(default_factory=dict)
    capability_result: str = ""          # "covered" | "uncovered" | "ambiguous"
    capability_missing: bool = False     # set by planner when required tool is absent
    plan_round: int = 0
    pending_calls: list[PlannedCall] = field(default_factory=list)
    executed_signatures: set[str] = field(default_factory=set)
    policy_violations: list = field(default_factory=list)
    clarification_question: str = ""
    final_response: str = ""
    # true if MAX_PLAN_ROUNDS ended the turn before the planner confirmed
    # completion (empty plan) — a possible task cut-off, must be investigated
    plan_bound_hit: bool = False
    # state trace for tests/debugging — appended on every transition
    state_trace: list[str] = field(default_factory=list)

    def transition(self, state: State) -> None:
        self.current_state = state
        self.state_trace.append(state.name)


class StateMachine:
    """Orchestrates one agent turn through the fixed state sequence.

    `run_turn(ctx)` starts a turn after a user message.
    `resume(ctx)` continues after tool results were recorded in the ledger.
    Both return an Action for the A2A layer.
    """

    def run_turn(self, ctx: TurnContext) -> Action:
        from .capability import CapabilityMatcher
        from . import prompts

        ctx.transition(State.INTAKE)
        ctx.intent = prompts.intake.extract_intent(ctx)

        ctx.transition(State.CAPABILITY_CHECK)
        matcher = CapabilityMatcher(ctx.tools)
        ctx.capability_result = self._capability_check(matcher, ctx)
        if ctx.capability_result == "uncovered":
            return self._respond_refusal(ctx)

        if ctx.capability_result == "ambiguous":
            ctx.transition(State.CLARIFY)
            action = self._clarify(ctx)
            if action is not None:
                return action
            # ambiguity was resolved internally — fall through to PLAN

        return self._plan_execute_loop(ctx, matcher)

    def resume(self, ctx: TurnContext) -> Action:
        """Continue after the A2A layer recorded tool results in the ledger."""
        from .capability import CapabilityMatcher

        ctx.pending_calls = []
        matcher = CapabilityMatcher(ctx.tools)
        return self._plan_execute_loop(ctx, matcher)

    # --- PLAN → POLICY_CHECK → EXECUTE (bounded loop, ADR-0002) ---

    def _plan_execute_loop(self, ctx: TurnContext, matcher) -> Action:
        from . import prompts

        while True:
            if ctx.plan_round >= MAX_PLAN_ROUNDS:
                ctx.plan_bound_hit = True
                break
            ctx.plan_round += 1
            ctx.transition(State.PLAN)
            steps = prompts.plan.build_plan(ctx)
            if not steps:
                if ctx.capability_missing:
                    return self._respond_refusal(ctx)
                break

            calls: list[PlannedCall] = []
            saw_unknown_tool = False
            for i, step in enumerate(steps):
                call = PlannedCall(
                    tool=step["tool"],
                    arguments=step.get("arguments", {}),
                    call_id=f"call_t{ctx.ledger.current_turn}_r{ctx.plan_round}_s{i}",
                    rationale=step.get("rationale", ""),
                )
                # per-step capability guard: never emit a call the evaluator
                # has no tool for (deterministic, no LLM)
                if matcher.check_step(call.tool, call.arguments) == "uncovered":
                    saw_unknown_tool = True
                    continue
                # idempotency: identical (tool, args) never executes twice per turn
                if call.signature in ctx.executed_signatures:
                    continue
                calls.append(call)

            if not calls:
                if saw_unknown_tool:
                    # planner wants a capability that does not exist
                    return self._respond_refusal(ctx)
                # everything was a duplicate → planner is looping; stop
                break

            # AUT-POL:005 deterministic guard (Stufe 3):
            # Sunroof cannot be opened unless sunshade control is available.
            # Catches hallucination tasks where open_close_sunshade is removed.
            if any(c.tool == "open_close_sunroof" for c in calls):
                sunshade_available = matcher.index.has_tool("open_close_sunshade")
                sunshade_in_batch = any(c.tool == "open_close_sunshade" for c in calls)
                sunshade_executed = any(
                    sig.startswith("open_close_sunshade:")
                    for sig in ctx.executed_signatures
                )
                if not sunshade_available and not sunshade_in_batch and not sunshade_executed:
                    ctx.capability_missing = True
                    return self._respond_refusal(ctx)

            ctx.transition(State.POLICY_CHECK)
            violations = self._policy_pre_flight(ctx, calls)
            if violations:
                ctx.policy_violations = violations
                return self._respond_policy_block(ctx)

            ctx.transition(State.EXECUTE)
            for call in calls:
                ctx.ledger.add_tool_call(call.tool, call.arguments, call.call_id)
                ctx.executed_signatures.add(call.signature)
            ctx.pending_calls = calls
            return EmitToolCalls(calls)

        return self._verify_and_respond(ctx)

    # --- terminal paths ---

    def _verify_and_respond(self, ctx: TurnContext) -> Action:
        from .guard import FabricationGuard
        from . import prompts

        ctx.transition(State.VERIFY)
        draft = prompts.verify.draft_response(ctx)
        try:
            safe = FabricationGuard().sanitize(draft, ctx.ledger)
        except NotImplementedError:  # Stufe 5 pass-through
            safe = draft

        ctx.transition(State.RESPOND)
        final = prompts.respond.finalize(safe, ctx)
        return self._finish(ctx, final)

    def _respond_refusal(self, ctx: TurnContext) -> Action:
        from . import prompts

        ctx.transition(State.RESPOND)
        try:
            text = prompts.respond.generate_honest_refusal(ctx)
        except NotImplementedError:  # Stufe 3 pass-through
            text = FALLBACK_UNAVAILABLE
        return self._finish(ctx, text)

    def _respond_policy_block(self, ctx: TurnContext) -> Action:
        from . import prompts

        ctx.transition(State.RESPOND)
        try:
            text = prompts.respond.generate_policy_block(ctx)
        except NotImplementedError:  # Stufe 4 pass-through
            text = FALLBACK_POLICY_BLOCK
        return self._finish(ctx, text)

    def _finish(self, ctx: TurnContext, text: str) -> EmitText:
        ctx.final_response = text
        ctx.ledger.add_agent_response(text)
        ctx.transition(State.DONE)
        return EmitText(text)

    # --- stub-safe component calls (pass-through until their Stufe lands) ---

    def _capability_check(self, matcher, ctx: TurnContext) -> str:
        try:
            return matcher.check(ctx.intent)
        except NotImplementedError:  # Stufe 3 pass-through
            return "ambiguous" if ctx.intent.get("is_ambiguous") else "covered"

    def _clarify(self, ctx: TurnContext) -> Action | None:
        """Returns an EmitText question, or None if resolved internally."""
        from .disambiguation import DisambiguationEngine

        try:
            result = DisambiguationEngine().resolve(ctx)
        except NotImplementedError:
            # Stufe 6 pass-through: conservative default — ask the question
            # that INTAKE already formulated (deterministic, no extra call)
            question = ctx.intent.get("clarification_question", "").strip()
            if not question:
                reason = ctx.intent.get("ambiguity_reason", "your request")
                question = f"Just to make sure I get this right: could you clarify {reason}?"
            ctx.clarification_question = question
            return self._finish(ctx, question)

        if result.needs_user_clarification:
            ctx.clarification_question = result.question
            return self._finish(ctx, result.question)
        ctx.intent = result.resolved_intent or ctx.intent
        return None

    def _policy_pre_flight(self, ctx: TurnContext, calls: list[PlannedCall]) -> list:
        from .policies import PolicyChecker

        try:
            return PolicyChecker().pre_flight(ctx)
        except NotImplementedError:  # Stufe 4 pass-through
            return []
