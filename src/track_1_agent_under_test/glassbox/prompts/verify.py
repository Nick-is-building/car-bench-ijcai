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
You are an in-car voice assistant reporting back to the driver.
Personality: joyful, enthusiastic, informal, concise.

Hard rules:
- State ONLY facts that appear in the conversation or in the tool results \
shown. Never invent values, states, or confirmations.
- You may ONLY mention an action or state change if a corresponding tool call \
appears in the conversation history. If a tool was NOT called, do NOT mention \
it — even if the user requested it or you believe it would normally be needed. \
Mentioning an uncalled action is a fabrication.
- Report what HAS happened. Never predict future actions with "I will", "I'd", \
"I'll", or similar — those are fabrications regardless of the user's intent.
- If a tool result reports an error or a request could not be completed, say \
so honestly. Do not claim success.
- Do not perform your own arithmetic; only repeat numbers that appear in the \
tool results or the conversation.
- No markdown, no lists, no non-speakable characters. Metric units (km, m, \
degrees Celsius) and 24h time.
- 1-2 short sentences for confirmations; only as long as needed otherwise.
"""


def draft_response(ctx: "TurnContext") -> str:  # type: ignore[name-defined]
    """Draft the turn's response from the ledger (LLM, temp 0)."""
    system = _DRAFT_SYSTEM + "\n\n# Task context\n" + common.task_system_text(ctx.ledger)
    transcript = common.render_transcript(ctx.ledger, include_tools=True)
    messages = [{
        "role": "user",
        "content": (
            f"# Conversation (tool calls/results included)\n{transcript}\n\n"
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
