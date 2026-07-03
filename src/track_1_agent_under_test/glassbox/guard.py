"""
Fabrikations-Waechter — Stufe 5.

Vor jeder Antwort an den User:
  1. Faktische Behauptungen aus dem Antwort-Entwurf extrahieren (LLM, strukturiert)
  2. Jede Behauptung deterministisch gegen das Ledger pruefen (hat sie eine Quelle?)
  3. Ungedeckte Behauptung → blockiert, ersetzt durch Nachpruefung oder ehrliches Eingestaendnis

Dasselbe fuer Tool-Argumente: jeder Parameterwert braucht eine Ledger-Herkunft.
Nie selbst rechnen — Summen/Zeit/Distanz nur via deterministischer Helfer oder Tool-Results.

Compliance: prueft nur gegen Wahrheit + Ledger — niemals gegen Evaluator-Subscores.
"""
from __future__ import annotations

from dataclasses import dataclass

from .ledger import Ledger


@dataclass
class UnsupportedClaim:
    claim: str
    reason: str


class FabricationGuard:
    """Blocks any response that asserts facts without ledger provenance."""

    def check_response(self, draft: str, ledger: Ledger) -> list[UnsupportedClaim]:
        """
        Extract factual claims from draft and verify each has ledger backing.

        Stufe 5 implementation point.
        Returns list of claims that have NO provenance — caller must handle them.
        """
        raise NotImplementedError("FabricationGuard.check_response — implement in Stufe 5")

    def check_tool_arguments(
        self, tool_name: str, arguments: dict, ledger: Ledger
    ) -> list[UnsupportedClaim]:
        """
        Verify every argument value has a traceable source in the ledger.

        IDs, booking numbers, entity names must all be from tool results or user input.
        Nothing may be invented.

        Stufe 5 implementation point.
        """
        raise NotImplementedError("FabricationGuard.check_tool_arguments — implement in Stufe 5")

    def sanitize(self, draft: str, ledger: Ledger) -> str:
        """
        Remove or replace unsupported claims in draft.

        Safe path: if a claim cannot be verified, replace with honest uncertainty
        or a request for re-lookup, never with a fabricated value.
        """
        raise NotImplementedError("FabricationGuard.sanitize — implement in Stufe 5")
