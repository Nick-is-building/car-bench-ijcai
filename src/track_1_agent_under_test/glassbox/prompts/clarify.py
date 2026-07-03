"""CLARIFY state — generate focused clarification question for user-type disambiguation."""
from __future__ import annotations


_CLARIFY_SYSTEM = """\
You are an in-car voice assistant that needs ONE piece of information to proceed.
Generate a natural, concise clarification question that:
- Presents exactly the ambiguous options to the user
- Does NOT make assumptions or pick a default
- Sounds natural for text-to-speech
- Is a single question (not a list)
"""


def generate_clarification_question(ambiguity_reason: str, candidates: list, model: str) -> str:
    """
    Generate a focused clarification question.

    Only call when DisambiguationEngine confirms user clarification is required.
    Stufe 6 implementation point.
    """
    raise NotImplementedError("clarify.generate_clarification_question — implement in Stufe 6")
