"""RESPOND state — finalize and deliver the agent's turn response.

Stufe 2: `finalize` is a deterministic cleanup pass (no LLM call — the draft
from VERIFY already follows the persona/format rules; a second rewrite would
only add variance). Refusal/policy-block generators remain later-Stufe points;
the state machine uses honest deterministic fallbacks until they land.
"""
from __future__ import annotations

import re

_REFUSAL_SYSTEM = """\
You are an honest in-car voice assistant. You must admit that the requested
capability is not available. Be friendly, brief, and honest.
Do NOT fabricate an alternative or workaround.
"""


def finalize(sanitized_draft: str, ctx: "TurnContext") -> str:  # type: ignore[name-defined]
    """Deterministic cleanup: strip markdown remnants, collapse whitespace."""
    text = sanitized_draft
    text = re.sub(r"[*_`#]+", "", text)          # markdown emphasis/heading chars
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)  # bullet markers
    text = re.sub(r"\s+", " ", text).strip()
    return text


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
