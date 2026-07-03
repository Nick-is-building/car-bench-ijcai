"""
Auditor — Stufe 7.

Gezielte Selbstpruefung an genau zwei Stellen:
  1. Vor zustandsaendernden Tool-Calls: passt der Plan zu Ledger und Policies?
  2. Vor der finalen Antwort: enthaelt sie eine Behauptung ohne Ledger-Deckung?

Nie bei jedem Turn (Kosten/Varianz). Bei Zweifel konservativer Default: nachfragen
oder ablehnen statt handeln — der Benchmark belohnt ehrliches Zoegern.

Compliance: prueft nur gegen Wahrheit, Ledger und 19 Policies — nie gegen
nachgebildete Evaluator-Subscores, kein iteratives Reparieren gegen Wertung.
"""
from __future__ import annotations

from dataclasses import dataclass

from .ledger import Ledger


@dataclass
class AuditResult:
    passed: bool
    issues: list[str]
    conservative_action: str  # what to do if failed: "ask" | "refuse" | "re-lookup"


class Auditor:
    """
    Two-point self-check: pre-action and pre-response.

    Stufe 7 implementation point.
    """

    def pre_action_check(
        self, plan: list[dict], ledger: Ledger
    ) -> AuditResult:
        """
        Verify the plan is consistent with ledger state and all 19 policies
        before any state-changing tool call is sent.

        Semantic check the deterministic PolicyChecker might miss.
        Conservative default on failure: refuse or ask.
        """
        raise NotImplementedError("Auditor.pre_action_check — implement in Stufe 7")

    def pre_response_check(
        self, response_draft: str, ledger: Ledger
    ) -> AuditResult:
        """
        Verify the response draft contains no claims without ledger backing.

        Catches semantic fabrication the FabricationGuard regex/struct check misses.
        Uses a forced self-check section in the RESPOND prompt, not a second LLM call
        unless strictly necessary.
        """
        raise NotImplementedError("Auditor.pre_response_check — implement in Stufe 7")
