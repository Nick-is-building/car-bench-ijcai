"""
Zustandsmaschine — Stufe 2.

INTAKE → CAPABILITY_CHECK → (CLARIFY | PLAN) → POLICY_CHECK → EXECUTE → VERIFY → RESPOND

Jeder Zustand hat ein eigenes enges Prompt-Modul. Das LLM laeuft nie frei:
es produziert strukturierten JSON-Output innerhalb des jeweiligen Zustands.
Temperatur 0, idempotente Tool-Calls (kein doppeltes Ausfuehren bei Retry).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable

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


@dataclass
class TurnContext:
    """Everything the state machine needs for one agent turn."""
    ledger: Ledger
    tools: list[dict]
    model: str
    current_state: State = State.INTAKE

    # outputs filled by each state
    intent: dict = field(default_factory=dict)
    capability_result: str = ""          # "covered" | "uncovered" | "ambiguous"
    plan: list[dict] = field(default_factory=list)
    policy_violations: list[str] = field(default_factory=list)
    tool_calls_to_execute: list[dict] = field(default_factory=list)
    clarification_question: str = ""
    final_response: str = ""


ToolExecutor = Callable[[str, dict, str], Any]


class StateMachine:
    """
    Orchestrates one agent turn through the fixed state sequence.

    Call `run_turn(ctx, tool_executor)` to process a single user turn.
    The tool_executor callback is provided by the A2A layer and actually
    sends tool calls to the evaluator.
    """

    def run_turn(
        self,
        ctx: TurnContext,
        tool_executor: ToolExecutor,
    ) -> str:
        """Run the full state sequence and return the agent's text response."""
        from .capability import CapabilityMatcher
        from .policies import PolicyChecker
        from .guard import FabricationGuard
        from .disambiguation import DisambiguationEngine
        from . import prompts

        ctx.current_state = State.INTAKE
        intent = prompts.intake.extract_intent(ctx)
        ctx.intent = intent
        ctx.current_state = State.CAPABILITY_CHECK

        matcher = CapabilityMatcher(ctx.tools)
        ctx.capability_result = matcher.check(intent)

        if ctx.capability_result == "uncovered":
            ctx.current_state = State.RESPOND
            response = prompts.respond.generate_honest_refusal(ctx)
            ctx.final_response = response
            ctx.ledger.add_agent_response(response)
            ctx.current_state = State.DONE
            return response

        if ctx.capability_result == "ambiguous":
            ctx.current_state = State.CLARIFY
            engine = DisambiguationEngine()
            result = engine.resolve(ctx)
            if result.needs_user_clarification:
                ctx.clarification_question = result.question
                ctx.current_state = State.RESPOND
                ctx.ledger.add_agent_response(result.question)
                ctx.current_state = State.DONE
                return result.question
            ctx.intent = result.resolved_intent

        ctx.current_state = State.PLAN
        ctx.plan = prompts.plan.build_plan(ctx)

        ctx.current_state = State.POLICY_CHECK
        checker = PolicyChecker()
        violations = checker.pre_flight(ctx)
        if violations:
            ctx.policy_violations = violations
            ctx.current_state = State.RESPOND
            response = prompts.respond.generate_policy_block(ctx)
            ctx.final_response = response
            ctx.ledger.add_agent_response(response)
            ctx.current_state = State.DONE
            return response

        ctx.current_state = State.EXECUTE
        for step in ctx.plan:
            tool_name = step["tool"]
            args = step["arguments"]
            call_id = step["call_id"]
            ctx.ledger.add_tool_call(tool_name, args, call_id)
            result = tool_executor(tool_name, args, call_id)
            ctx.ledger.add_tool_result(tool_name, result, call_id)

            # Re-check policy after each state-changing step
            post_violations = checker.post_execution(tool_name, args, result, ctx)
            if post_violations:
                ctx.policy_violations.extend(post_violations)

        ctx.current_state = State.VERIFY
        guard = FabricationGuard()
        draft = prompts.verify.draft_response(ctx)
        safe_response = guard.sanitize(draft, ctx.ledger)

        ctx.current_state = State.RESPOND
        final = prompts.respond.finalize(safe_response, ctx)
        ctx.final_response = final
        ctx.ledger.add_agent_response(final)
        ctx.current_state = State.DONE
        return final
