"""INTAKE state — extract structured intent from the user's message. Stufe 2."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .. import llm
from . import common


class ToolParams(BaseModel):
    tool: str
    params: list[str] = Field(default_factory=list)


class ValueAmbiguity(BaseModel):
    """A state-changing tool argument the user did NOT pin down this turn.

    Candidate generation only — Stufe 6 code decides the value. NEVER pick or
    rank a value here; only flag which (tool, argument) is under-specified.
    """
    tool: str                       # exact catalog tool name the value flows into
    argument: str                   # argument name on that tool (from its schema)
    user_stated: bool = False       # did the user explicitly give this value this turn?
    candidates: list[str] = Field(default_factory=list)  # known valid options, if a closed set


class Intent(BaseModel):
    user_request_summary: str
    required_tools: list[str] = Field(default_factory=list)
    required_params: list[ToolParams] = Field(default_factory=list)
    required_but_missing_tools: list[str] = Field(default_factory=list)
    is_state_changing: bool
    is_ambiguous: bool
    ambiguity_reason: str = ""
    clarification_question: str = ""
    value_ambiguities: list[ValueAmbiguity] = Field(default_factory=list)


_SYSTEM = """\
# Role
You are the intent extraction layer of an in-car voice assistant. \
Your output drives the planning layer — accuracy is critical.

# Context
You receive the full conversation and the catalog of available tools.

# Task
Extract a structured intent for the LAST user message only.

## Field rules
- user_request_summary: one sentence describing what the user wants right now.
- required_tools: exact tool names from the catalog that fulfilling this request \
will definitely call. CRITICAL: copy names character-for-character from the catalog. \
Never abbreviate, guess, or invent names. If unsure, leave the list empty.
- required_params: for each required tool, list ONLY parameter names whose values \
the user EXPLICITLY stated (e.g. user said "50%" → list "percentage"). \
Do NOT list parameters derived from vehicle context (location, time, car state). \
Never include values — list only parameter names, exactly as in the tool schema.
- required_but_missing_tools: tools the request NEEDS but that are absent from \
the catalog. List only if a step is genuinely impossible without a specific tool \
that is not present. Leave empty otherwise.
- is_state_changing: true if fulfilling the request changes vehicle or world state.
- is_ambiguous: true ONLY for genuine GOAL or TOOL ambiguity — the request could \
mean two different actions and nothing (preferences, defaults, car state) can decide \
which. An under-specified ARGUMENT VALUE of an otherwise clear action (e.g. "open the \
sunroof" without a percentage) is NOT is_ambiguous — flag it in value_ambiguities \
instead, so it can be resolved deterministically. Do not flag requests as ambiguous \
when a sensible default reading exists.
- ambiguity_reason / clarification_question: fill only when is_ambiguous=true; \
clarification_question must be a single natural, speakable question.
- value_ambiguities: for each state-changing tool this request will call, list any \
argument whose value the user did NOT explicitly pin down THIS turn (e.g. "open the \
sunroof" gives no percentage). Set tool + argument to the exact catalog names and \
user_stated=false. If the user DID state the value, set user_stated=true (or omit the \
entry). candidates: only fill when the valid options are a small closed set you can \
read from the conversation/tool results — NEVER invent, pick, or rank a value. The \
value itself is decided later by deterministic code, not here.

# Prohibitions
- Never list a tool name that is not literally in the catalog.
- Never mark a request ambiguous when the conversation already resolves it.
- Never choose or guess a value for an under-specified argument — only flag it in \
value_ambiguities.
"""


def extract_intent(ctx: "TurnContext", rebuttal_note: str = "") -> dict:  # type: ignore[name-defined]
    """Extract structured intent from the last user message (LLM, temp 0).

    rebuttal_note: if set (INTAKE-REBUTTAL), appended to the user message so the
    LLM knows some required_tools names were not found and gets catalog candidates.
    """
    system = (
        _SYSTEM
        + "\n\n# Task context\n" + common.task_system_text(ctx.ledger)
        + "\n\n# Available tools\n" + common.render_tool_catalog(ctx.tools)
    )
    transcript = common.render_transcript(ctx.ledger, include_tools=False)
    base_content = f"# Conversation\n{transcript}\n\nExtract the intent of the last user message."
    if rebuttal_note:
        base_content += f"\n\n# CATALOG CORRECTION\n{rebuttal_note}"
    messages = [{"role": "user", "content": base_content}]
    intent = llm.call_structured(messages, Intent, model=ctx.model, system=system)
    return intent.model_dump()


try:
    from ..state_machine import TurnContext
except ImportError:
    pass
