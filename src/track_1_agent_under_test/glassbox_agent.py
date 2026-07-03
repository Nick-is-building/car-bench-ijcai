"""
Glassbox Agent — wires the deterministic shell into the A2A interface.

Drop-in replacement for CARBenchAgentExecutor.
Activate via env var: AGENT_CLASS=glassbox
"""
from __future__ import annotations

import json
import os
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
sys.path.pop(0)

from glassbox import Ledger, StateMachine, TurnContext

logger = configure_logger(role="glassbox_agent", context="-")


class GlassboxAgentExecutor(AgentExecutor):
    """
    CAR-bench agent using the deterministic glassbox shell.

    Until all Stufen are implemented the agent falls back to raising
    NotImplementedError on the first real task — use baseline agent for now.
    """

    def __init__(self, model: str, temperature: float = 0.0) -> None:
        self.model = model
        self.temperature = temperature
        self._ledgers: dict[str, Ledger] = {}
        self._tools: dict[str, list[dict]] = {}
        self._machines: dict[str, StateMachine] = {}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        ctx_log = logger.bind(context=f"ctx:{context.context_id[:8]}")

        if context.context_id not in self._ledgers:
            self._ledgers[context.context_id] = Ledger()
            self._machines[context.context_id] = StateMachine()

        ledger = self._ledgers[context.context_id]
        machine = self._machines[context.context_id]

        # --- parse incoming A2A message ---
        user_text: str | None = None
        tool_results: list[dict] | None = None
        tools: list[dict] = self._tools.get(context.context_id, [])

        try:
            for part in context.message.parts:
                kind = part.WhichOneof("content")
                if kind == "text":
                    text = part.text
                    if "System:" in text and "\n\nUser:" in text:
                        split = text.split("\n\nUser:", 1)
                        system_text = split[0].replace("System:", "").strip()
                        user_text = split[1].strip()
                        ledger.add_system(system_text)
                    else:
                        user_text = text
                elif kind == "data":
                    data = MessageToDict(part.data)
                    if "tools" in data:
                        tools = data["tools"]
                        self._tools[context.context_id] = tools
                    elif "tool_results" in data:
                        tool_results = data["tool_results"]
        except Exception as exc:
            logger.warning(f"Message parse error: {exc}")
            user_text = context.get_user_input()

        # --- record tool results into ledger ---
        if tool_results:
            for tr in tool_results:
                name = tr.get("tool_name", tr.get("toolName", ""))
                content = tr.get("content", "")
                call_id = tr.get("tool_call_id", tr.get("toolCallId", f"unknown_{name}"))
                ledger.add_tool_result(name, content, call_id)

        if user_text:
            ledger.add_user_turn(user_text)

        # --- run state machine ---
        turn_ctx = TurnContext(
            ledger=ledger,
            tools=tools,
            model=self.model,
        )

        def tool_executor(tool_name: str, args: dict, call_id: str):
            # The A2A protocol is async/multi-turn: we send the tool call
            # as a response and wait for the evaluator's next message.
            # This callback is called by the state machine but we need to
            # enqueue the call and suspend — handled by the multi-turn loop below.
            raise _ToolCallRequested(tool_name, args, call_id)

        try:
            response_text = machine.run_turn(turn_ctx, tool_executor)
            parts = [new_text_part(response_text)]
        except _ToolCallRequested as tc_req:
            # Emit a tool call message and let the evaluator send results back
            tool_calls = [ToolCall(tool_name=tc_req.tool_name, arguments=tc_req.arguments)]
            parts = [new_data_part(ToolCallsData(tool_calls=tool_calls).model_dump())]
            ledger.add_tool_call(tc_req.tool_name, tc_req.arguments, tc_req.call_id)
        except NotImplementedError as ni:
            ctx_log.warning(f"Glassbox stub hit: {ni} — falling back to error response")
            parts = [new_text_part(f"[glassbox stub not yet implemented: {ni}]")]

        msg = new_message(
            parts=parts,
            context_id=context.context_id,
            role=Role.ROLE_AGENT,
        )
        await event_queue.enqueue_event(msg)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        self._ledgers.pop(context.context_id, None)
        self._tools.pop(context.context_id, None)
        self._machines.pop(context.context_id, None)


class _ToolCallRequested(Exception):
    def __init__(self, tool_name: str, arguments: dict, call_id: str) -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        self.call_id = call_id
