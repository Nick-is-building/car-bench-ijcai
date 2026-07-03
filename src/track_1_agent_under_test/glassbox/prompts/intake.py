"""INTAKE state — extract structured intent from the user's message. Stufe 2."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .. import llm
from . import common


class ToolParams(BaseModel):
    tool: str
    params: list[str] = Field(default_factory=list)


class Intent(BaseModel):
    user_request_summary: str
    required_tools: list[str] = Field(default_factory=list)
    required_params: list[ToolParams] = Field(default_factory=list)
    is_state_changing: bool
    is_ambiguous: bool
    ambiguity_reason: str = ""
    clarification_question: str = ""


_SYSTEM = """\
You are the intent extraction layer of an in-car voice assistant.
Given the conversation and the list of available tools, extract a structured intent \
for the LAST user message.

Rules:
- user_request_summary: one sentence, what the user wants now.
- required_tools: only tools from the catalog you are CERTAIN the request needs. \
If the needed capability is not in the catalog, leave the list empty rather than \
naming a similar-sounding tool.
- required_params: for each required tool, the parameters the request constrains.
- is_state_changing: true if fulfilling the request changes vehicle/world state.
- is_ambiguous: true ONLY if the request cannot be acted on without more \
information AND the conversation does not already contain that information. \
Do not flag requests as ambiguous when a sensible reading exists.
- If is_ambiguous, write ambiguity_reason and a single natural, speakable \
clarification_question presenting the options. Otherwise leave both empty.
"""


def extract_intent(ctx: "TurnContext") -> dict:  # type: ignore[name-defined]
    """Extract structured intent from the last user message (LLM, temp 0)."""
    system = (
        _SYSTEM
        + "\n\n# Task context\n" + common.task_system_text(ctx.ledger)
        + "\n\n# Available tools\n" + common.render_tool_catalog(ctx.tools)
    )
    transcript = common.render_transcript(ctx.ledger, include_tools=False)
    messages = [{
        "role": "user",
        "content": f"# Conversation\n{transcript}\n\nExtract the intent of the last user message.",
    }]
    intent = llm.call_structured(messages, Intent, model=ctx.model, system=system)
    return intent.model_dump()


try:
    from ..state_machine import TurnContext
except ImportError:
    pass
