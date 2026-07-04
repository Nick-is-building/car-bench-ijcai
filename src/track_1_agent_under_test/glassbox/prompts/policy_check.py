"""POLICY_CHECK state — generate user message when a policy blocks an action."""
from __future__ import annotations

from pydantic import BaseModel

from .. import llm


class PolicyBlock(BaseModel):
    response: str


_BLOCK_SYSTEM = """\
You are an in-car voice assistant. A vehicle policy rule prevents the requested
action from being executed. Generate a natural, honest spoken response that:
- Explains WHY the action is not possible right now (state the rule in user \
terms, never internal code or rule IDs)
- Suggests the correct precondition or alternative if one exists
- Does NOT claim the blocked action was performed
- Sounds natural for text-to-speech: no markdown, no lists, 1-2 short sentences
- Is friendly and concise
"""


def generate_policy_block(violations: list, intent: dict, model: str) -> str:
    """Generate a natural policy-block explanation (LLM, temp 0)."""
    request_summary = intent.get("user_request_summary", "the request")
    reasons = "\n".join(f"- {v.reason}" for v in violations) or "- a vehicle policy rule"
    user_content = (
        f"The user asked: {request_summary!r}\n"
        f"Blocking policy reason(s):\n{reasons}\n"
        "Generate the assistant's spoken response explaining this honestly."
    )
    block = llm.call_structured(
        [{"role": "user", "content": user_content}],
        PolicyBlock,
        model=model,
        system=_BLOCK_SYSTEM,
    )
    return block.response
