"""
Fabrikations-Waechter — Stufe 5.

Vor jeder Antwort an den User:
  1. Faktische Behauptungen aus dem Antwort-Entwurf extrahieren (LLM, strukturiert)
  2. Jede Behauptung deterministisch gegen das Ledger pruefen (hat sie eine Quelle?)
  3. Ungedeckte Behauptung → blockiert, ersetzt durch ehrliches Eingestaendnis

Dasselbe fuer Tool-Argumente (state-changing Calls):
  C2: jeder numerische Wert braucht eine Ledger-Herkunft (deterministisch)
  C3: LLM-Attribution → deterministischer Gate: Zitat im Ledger + Zitat erwaehnt Ziel-Entitaet
  C4: Einstimmigkeits-Gate — zweiter identischer Call bei UNCERTAIN aus C3

Guard-Interface (C1): alle Pruefschiichten geben GuardResult zurueck.
Semantik: PASS/BLOCK sind final; UNCERTAIN eskaliert an die StateMachine (Re-Plan oder Senke).

Compliance: prueft nur gegen Wahrheit + Ledger — niemals gegen Evaluator-Subscores.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from loguru import logger as _log
from pydantic import BaseModel, Field

from . import llm, prompts
from .ledger import Ledger


# ---------------------------------------------------------------------------
# C1 — Guard-Interface
# ---------------------------------------------------------------------------

GuardVerdict = Literal["PASS", "BLOCK", "UNCERTAIN"]


@dataclass
class GuardResult:
    """Uniform decision record for every guard layer."""
    verdict: GuardVerdict
    layer: str
    reason: str = ""


# ---------------------------------------------------------------------------
# LLM-Schemas fuer C3 (Argument-Attribution) und C5 (Claim-Extraktion)
# ---------------------------------------------------------------------------

class ArgumentAttribution(BaseModel):
    argument_name: str
    argument_value: str = Field(description="The value being attributed, as a string")
    source_quote: str = Field(
        description="Literal verbatim quote from the conversation that provides this value. "
                    "Empty string if no clear source exists."
    )
    target_entity: str = Field(
        description="The entity or concept the source quote is referring to "
                    "(e.g. 'sunroof', 'sunshade', 'navigation'). 'unknown' if unclear."
    )


class AttributionResponse(BaseModel):
    attributions: list[ArgumentAttribution]


class FactualClaim(BaseModel):
    value: str = Field(
        description="The specific value being asserted (e.g. '42 minutes', '22°C', 'unavailable')"
    )
    sentence: str = Field(description="The full sentence containing this claim")


class ClaimExtractionResponse(BaseModel):
    claims: list[FactualClaim]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_STATE_CHANGING_PREFIXES = (
    "open_", "close_", "set_", "activate_", "deactivate_", "send_",
    "start_", "stop_", "turn_", "change_", "update_", "add_",
    "delete_", "replace_", "edit_",
)

_COMMON_VERBS = frozenset({
    "open", "close", "set", "get", "activate", "deactivate", "send",
    "start", "stop", "turn", "change", "update", "navigate", "add",
    "delete", "replace", "edit", "new", "current", "all", "main", "by",
})

_ROUTE_TOOLS = frozenset({
    "set_new_navigation", "add_waypoint", "replace_waypoint",
    "delete_waypoint",
})

# Known in-car entities for C3 Gate-2 competing-entity detection.
# Only flag binding confusion when the source quote explicitly mentions a different entity.
_KNOWN_ENTITIES = frozenset({
    "sunroof", "sunshade", "window", "seat", "heater",
    "climate", "fan", "defrost", "navigation", "radio",
    "light", "fog", "mirror", "door", "trunk", "temperature",
})

_ROUTE_CHOICE_WORDS = ("fastest", "quickest", "shortest", "optimal", "direct")


def _is_state_changing(tool_name: str) -> bool:
    return any(tool_name.startswith(p) for p in _STATE_CHANGING_PREFIXES)


def _tool_entity_synonyms(tool_name: str) -> list[str]:
    """Extract entity nouns from a tool name for binding verification."""
    parts = tool_name.lower().split("_")
    return [p for p in parts if p not in _COMMON_VERBS and len(p) > 2]


def _ledger_text_corpus(ledger: Ledger) -> str:
    """All user messages and tool results as one searchable string."""
    parts: list[str] = []
    for e in ledger.entries:
        if e.kind in ("user", "system"):
            parts.append(str(e.content))
        elif e.kind == "tool_result":
            parts.append(str(e.content))
    return " ".join(parts)


def _value_in_ledger(value: str | float | int, corpus: str) -> bool:
    """True if the value is backed by the ledger corpus.

    Clean numbers are matched in their int/float forms. A value that carries
    digits alongside a unit or symbol ("42 minutes", "50%", "22°C") is matched
    by its numeric TOKENS, not by literal substring: the ledger often stores the
    bare number (a dict field like {"eta_minutes": 42}) while the draft renders
    it with a unit, so a literal substring match is a false positive (OI-015).
    This is a factual number check, not a free-text pattern match. Non-numeric
    strings still require a literal substring match.
    """
    sv = str(value).strip()
    if not sv:
        return True
    # Clean numeric value → normalised int/float comparison (unchanged behaviour).
    try:
        fv = float(sv)
        iv = int(fv)
        return sv in corpus or str(iv) in corpus or f"{fv}" in corpus
    except (ValueError, OverflowError):
        pass
    # Value with embedded digits (unit/symbol) → every numeric token must appear
    # as a number in the corpus.
    tokens = re.findall(r"\d+(?:\.\d+)?", sv)
    if tokens:
        corpus_numbers = set(re.findall(r"\d+(?:\.\d+)?", corpus))
        return all(_number_backed(tok, corpus_numbers) for tok in tokens)
    # No digits at all → literal substring (paraphrase-tolerant callers skip these).
    return sv in corpus


def _number_backed(token: str, corpus_numbers: set[str]) -> bool:
    """True if a numeric token is present in the corpus (int/float normalised)."""
    if token in corpus_numbers:
        return True
    try:
        f = float(token)
    except ValueError:
        return False
    return str(int(f)) in corpus_numbers or f"{f}" in corpus_numbers


# ---------------------------------------------------------------------------
# FabricationGuard
# ---------------------------------------------------------------------------

class FabricationGuard:
    """Blocks responses and tool arguments that assert facts without ledger provenance."""

    # --- C2 + C3 + C4: argument provenance ---

    def check_tool_arguments(
        self,
        tool_name: str,
        arguments: dict,
        ledger: Ledger,
        model: str | None = None,
    ) -> GuardResult:
        """
        C2: every numeric value in a state-changing call must appear in the ledger.
        C3: LLM attribution + deterministic entity-binding gate.
        C4: unanimity gate (second call) on UNCERTAIN from C3.
        """
        if not arguments or not _is_state_changing(tool_name):
            return GuardResult(
                verdict="PASS", layer="FabricationGuard.C2",
                reason="no arguments or read-only tool",
            )

        corpus = _ledger_text_corpus(ledger)
        numeric_args = {
            k: v for k, v in arguments.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        if not numeric_args:
            return GuardResult(
                verdict="PASS", layer="FabricationGuard.C2",
                reason="no numeric arguments",
            )

        # C2: provenance check — value must appear somewhere in the ledger
        for name, value in numeric_args.items():
            if not _value_in_ledger(value, corpus):
                _log.warning(
                    "FabricationGuard.C2: BLOCK — numeric value not in ledger",
                    tool=tool_name, arg=name, value=value,
                )
                return GuardResult(
                    verdict="BLOCK",
                    layer="FabricationGuard.C2",
                    reason=f"{tool_name}.{name}={value} has no ledger provenance",
                )

        # C3: LLM-assisted binding check
        result1 = self._attribution_check(tool_name, numeric_args, ledger, corpus, model)
        if result1.verdict == "PASS":
            return result1

        # C4: unanimity gate — second identical call
        result2 = self._attribution_check(tool_name, numeric_args, ledger, corpus, model)
        if result1.verdict == result2.verdict:
            # unanimous agreement → follow
            return GuardResult(
                verdict=result1.verdict,
                layer="FabricationGuard.C4",
                reason=result1.reason,
            )
        # dissent → conservative UNCERTAIN
        return GuardResult(
            verdict="UNCERTAIN",
            layer="FabricationGuard.C4",
            reason=result1.reason + " [C4: dissent]",
        )

    def _attribution_check(
        self,
        tool_name: str,
        numeric_args: dict,
        ledger: Ledger,
        corpus: str,
        model: str | None,
    ) -> GuardResult:
        """One LLM attribution call + deterministic gate (C3 inner)."""
        transcript = prompts.common.render_transcript(ledger, include_tools=True)
        synonyms = _tool_entity_synonyms(tool_name)

        system = (
            "You are an argument attribution assistant for an in-car voice assistant. "
            "For each numeric argument of the tool call shown, find the literal verbatim "
            "quote from the conversation that provides that value, and identify what entity "
            "or concept the quote is talking about. Be precise."
        )
        content = (
            f"# Conversation\n{transcript}\n\n"
            f"# Tool call to attribute\nTool: {tool_name}\nArguments: {numeric_args}\n\n"
            "For each numeric argument, provide:\n"
            "- argument_name: the parameter name\n"
            "- argument_value: the value as a string\n"
            "- source_quote: EXACT literal quote from the conversation providing this value "
            "(empty string if no clear source exists)\n"
            "- target_entity: what entity or concept the quote is talking about "
            "(e.g. 'sunroof', 'sunshade', 'navigation', 'unknown')"
        )
        messages = [{"role": "user", "content": content}]

        try:
            resp: AttributionResponse = llm.call_structured(
                messages, AttributionResponse, model=model, system=system, temperature=0.0
            )
        except Exception as exc:
            _log.warning("FabricationGuard.C3: LLM call failed — defaulting to PASS", error=str(exc))
            return GuardResult(verdict="PASS", layer="FabricationGuard.C3", reason=f"LLM error: {exc}")

        for attr in resp.attributions:
            quote = attr.source_quote.strip()

            # Gate 1: empty quote means the value is inferred from context (not falsifiable) → skip
            if not quote:
                continue

            # Gate 1b: non-empty quote must exist literally in the ledger corpus
            if quote not in corpus:
                _log.info(
                    "FabricationGuard.C3: source quote not found in ledger",
                    tool=tool_name, arg=attr.argument_name, quote=quote[:80],
                )
                return GuardResult(
                    verdict="UNCERTAIN",
                    layer="FabricationGuard.C3",
                    reason=(
                        f"{tool_name}.{attr.argument_name}={attr.argument_value}: "
                        f"source quote not found in ledger"
                    ),
                )

            # Gate 2: only flag entity confusion when the quote explicitly names a DIFFERENT
            # entity — not merely when it omits the target entity (which might be a fragment
            # quote like "halfway" that doesn't name any entity).
            if synonyms and not any(s in quote.lower() for s in synonyms):
                competing = [
                    e for e in _KNOWN_ENTITIES
                    if e not in synonyms and e in quote.lower()
                ]
                if competing:
                    _log.info(
                        "FabricationGuard.C3: value bound to wrong entity",
                        tool=tool_name, synonyms=synonyms,
                        source_entity=attr.target_entity,
                        arg=attr.argument_name,
                    )
                    return GuardResult(
                        verdict="UNCERTAIN",
                        layer="FabricationGuard.C3",
                        reason=(
                            f"{tool_name}.{attr.argument_name}={attr.argument_value}: "
                            f"source mentions '{', '.join(competing)}', not "
                            f"({', '.join(synonyms)})"
                        ),
                    )

        return GuardResult(
            verdict="PASS",
            layer="FabricationGuard.C3",
            reason="all argument bindings verified",
        )

    # --- C5: draft sanitization ---

    def sanitize(self, draft: str, ledger: Ledger, model: str | None = None) -> str:
        """
        C5: remove or replace unsupported factual claims in draft.
        Mandatory route-choice mention added if missing (OI-012 partial).
        LLM extracts candidates; code decides via Ledger-corpus check (Lesson 1a).
        """
        corpus = _ledger_text_corpus(ledger)

        system = (
            "You are a claim extractor for an in-car voice assistant. "
            "Given a draft response, list every specific factual value it asserts: "
            "numbers, times, distances, temperatures, ETA, specific states, availability."
            "Only list concrete claims that could be wrong if invented."
        )
        content = (
            f"# Draft response\n{draft}\n\n"
            "List each specific factual value asserted in the draft. "
            "For each:\n"
            "- value: the specific value (e.g. '42 minutes', '22°C', 'unavailable')\n"
            "- sentence: the full sentence that contains this value\n\n"
            "Leave out generic confirmations ('Done', 'All set'). "
            "Only list values that could be wrong if invented."
        )
        messages = [{"role": "user", "content": content}]

        try:
            resp: ClaimExtractionResponse = llm.call_structured(
                messages, ClaimExtractionResponse, model=model, system=system, temperature=0.0
            )
        except Exception as exc:
            _log.warning("FabricationGuard.C5: claim extraction failed — draft unchanged", error=str(exc))
            return draft

        safe = draft
        for claim in resp.claims:
            # Only check claims with numeric content — string descriptions may be valid
            # paraphrases of tool results (e.g. "cloudy with rain" for "cloudy_and_rain").
            if not re.search(r"\d", claim.value):
                continue
            if not _value_in_ledger(claim.value, corpus):
                _log.info(
                    "FabricationGuard.C5: unsupported claim replaced",
                    claim_value=claim.value,
                    sentence=claim.sentence[:80],
                )
                safe = safe.replace(
                    claim.sentence,
                    "I'm sorry, I don't have confirmed information about that.",
                ).strip()

        return self._add_route_mention_if_missing(safe, ledger)

    def _add_route_mention_if_missing(self, draft: str, ledger: Ledger) -> str:
        route_calls = [
            e for e in ledger.entries
            if e.kind == "tool_call" and e.tool_name in _ROUTE_TOOLS
        ]
        if not route_calls:
            return draft
        draft_lower = draft.lower()
        if not any(w in draft_lower for w in _ROUTE_CHOICE_WORDS):
            return draft + " I've selected the fastest route."
        return draft
