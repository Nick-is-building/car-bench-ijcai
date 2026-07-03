"""RESPOND state — finalize and deliver the agent's turn response."""
from __future__ import annotations


_FINALIZE_SYSTEM = """\
You are an in-car voice assistant delivering a final response.
Personality: joyful, enthusiastic, informal, concise.
Rules:
- No markdown, no lists, no non-speakable characters
- Use metric units (km, m, °C, 24h time)
- 1-2 sentences maximum for confirmations
- Do NOT add information not present in the sanitized draft
"""

_REFUSAL_SYSTEM = """\
You are an honest in-car voice assistant. You must admit that the requested
capability is not available. Be friendly, brief, and honest.
Do NOT fabricate an alternative or workaround.
"""


def finalize(sanitized_draft: str, ctx: "TurnContext") -> str:  # type: ignore[name-defined]
    """
    Finalize the sanitized draft into a natural spoken response.

    Stufe 5 implementation point.
    """
    raise NotImplementedError("respond.finalize — implement in Stufe 5")


def generate_honest_refusal(ctx: "TurnContext") -> str:  # type: ignore[name-defined]
    """
    Generate natural honest refusal (called when capability is uncovered).

    Stufe 3 implementation point.
    """
    raise NotImplementedError("respond.generate_honest_refusal — implement in Stufe 3")


def generate_policy_block(ctx: "TurnContext") -> str:  # type: ignore[name-defined]
    """
    Generate natural policy-block response.

    Stufe 4 implementation point.
    """
    raise NotImplementedError("respond.generate_policy_block — implement in Stufe 4")


try:
    from ..state_machine import TurnContext
except ImportError:
    pass
