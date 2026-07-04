"""VERIFY state — draft response from ledger facts before the guard sanitizes it.

Stufe 2 provides the functional draft call (needed for a runnable turn);
Stufe 5 adds the FabricationGuard claim-check on top of this draft.
"""
from __future__ import annotations

from pydantic import BaseModel

from .. import llm
from . import common


class Draft(BaseModel):
    response: str


_DRAFT_SYSTEM = """\
# Role
You are the response drafting layer of an in-car voice assistant. \
You produce spoken text to be delivered to the driver.

# Context
You receive the full conversation including tool calls and results.

# Task
Write a concise spoken reply to the last user message, \
based strictly on facts present in the conversation and tool results.

# Format
- 1-2 short sentences for confirmations; only as long as needed otherwise.
- No markdown, no bullet lists, no non-speakable characters.
- Metric units (km, m, degrees Celsius) and 24h time format.

# Prohibitions
- Never state a fact, value, or state not present in the tool results or \
conversation. Invented values are fabrications.
- Do not mention an action or state change unless the corresponding tool call \
appears in the conversation history.
- Never predict future actions ("I will", "I'll", "I'd") — report only what \
has already happened.
- Do not perform arithmetic; repeat only numbers from tool results or conversation.
- If a tool result reports an error or unknown value, say so honestly.
"""


def draft_response(ctx: "TurnContext") -> str:  # type: ignore[name-defined]
    """Draft the turn's response from the ledger (LLM, temp 0)."""
    system = (
        _DRAFT_SYSTEM
        + "\n" + common.SEMANTIC_POLICY_OBLIGATIONS
        + "\n\n# Task context\n" + common.task_system_text(ctx.ledger)
    )
    transcript = common.render_transcript(ctx.ledger, include_tools=True)
    messages = [{
        "role": "user",
        "content": (
            f"# Conversation (tool calls/results included)\n{transcript}"
            f"{common.render_policy_notes(ctx.policy_notes)}\n\n"
            "Write the assistant's spoken reply to the last user message, "
            "based strictly on the facts above."
        ),
    }]
    draft = llm.call_structured(messages, Draft, model=ctx.model, system=system)
    return draft.response


try:
    from ..state_machine import TurnContext
except ImportError:
    pass
