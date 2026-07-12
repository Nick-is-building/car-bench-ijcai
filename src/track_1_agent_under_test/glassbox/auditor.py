"""
Auditor — Stufe 7 (bewusst schlank, siehe ADR-0006).

Gezielte Selbstpruefung an zwei Stellen:
  1. VOR zustandsaendernden Tool-Calls: bereits realisiert durch Stufe 4
     (PolicyChecker Pre-Flight) + Stufe 5 (FabricationGuard.check_tool_arguments),
     die in jeder PLAN-Runde vor EXECUTE laufen. KEIN eigener Auditor-Code noetig.
  2. VOR der finalen Antwort: `pre_response_check`. Das RESPOND-/VERIFY-Prompt
     erzwingt eine Selbstpruefung (jede faktische Behauptung mit Ledger-Quelle,
     DANN die Antwort). Der Auditor parst diese Selbstpruefung DETERMINISTISCH —
     KEIN zusaetzlicher LLM-Aufruf.

Bei Zweifel konservativer Default: die ungedeckte Behauptung wird durch ein ehrliches
Eingestaendnis ersetzt (handeln/behaupten nur mit Deckung).

Compliance: prueft nur gegen Wahrheit + Ledger — nie gegen nachgebildete
Evaluator-Subscores, kein iteratives Reparieren gegen die Wertung.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from loguru import logger as _log

from .ledger import Ledger
from .guard import _ledger_text_corpus, _value_in_ledger


_HONEST_ADMISSION = "I'm sorry, I don't have confirmed information about that."


@dataclass
class AuditResult:
    passed: bool
    issues: list[str] = field(default_factory=list)
    conservative_action: str = ""     # "admit" when a claim was replaced, else ""
    safe_text: str = ""               # response with unsupported claims replaced


class Auditor:
    """Deterministic pre-response self-check (no LLM call of its own)."""

    def pre_response_check(self, draft: "Draft", ledger: Ledger,
                           policy_notes: list[str] | tuple[str, ...] = ()) -> AuditResult:  # type: ignore[name-defined]
        """Verify each self-declared factual claim against the ledger.

        A numeric claim whose value has no ledger provenance (or whose declared
        source quote is not in the ledger) is unsupported → its sentence is
        replaced by an honest admission. String-only claims are left untouched
        (they may be valid paraphrases of tool results — Null-FP discipline).

        Values and quotes from `policy_notes` count as supported: those notes
        are emitted by the deterministic PolicyChecker (e.g. LLM-POL:012 zone
        temperature difference) and derive from ledger state — the LLM is
        REQUIRED to surface them. Without this, the Auditor kills the very
        obligation the policy is asking it to communicate (dis_38 root cause).
        Only the Auditor uses this extended corpus; the pre-execute
        FabricationGuard keeps the ledger-only corpus to prevent fabrication.
        """
        corpus = _ledger_text_corpus(ledger)
        if policy_notes:
            corpus = corpus + " " + " ".join(policy_notes)
        safe = draft.response
        issues: list[str] = []

        for claim in draft.claims:
            # Only numeric claims are deterministically falsifiable here.
            if not re.search(r"\d", claim.value):
                continue
            value_ok = _value_in_ledger(claim.value, corpus)
            # Value in corpus is sufficient — the declared source is a
            # self-annotation and often a paraphrase of the actual ledger
            # quote (LLM formatting variance). Only fall back to source
            # matching when the value itself is not backed.
            if value_ok:
                continue
            src = claim.source.strip().lower()
            source_declared = src not in ("", "inferred", "context", "none")
            source_ok = source_declared and claim.source.strip() in corpus
            if source_ok:
                continue
            issues.append(f"{claim.value!r} unsupported")
            if claim.sentence and claim.sentence in safe:
                safe = safe.replace(claim.sentence, _HONEST_ADMISSION).strip()

        if issues:
            _log.info("Auditor.pre_response: unsupported claims replaced", issues=issues)
        return AuditResult(
            passed=not issues,
            issues=issues,
            conservative_action="admit" if issues else "",
            safe_text=safe,
        )


try:
    from .prompts.verify import Draft
except ImportError:
    pass
