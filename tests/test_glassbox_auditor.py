"""Stufe-7 unit tests: deterministic pre-response self-check (Auditor).

The Auditor makes NO LLM call of its own — it parses the forced self-check
(`Draft.claims`) that the VERIFY draft already produced and checks each numeric
claim against the ledger corpus. These tests build a Draft + Ledger directly, so
they run fully deterministically without API keys. Covers: numeric value present
in ledger -> PASS; numeric value absent -> sentence replaced by honest admission;
non-numeric claim -> ignored (Null-FP); empty claims -> PASS; multiple claims with
exactly one unsupported.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from track_1_agent_under_test.glassbox import Ledger
from track_1_agent_under_test.glassbox.auditor import Auditor, _HONEST_ADMISSION
from track_1_agent_under_test.glassbox.prompts.verify import ClaimCheck, Draft


def _ledger_with_tool_result(result) -> Ledger:
    led = Ledger()
    led.add_user_turn("How long until we arrive?")
    led.add_tool_call("get_eta", {}, "c1")
    led.add_tool_result("get_eta", result, "c1")
    return led


class AuditorPreResponseTest(unittest.TestCase):
    def test_numeric_value_in_ledger_passes(self):
        led = _ledger_with_tool_result("Estimated arrival in 42 minutes.")
        draft = Draft(
            claims=[ClaimCheck(
                value="42 minutes",
                sentence="You'll arrive in 42 minutes.",
                source="42",
            )],
            response="You'll arrive in 42 minutes.",
        )
        res = Auditor().pre_response_check(draft, led)
        self.assertTrue(res.passed)
        self.assertEqual(res.issues, [])
        self.assertEqual(res.conservative_action, "")
        self.assertEqual(res.safe_text, "You'll arrive in 42 minutes.")

    def test_numeric_value_not_in_ledger_replaced(self):
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
        self.assertEqual(res.conservative_action, "admit")
        self.assertEqual(res.safe_text, _HONEST_ADMISSION)
        self.assertTrue(any("99 minutes" in i for i in res.issues))

    def test_declared_source_not_in_ledger_replaced(self):
        led = _ledger_with_tool_result("Estimated arrival in 42 minutes.")
        draft = Draft(
            claims=[ClaimCheck(
                value="42 minutes",
                sentence="You'll arrive in 42 minutes.",
                source="the traffic service reported 42",
            )],
            response="You'll arrive in 42 minutes.",
        )
        res = Auditor().pre_response_check(draft, led)
        self.assertFalse(res.passed)
        self.assertEqual(res.safe_text, _HONEST_ADMISSION)
        self.assertTrue(any("declared source not in ledger" in i for i in res.issues))

    def test_non_numeric_claim_ignored_null_fp(self):
        led = _ledger_with_tool_result({"status": "available"})
        draft = Draft(
            claims=[ClaimCheck(
                value="available",
                sentence="The charging station is available.",
                source="",
            )],
            response="The charging station is available.",
        )
        res = Auditor().pre_response_check(draft, led)
        self.assertTrue(res.passed)
        self.assertEqual(res.safe_text, "The charging station is available.")

    def test_empty_claims_passes(self):
        led = _ledger_with_tool_result({"ok": True})
        draft = Draft(claims=[], response="Done.")
        res = Auditor().pre_response_check(draft, led)
        self.assertTrue(res.passed)
        self.assertEqual(res.safe_text, "Done.")

    def test_multiple_claims_one_unsupported(self):
        led = _ledger_with_tool_result("Estimated arrival in 42 minutes, 15 km remaining.")
        draft = Draft(
            claims=[
                ClaimCheck(value="42 minutes", sentence="ETA is 42 minutes.", source="42 minutes"),
                ClaimCheck(value="30 km", sentence="It's 30 km away.", source=""),
            ],
            response="ETA is 42 minutes. It's 30 km away.",
        )
        res = Auditor().pre_response_check(draft, led)
        self.assertFalse(res.passed)
        self.assertIn("ETA is 42 minutes.", res.safe_text)
        self.assertNotIn("30 km", res.safe_text)
        self.assertIn(_HONEST_ADMISSION, res.safe_text)
        self.assertEqual(len(res.issues), 1)


if __name__ == "__main__":
    unittest.main()
