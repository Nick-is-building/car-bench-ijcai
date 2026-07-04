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

from loguru import logger as _log

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
    # planner claimed a missing capability, but every named tool exists in the
    # schemas (or none was named) — claim rejected, re-plan (bounded)
    capability_claim_rebutted: bool = False
    capability_rebuttals: int = 0
    plan_round: int = 0
    pending_calls: list[PlannedCall] = field(default_factory=list)
    executed_signatures: set[str] = field(default_factory=set)
    policy_violations: list = field(default_factory=list)
    # markierte Pre-Flight-Notizen (Verschiebungen, Injektionen, Obligationen);
    # fliessen in die PLAN-Folgerunden und in VERIFY ein (ADR-0004)
    policy_notes: list[str] = field(default_factory=list)
    clarification_question: str = ""
    final_response: str = ""
    # true if MAX_PLAN_ROUNDS ended the turn before the planner confirmed
    # completion (empty plan) — a possible task cut-off, must be investigated
    plan_bound_hit: bool = False
    # state trace for tests/debugging — appended on every transition
    state_trace: list[str] = field(default_factory=list)
    # OI-011 diagnostics: tracks where a refusal originated
    capability_refusal_source: str = ""
    # OI-011 H-R2: intake re-extract already attempted this turn
    intake_rebuttal_done: bool = False

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
        from .capability import CapabilityIndex, CapabilityMatcher, fuzzy_catalog_hint
        from . import prompts

        ctx.transition(State.INTAKE)
        ctx.intent = prompts.intake.extract_intent(ctx)

        # INTAKE-REBUTTAL (OI-011 H-R2): one-time re-extract if required_tools
        # contains names not in the catalog but close to a catalog tool.
        # Source of refusal becomes diagnosable: intake vs. planner.
        if not ctx.intake_rebuttal_done:
            idx = CapabilityIndex(ctx.tools)
            unknown_required = [
                t for t in ctx.intent.get("required_tools", [])
                if not idx.has_tool(t)
            ]
            if unknown_required:
                hints: dict[str, list[str]] = {}
                for t in unknown_required:
                    candidates = fuzzy_catalog_hint(t, idx.tool_names)
                    if candidates:
                        hints[t] = candidates
                if hints:
                    ctx.intake_rebuttal_done = True
                    note = (
                        "INTAKE-REBUTTAL: some required_tools names are not in the catalog. "
                        + " ".join(
                            f"'{t}' → closest: {' / '.join(c)}."
                            for t, c in hints.items()
                        )
                        + " Re-extract using exact catalog tool names."
                    )
                    _log.info(
                        "INTAKE-REBUTTAL: re-extracting intent",
                        source="intake_required_tools",
                        fuzzy_hints={t: c for t, c in hints.items()},
                    )
                    ctx.intent = prompts.intake.extract_intent(ctx, rebuttal_note=note)
                else:
                    _log.warning(
                        "INTAKE: unknown required_tools with no fuzzy match — will refuse",
                        source="intake_required_tools",
                        unknown=unknown_required,
                    )

        ctx.transition(State.CAPABILITY_CHECK)
        matcher = CapabilityMatcher(ctx.tools)
        ctx.capability_result = self._capability_check(matcher, ctx)
        if ctx.capability_result == "uncovered":
            ctx.capability_refusal_source = "intake"
            _log.warning(
                "Refusal: intake capability check uncovered",
                source="intake",
                intent_required_tools=ctx.intent.get("required_tools", []),
                intent_missing=ctx.intent.get("required_but_missing_tools", []),
            )
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
                    ctx.capability_refusal_source = ctx.capability_refusal_source or "planner"
                    _log.warning(
                        "Refusal: planner reported missing capability",
                        source=ctx.capability_refusal_source,
                        policy_notes_count=len(ctx.policy_notes),
                    )
                    return self._respond_refusal(ctx)
                if ctx.capability_claim_rebutted and ctx.capability_rebuttals < 2:
                    # false missing-capability claim — re-plan with the
                    # PLAN-GUARD note instead of refusing (B6 root cause)
                    ctx.capability_rebuttals += 1
                    continue
                break

            calls: list[PlannedCall] = []
            unknown_tool_names: list[str] = []
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
                    unknown_tool_names.append(call.tool)
                    continue
                # idempotency: identical (tool, args) never executes twice per turn
                if call.signature in ctx.executed_signatures:
                    continue
                calls.append(call)

            if not calls:
                if unknown_tool_names:
                    # planner tried to USE unknown tool names in steps — log source
                    ctx.capability_refusal_source = "execute_guard"
                    _log.warning(
                        "Refusal: planner used unknown tool names in steps",
                        source="execute_guard",
                        unknown_tools=unknown_tool_names,
                    )
                    return self._respond_refusal(ctx)
                # everything was a duplicate → planner is looping; stop
                break

            # Stufe 4 (ADR-0004): declarative policy pre-flight. May refuse,
            # block, inject corrective calls, or defer calls to a later round.
            ctx.transition(State.POLICY_CHECK)
            pf = self._policy_pre_flight(ctx, calls, matcher)
            if pf.missing_capability:
                ctx.policy_violations = pf.missing_capability
                ctx.capability_missing = True
                ctx.capability_refusal_source = "policy_pre_flight"
                _log.warning(
                    "Refusal: policy pre-flight missing capability",
                    source="policy_pre_flight",
                    missing=pf.missing_capability,
                )
                return self._respond_refusal(ctx)
            if pf.blocked:
                ctx.policy_violations = pf.blocked
                return self._respond_policy_block(ctx)
            ctx.policy_notes.extend(pf.notes)
            calls = [
                PlannedCall(
                    tool=inj.tool,
                    arguments=inj.arguments,
                    call_id=f"call_t{ctx.ledger.current_turn}_r{ctx.plan_round}_p{i}",
                    rationale=f"policy pre-flight injection ({inj.policy_id})",
                )
                for i, inj in enumerate(pf.injected)
            ] + list(pf.kept)
            if not calls:
                # whole batch deferred — re-plan with the pre-flight notes
                continue

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

    def _policy_pre_flight(self, ctx: TurnContext, calls: list[PlannedCall], matcher):
        from .policies import PolicyChecker

        return PolicyChecker().pre_flight(calls, ctx.ledger, matcher.index)
