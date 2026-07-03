"""VERIFY state — draft response from tool results before guard sanitizes it."""
from __future__ import annotations


_DRAFT_SYSTEM = """\
You are an in-car voice assistant summarizing what was just done.
Draft a response based ONLY on tool results that are explicitly provided.
Do NOT invent values, states, or confirmations not present in the results.
Do NOT include markdown, lists, or non-speakable characters.
"""


def draft_response(ctx: "TurnContext") -> str:  # type: ignore[name-defined]
    """
    Draft a response from the ledger's tool results.
    The FabricationGuard will sanitize this draft next.

    Stufe 5 implementation point.
    """
    raise NotImplementedError("verify.draft_response — implement in Stufe 5")


try:
    from ..state_machine import TurnContext
except ImportError:
    pass
