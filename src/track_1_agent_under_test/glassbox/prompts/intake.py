"""INTAKE state — extract structured intent from user message."""
from __future__ import annotations

from pydantic import BaseModel

from ..llm import call_structured


class Intent(BaseModel):
    user_request_summary: str
    required_tools: list[str]
    required_params: dict[str, list[str]]
    is_ambiguous: bool
    ambiguity_reason: str = ""
    is_state_changing: bool


_SYSTEM = """\
You are the intent extraction layer of an in-car voice assistant.
Given a user message and the list of available tools, extract a structured intent.
Be conservative: only list tools you are CERTAIN the user's request requires.
If the required tool or parameter is unclear, set is_ambiguous=true.
"""


def extract_intent(ctx: "TurnContext") -> dict:  # type: ignore[name-defined]
    """
    Extract structured intent from the last user message.
    Returns dict matching Intent schema.

    Stufe 2 implementation point (called by StateMachine).
    """
    raise NotImplementedError("intake.extract_intent — implement in Stufe 2")


try:
    from ..state_machine import TurnContext
except ImportError:
    pass
