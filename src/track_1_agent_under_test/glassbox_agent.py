"""
Glassbox Agent — wires the deterministic shell into the A2A interface.

Drop-in replacement for CARBenchAgentExecutor. Activate via AGENT_CLASS=glassbox.

Protocol mapping (the A2A exchange is multi-turn):
  user message      → StateMachine.run_turn()  → EmitToolCalls | EmitText
  tool results      → StateMachine.resume()    → EmitToolCalls | EmitText
EmitToolCalls suspends the turn (TurnContext persists per context_id) until the
evaluator sends the results back; EmitText completes the turn.
"""
from __future__ import annotations

from pathlib import Path
import sys

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.helpers.proto_helpers import new_message, new_text_part, new_data_part
from a2a.types import Role
from google.protobuf.json_format import MessageToDict

sys.path.insert(0, str(Path(__file__).parent.parent))
from logging_utils import configure_logger
from tool_call_types import ToolCall, ToolCallsData
from turn_metrics import (
    TURN_METRICS_KEY, PROMPT_TOKENS, COMPLETION_TOKENS, COST, MODEL,
    THINKING_TOKENS, NUM_LLM_CALLS, AVG_LLM_CALL_TIME_MS, NUM_PASSES,
)
sys.path.pop(0)

from glassbox import EmitText, EmitToolCalls, Ledger, StateMachine, TurnContext
from glassbox import llm as glassbox_llm

logger = configure_logger(role="glassbox_agent", context="-")

_SAFE_ERROR_TEXT = (
    "I'm sorry, something went wrong on my side and I couldn't complete that. "
    "Could you please try again?"
)


class GlassboxAgentExecutor(AgentExecutor):
    """CAR-bench agent under test using the deterministic glassbox shell."""

    def __init__(self, model: str, temperature: float = 0.0) -> None:
        self.model = model
        self.temperature = temperature
        self._ledgers: dict[str, Ledger] = {}
        self._tools: dict[str, list[dict]] = {}
        self._machines: dict[str, StateMachine] = {}
        # active TurnContext per context while a turn awaits tool results
        self._active_turns: dict[str, TurnContext] = {}
        self._turn_metrics: dict[str, dict] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        ctx_id = context.context_id
        ctx_log = logger.bind(context=f"ctx:{ctx_id[:8]}")

        if ctx_id not in self._ledgers:
            self._ledgers[ctx_id] = Ledger()
            self._machines[ctx_id] = StateMachine()

        ledger = self._ledgers[ctx_id]
        machine = self._machines[ctx_id]

        # accumulate LLM usage across all A2A exchanges of this user turn
        metrics = self._turn_metrics.setdefault(ctx_id, {})
        glassbox_llm.set_metrics_sink(metrics)

        # --- parse incoming A2A message ---
        user_text: str | None = None
        tool_results: list[dict] | None = None
        tools: list[dict] = self._tools.get(ctx_id, [])

        try:
            for part in context.message.parts:
                kind = part.WhichOneof("content")
                if kind == "text":
                    text = part.text
                    if "System:" in text and "\n\nUser:" in text:
                        split = text.split("\n\nUser:", 1)
                        ledger.add_system(split[0].replace("System:", "").strip())
                        user_text = split[1].strip()
                    else:
                        user_text = text
                elif kind == "data":
                    data = MessageToDict(part.data)
                    if "tools" in data:
                        tools = data["tools"]
                        self._tools[ctx_id] = tools
                    elif "tool_results" in data:
                        tool_results = data["tool_results"]
            if user_text is None and tool_results is None:
                user_text = context.get_user_input()
        except Exception as exc:
            ctx_log.warning(f"Message parse error: {exc}")
            user_text = context.get_user_input()

        turn_ctx = self._active_turns.get(ctx_id)

        try:
            if tool_results is not None and turn_ctx is not None:
                self._record_tool_results(ledger, turn_ctx, tool_results, ctx_log)
                action = machine.resume(turn_ctx)
            else:
                if turn_ctx is not None:
                    # user spoke while we awaited results — start a fresh turn
                    ctx_log.warning("New user message with pending tool calls; resetting turn")
                    self._active_turns.pop(ctx_id, None)
                ledger.add_user_turn(user_text or "")
                turn_ctx = TurnContext(ledger=ledger, tools=tools, model=self.model)
                self._active_turns[ctx_id] = turn_ctx
                action = machine.run_turn(turn_ctx)
        except Exception as exc:
            ctx_log.error(f"Glassbox turn failed: {exc}")
            self._active_turns.pop(ctx_id, None)
            action = EmitText(_SAFE_ERROR_TEXT)
            ledger.add_agent_response(_SAFE_ERROR_TEXT)

        # --- serialize action ---
        if isinstance(action, EmitToolCalls):
            tool_calls = [
                ToolCall(tool_name=c.tool, arguments=c.arguments) for c in action.calls
            ]
            parts = [new_data_part(ToolCallsData(tool_calls=tool_calls).model_dump())]
            ctx_log.info(
                "Emitting tool calls",
                tools=[c.tool for c in action.calls],
                state_trace=turn_ctx.state_trace if turn_ctx else [],
            )
        else:
            parts = [new_text_part(action.text)]
            self._active_turns.pop(ctx_id, None)
            ctx_log.info(
                "Turn complete",
                response_preview=action.text[:100],
                state_trace=turn_ctx.state_trace if turn_ctx else [],
            )

        msg = new_message(parts=parts, context_id=ctx_id, role=Role.ROLE_AGENT)

        # attach turn metrics on the final (text) response, like the baseline
        if isinstance(action, EmitText):
            m = self._turn_metrics.pop(ctx_id, {})
            num_calls = m.get("num_llm_calls", 0)
            avg_ms = (m.get("total_llm_time_ms", 0.0) / num_calls) if num_calls else 0.0
            msg.metadata.update({TURN_METRICS_KEY: {
                PROMPT_TOKENS: m.get("prompt_tokens", 0),
                COMPLETION_TOKENS: m.get("completion_tokens", 0),
                COST: m.get("cost", 0.0),
                MODEL: self.model,
                THINKING_TOKENS: m.get("thinking_tokens", 0),
                NUM_LLM_CALLS: num_calls,
                AVG_LLM_CALL_TIME_MS: round(avg_ms, 1),
                NUM_PASSES: 1,
            }})

        await event_queue.enqueue_event(msg)

    def _record_tool_results(
        self,
        ledger: Ledger,
        turn_ctx: TurnContext,
        tool_results: list[dict],
        ctx_log,
    ) -> None:
        """Match evaluator tool results to pending calls by tool name (FIFO)."""
        pending_by_name: dict[str, list] = {}
        for call in turn_ctx.pending_calls:
            pending_by_name.setdefault(call.tool, []).append(call)

        for tr in tool_results:
            name = tr.get("tool_name", tr.get("toolName", ""))
            content = tr.get("content", "")
            matching = pending_by_name.get(name, [])
            if matching:
                call = matching.pop(0)
                ledger.add_tool_result(name, content, call.call_id)
            else:
                ctx_log.warning("Tool result without matching pending call", tool_name=name)
                fallback_id = tr.get("tool_call_id", tr.get("toolCallId", f"unmatched_{name}"))
                ledger.add_tool_result(name, content, fallback_id)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        ctx_id = context.context_id
        self._ledgers.pop(ctx_id, None)
        self._tools.pop(ctx_id, None)
        self._machines.pop(ctx_id, None)
        self._active_turns.pop(ctx_id, None)
        self._turn_metrics.pop(ctx_id, None)
