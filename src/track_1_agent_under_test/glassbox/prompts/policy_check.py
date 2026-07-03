"""POLICY_CHECK state — generate user message when a policy blocks an action."""
from __future__ import annotations


_BLOCK_SYSTEM = """\
You are an in-car voice assistant. A policy rule prevents the requested action.
Generate a natural, honest response that:
- Explains WHY the action is not possible right now (reference the rule, not internal code)
- Suggests the correct precondition if applicable
- Does NOT perform the blocked action
- Sounds natural for text-to-speech
"""


def generate_policy_block(violations: list, intent: dict, model: str) -> str:
    """
    Generate a natural policy-block explanation.

    Stufe 4 implementation point.
    """
    raise NotImplementedError("policy_check.generate_policy_block — implement in Stufe 4")
