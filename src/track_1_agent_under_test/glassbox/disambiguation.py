"""
Disambiguierungs-Motor — Stufe 6.

Zwei Task-Untertypen:
  disambiguation_internal — NIE fragen, intern aus Praeferenzen/Kontext loesen
  disambiguation_user     — MUSS fragen, wenn mehr als ein gueltiger Kandidat bleibt

Prioritaets-Reihenfolge (aus wiki.md):
  0. Policy-Regeln
  1. Expliziter User-Request
  2. Gelernte Praeferenzen (get_user_preferences)
  3. Heuristische Regeln / Defaults
  4. Kontext (Fahrzeugzustand, Ort, Zeit)
  5. (nur wenn alle obigen scheitern) User-Clarification

Falsche Wahl in beide Richtungen setzt r_user_end_conversation = 0.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DisambiguationResult:
    needs_user_clarification: bool
    question: str = ""
    resolved_intent: dict | None = None
    chosen_candidate: str = ""
    reasoning: str = ""


class DisambiguationEngine:
    """
    Resolves ambiguous intents deterministically before planning.

    Stufe 6 implementation point.
    """

    def resolve(self, ctx: "TurnContext") -> DisambiguationResult:  # type: ignore[name-defined]
        """
        Attempt to resolve ambiguity without asking the user.

        If a single valid candidate remains after applying all internal
        disambiguation priorities (0–4), return it resolved.
        If ≥2 valid candidates remain and the task is user-type, return
        needs_user_clarification=True with a focused clarification question.

        Never rank valid candidates — only eliminate invalid ones.
        """
        raise NotImplementedError("DisambiguationEngine.resolve — implement in Stufe 6")

    def _apply_policy_rules(self, candidates: list, ledger: "Ledger") -> list:  # type: ignore[name-defined]
        """Priority 0: eliminate candidates that violate a policy."""
        raise NotImplementedError

    def _apply_explicit_request(self, candidates: list, intent: dict) -> list:
        """Priority 1: user explicitly named a preference."""
        raise NotImplementedError

    def _apply_learned_preferences(self, candidates: list, preferences: dict) -> list:
        """Priority 2: apply get_user_preferences result from ledger."""
        raise NotImplementedError

    def _apply_heuristics(self, candidates: list, intent: dict) -> list:
        """Priority 3: defaults (e.g. fastest route for multi-stop)."""
        raise NotImplementedError

    def _apply_context(self, candidates: list, ledger: "Ledger") -> list:  # type: ignore[name-defined]
        """Priority 4: vehicle state, location, time from tool results."""
        raise NotImplementedError


try:
    from .state_machine import TurnContext
    from .ledger import Ledger
except ImportError:
    pass
