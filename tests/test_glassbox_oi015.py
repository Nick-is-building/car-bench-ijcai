"""OI-015 regression: numeric provenance check must not false-positive on
values rendered with a unit or symbol.

Root cause: `_value_in_ledger` used a literal substring match. The ledger often
stores a bare number (a tool-result dict field like {"eta_minutes": 42}) while
the reply renders it with a unit or symbol ("42 minutes", "50%"). The literal
match then failed and the honest-admission sink was injected into a valid,
ledger-backed reply — a false positive. The fix matches by numeric TOKENS while
still requiring genuine ledger provenance, so real fabrications stay blocked.

These tests exercise the shared helper directly, the Auditor (Stufe 7), and the
C5 sanitize path (with a faked claim extractor), all without API keys.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from track_1_agent_under_test.glassbox import Ledger
from track_1_agent_under_test.glassbox.auditor import Auditor, _HONEST_ADMISSION
from track_1_agent_under_test.glassbox.guard import (
    FabricationGuard,
    ClaimExtractionResponse,
    FactualClaim,
    _value_in_ledger,
    _ledger_text_corpus,
)
from track_1_agent_under_test.glassbox.prompts.verify import ClaimCheck, Draft


def _ledger_with_tool_result(result) -> Ledger:
    led = Ledger()
    led.add_user_turn("How long until we arrive?")
    led.add_tool_call("get_eta", {}, "c1")
    led.add_tool_result("get_eta", result, "c1")
    return led


class ValueInLedgerHelperTest(unittest.TestCase):
    """The numeric-token match — the actual OI-015 fix, tested in isolation."""

    def test_unit_value_backed_by_bare_number(self):
        # {"eta_minutes": 42} -> corpus contains "42" but not "42 minutes".
        corpus = _ledger_text_corpus(_ledger_with_tool_result({"eta_minutes": 42}))
        self.assertTrue(_value_in_ledger("42 minutes", corpus))

    def test_percent_symbol_backed_by_bare_number(self):
        corpus = _ledger_text_corpus(_ledger_with_tool_result({"level": 50}))
        self.assertTrue(_value_in_ledger("50%", corpus))
        self.assertTrue(_value_in_ledger("50 percent", corpus))

    def test_temperature_symbol_backed_by_bare_number(self):
        corpus = _ledger_text_corpus(_ledger_with_tool_result({"temp": 22}))
        self.assertTrue(_value_in_ledger("22°C", corpus))

    def test_clean_numeric_still_matches(self):
        # Unchanged behaviour: bare number vs bare number (int/float normalised).
        corpus = _ledger_text_corpus(_ledger_with_tool_result({"eta_minutes": 42}))
        self.assertTrue(_value_in_ledger("42", corpus))
        self.assertTrue(_value_in_ledger(42, corpus))
        self.assertTrue(_value_in_ledger(42.0, corpus))

    def test_empty_value_is_backed(self):
        corpus = _ledger_text_corpus(_ledger_with_tool_result({"ok": True}))
        self.assertTrue(_value_in_ledger("", corpus))
        self.assertTrue(_value_in_ledger("   ", corpus))

    def test_absent_number_not_backed(self):
        # Genuine fabrication: 99 is nowhere in the ledger.
        corpus = _ledger_text_corpus(_ledger_with_tool_result({"eta_minutes": 42}))
        self.assertFalse(_value_in_ledger("99 minutes", corpus))

    def test_substring_of_larger_number_not_backed(self):
        # "3 °C" must NOT be considered backed by a corpus that only has "30".
        corpus = _ledger_text_corpus(_ledger_with_tool_result({"temp": 30}))
        self.assertFalse(_value_in_ledger("3 °C", corpus))

    def test_multi_token_all_must_be_backed(self):
        corpus = _ledger_text_corpus(
            _ledger_with_tool_result({"eta_minutes": 42, "distance_km": 15})
        )
        self.assertTrue(_value_in_ledger("42 min, 15 km", corpus))
        self.assertFalse(_value_in_ledger("42 min, 16 km", corpus))

    def test_non_numeric_uses_substring(self):
        corpus = _ledger_text_corpus(_ledger_with_tool_result({"status": "available"}))
        self.assertTrue(_value_in_ledger("available", corpus))
        self.assertFalse(_value_in_ledger("occupied", corpus))


class AuditorNullFpTest(unittest.TestCase):
    """Stufe-7 self-check must PASS a valid unit-rendered claim (the OI-015 FP)."""

    def test_unit_claim_backed_by_dict_field_passes(self):
        led = _ledger_with_tool_result({"eta_minutes": 42})
        draft = Draft(
            claims=[ClaimCheck(
                value="42 minutes",
                sentence="You'll arrive in 42 minutes.",
                source="42",
            )],
            response="You'll arrive in 42 minutes.",
        )
        res = Auditor().pre_response_check(draft, led)
        self.assertTrue(res.passed, res.issues)
        self.assertEqual(res.conservative_action, "")
        self.assertEqual(res.safe_text, "You'll arrive in 42 minutes.")

    def test_percent_phrasings_pass(self):
        # Various phrasings of the same ledger-backed value → PASS in all cases.
        for value, sentence in [
            ("50%", "The window is now 50% open."),
            ("50 percent", "The window is now 50 percent open."),
        ]:
            led = _ledger_with_tool_result({"percentage": 50})
            draft = Draft(
                claims=[ClaimCheck(value=value, sentence=sentence, source="50")],
                response=sentence,
            )
            res = Auditor().pre_response_check(draft, led)
            self.assertTrue(res.passed, f"{value!r}: {res.issues}")
            self.assertEqual(res.safe_text, sentence)

    def test_missing_confirmation_still_blocks(self):
        # Regression guard: an unbacked numeric claim is still replaced.
        led = _ledger_with_tool_result({"eta_minutes": 42})
        draft = Draft(
            claims=[ClaimCheck(
                value="99 minutes",
                sentence="You'll arrive in 99 minutes.",
                source="",
            )],
            response="You'll arrive in 99 minutes.",
        )
        res = Auditor().pre_response_check(draft, led)
        self.assertFalse(res.passed)
        self.assertEqual(res.safe_text, _HONEST_ADMISSION)


class C5SanitizeNullFpTest(unittest.TestCase):
    """C5 draft sanitization must not inject the honest-admission sink into a
    valid unit-rendered claim, but must still replace genuine fabrications."""

    def _run_sanitize(self, draft_text, ledger, extracted_claims):
        resp = ClaimExtractionResponse(claims=extracted_claims)
        with patch(
            "track_1_agent_under_test.glassbox.llm.call_structured",
            return_value=resp,
        ):
            return FabricationGuard().sanitize(draft_text, ledger, model="fake")

    def test_valid_unit_claim_not_replaced(self):
        led = _ledger_with_tool_result({"eta_minutes": 42})
        draft = "You'll arrive in 42 minutes."
        out = self._run_sanitize(
            draft, led,
            [FactualClaim(value="42 minutes", sentence="You'll arrive in 42 minutes.")],
        )
        self.assertEqual(out, "You'll arrive in 42 minutes.")
        self.assertNotIn("I don't have confirmed information", out)

    def test_fabricated_value_still_replaced(self):
        led = _ledger_with_tool_result({"eta_minutes": 42})
        draft = "You'll arrive in 99 minutes."
        out = self._run_sanitize(
            draft, led,
            [FactualClaim(value="99 minutes", sentence="You'll arrive in 99 minutes.")],
        )
        self.assertIn("I don't have confirmed information", out)
        self.assertNotIn("99 minutes", out)


if __name__ == "__main__":
    unittest.main()
