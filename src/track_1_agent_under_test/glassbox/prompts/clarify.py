"""CLARIFY state — Stufe 6 disambiguation LLM adapters.

Two narrow LLM roles, both candidate-generation only (Lesson 1a):
  - extract_preference: structure the free-text user preferences already in the
    ledger into {default, prohibited} for ONE (tool, argument) slot. The engine
    decides whether/how to apply it.
  - generate_clarification_question: phrase the single question the engine asks
    when ≥2 valid candidates remain (only when the engine says to ask).
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .. import llm
from . import common


class PreferenceResolution(BaseModel):
    """Structured reading of the user's learned preferences for one slot."""
    default: str | None = None          # the value the preference implies, or null
    prohibited: list[str] = Field(default_factory=list)  # values the user never wants


_EXTRACT_SYSTEM = """\
You read the user's LEARNED PREFERENCES (already retrieved via get_user_preferences \
and shown in the conversation) and structure them for ONE specific tool argument.

Rules:
- default: the concrete value the preferences imply for this argument, copied from \
the preference text (e.g. "Default to open the sunroof is 50%" → "50"). If the \
preferences say nothing about this argument, return null. NEVER invent a value.
- prohibited: values the preferences explicitly rule out (e.g. "never fully" → \
"100"). Empty if none.
- Read ONLY from the stated preferences. Do not use outside knowledge or guess.
"""


def extract_preference(ctx: "TurnContext", tool: str, argument: str) -> "PreferenceResolution":  # type: ignore[name-defined]
    """Structure the ledger's user-preference text for one (tool, argument)."""
    system = (
        _EXTRACT_SYSTEM
        + "\n\n# Task context\n" + common.task_system_text(ctx.ledger)
    )
    transcript = common.render_transcript(ctx.ledger, include_tools=True)
    messages = [{
        "role": "user",
        "content": (
            f"# Conversation (includes retrieved preferences)\n{transcript}\n\n"
            f"Structure the learned preference for tool '{tool}', argument "
            f"'{argument}'. Return default=null if the preferences are silent on it."
        ),
    }]
    from ..disambiguation import PreferenceSlot
    res = llm.call_structured(messages, PreferenceResolution, model=ctx.model, system=system)
    return PreferenceSlot(default=res.default, prohibited=list(res.prohibited))


_CLARIFY_SYSTEM = """\
You are an in-car voice assistant that needs ONE piece of information to proceed.
Generate a natural, concise clarification question that:
- Presents exactly the ambiguous options to the user
- Does NOT make assumptions or pick a default
- Sounds natural for text-to-speech
- Is a single question (not a list)
"""


class ClarificationQuestion(BaseModel):
    question: str


def generate_clarification_question(ambiguity_reason: str, candidates: list, model: str) -> str:
    """Phrase the single clarification question (only when the engine asks)."""
    cands = ", ".join(str(c) for c in candidates) if candidates else "(none enumerated)"
    messages = [{
        "role": "user",
        "content": (
            f"Ambiguity: {ambiguity_reason}\nValid options: {cands}\n"
            "Ask the user one natural question to resolve it."
        ),
    }]
    res = llm.call_structured(messages, ClarificationQuestion, model=model, system=_CLARIFY_SYSTEM)
    return res.question


try:
    from ..state_machine import TurnContext
except ImportError:
    pass
