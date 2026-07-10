"""VERIFY state — draft response from ledger facts before the guard sanitizes it.

Stufe 2 provides the functional draft call (needed for a runnable turn);
Stufe 5 adds the FabricationGuard claim-check on top of this draft.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .. import llm
from . import common


class ClaimCheck(BaseModel):
    """One factual claim in the reply plus its ledger source (Stufe-7 self-check)."""
    value: str = Field(description="The specific asserted value (e.g. '42 minutes', '22')")
    sentence: str = Field(description="The full sentence in the reply that states it")
    source: str = Field(
        default="",
        description="Verbatim quote from a tool result or user message that backs this "
                    "value. Use 'inferred' if it follows from context, or leave empty if "
                    "there is no ledger source (then do NOT state the claim).",
    )


class Draft(BaseModel):
    # Forced self-check FIRST (Stufe 7): the model enumerates its factual claims with
    # their ledger sources before writing the reply. Parsed deterministically by the
    # Auditor — no separate audit LLM call. Empty for pure confirmations.
    claims: list[ClaimCheck] = Field(default_factory=list)
    response: str


_DRAFT_SYSTEM = """\
# Role
You are the response drafting layer of an in-car voice assistant. \
You produce spoken text to be delivered to the driver.

# Context
You receive the full conversation including tool calls and results.

# Task
First run a self-check: in `claims`, list every specific factual value your reply \
will assert (numbers, times, distances, temperatures, states, availability) together \
with the verbatim ledger source (a quote from a tool result or user message) that \
backs it. If a value has NO ledger source, do not state it in the reply at all. \
Then write a concise spoken reply in `response`, based strictly on facts present in \
the conversation and tool results.

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
- UNKNOWN VALUES: If a tool result field shows "unknown", treat it as MISSING \
INFORMATION. Do NOT refuse the user's request based on an "unknown" status of an \
unrelated field. For example, if fog_lights is "unknown" but the user asked about \
high beams, proceed with the high beam request and mention the fog light status is \
unavailable if relevant — do not block the action. Only report "unknown" honestly \
when the user asks specifically about that field.
- SUCCESSFUL ACTIONS: If tool calls succeeded (status: SUCCESS), your reply MUST \
acknowledge them. Never claim you cannot do something that the tool results show \
was already done successfully.
"""


def draft_response(ctx: "TurnContext") -> "Draft":  # type: ignore[name-defined]
    """Draft the turn's reply plus its self-check claims from the ledger (LLM, temp 0)."""
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
            "Self-check your factual claims in `claims`, then write the assistant's "
            "spoken reply to the last user message, based strictly on the facts above."
        ),
    }]
    return llm.call_structured(messages, Draft, model=ctx.model, system=system)


try:
    from ..state_machine import TurnContext
except ImportError:
    pass
