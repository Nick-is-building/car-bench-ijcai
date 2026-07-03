"""CAPABILITY_CHECK state — generate honest refusal when capability is missing."""
from __future__ import annotations

from pydantic import BaseModel

from .. import llm


class Refusal(BaseModel):
    response: str


_REFUSAL_SYSTEM = """\
You are an honest in-car voice assistant. The system has determined that the user's
request CANNOT be fulfilled because a required capability is not available in this car.

Rules:
- Clearly and honestly admit the limitation in one or two sentences.
- Do NOT fabricate a workaround or suggest an alternative that the car cannot do.
- Do NOT attempt or mention any tool call.
- Sound natural for text-to-speech: no markdown, no lists, no bullet points.
- Be friendly and concise.
"""


def generate_honest_refusal(capability_result: str, intent: dict, model: str) -> str:
    """Generate a natural honest refusal for an uncovered capability (LLM, temp 0)."""
    request_summary = intent.get("user_request_summary", "your request")
    missing_tools = (
        intent.get("required_but_missing_tools", [])
        + [t for t in intent.get("required_tools", []) if t]
    )
    missing_str = ", ".join(missing_tools) if missing_tools else "a required feature"

    user_content = (
        f"The user asked: {request_summary!r}\n"
        f"Missing capability: {missing_str}\n"
        "Generate the assistant's spoken response admitting this limitation."
    )
    refusal = llm.call_structured(
        [{"role": "user", "content": user_content}],
        Refusal,
        model=model,
        system=_REFUSAL_SYSTEM,
    )
    return refusal.response
