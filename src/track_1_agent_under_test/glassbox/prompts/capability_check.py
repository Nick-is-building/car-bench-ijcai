"""CAPABILITY_CHECK state — generate honest refusal when capability is missing."""
from __future__ import annotations


_REFUSAL_SYSTEM = """\
You are an honest in-car voice assistant. The system has determined that the user's
request CANNOT be fulfilled because the required capability is not available.
Generate a natural, friendly, and honest response that:
- Clearly admits the limitation (do NOT fabricate a workaround)
- Does NOT attempt any tool call
- Sounds natural for text-to-speech (no markdown, no lists)
- Is concise (1-2 sentences)
"""


def generate_honest_refusal(capability_result: str, intent: dict, model: str) -> str:
    """
    Generate a natural honest refusal for uncovered capability.

    Stufe 3 implementation point.
    """
    raise NotImplementedError("capability_check.generate_honest_refusal — implement in Stufe 3")
