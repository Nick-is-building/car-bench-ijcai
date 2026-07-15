"""F4 hallucination hardening tests.

Fix 1 (C6): Inability claims contradicting successful tool results.
Fix 2: Unknown-semantik (prompt-only, tested via integration patterns).
Fix 3: Relative distance claims flagged when route data is unknown.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from track_1_agent_under_test.glassbox import Ledger
from track_1_agent_under_test.glassbox.guard import (
    FabricationGuard,
    ClaimExtractionResponse,
    FactualClaim,
    _inability_contradicts_ledger,
    _successful_tool_names,
    _is_relative_distance_claim,
    _route_data_is_unknown,
    strip_action_promises,
    check_navigation_arguments,
)


def _ledger_with_successful_call(tool_name: str, args: dict, result: dict) -> Ledger:
    led = Ledger()
    led.add_user_turn("Do something")
    led.add_tool_call(tool_name, args, "c1")
    led.add_tool_result(
        tool_name, json.dumps({"status": "SUCCESS", "result": result}), "c1"
    )
    return led


def _ledger_with_route_unknown() -> Ledger:
    led = Ledger()
    led.add_user_turn("Can I drive to Prague?")
    led.add_tool_call("get_charging_specs_and_status", {}, "c1")
    led.add_tool_result(
        "get_charging_specs_and_status",
        json.dumps({"status": "SUCCESS", "result": {"state_of_charge": 50.0, "remaining_range_km": 178}}),
        "c1",
    )
    led.add_tool_call("get_routes_from_start_to_destination", {"start_id": "a", "destination_id": "b"}, "c2")
    led.add_tool_result(
        "get_routes_from_start_to_destination",
        json.dumps({"status": "SUCCESS", "result": {"routes": "unknown"}}),
        "c2",
    )
    return led


def _ledger_with_route_data() -> Ledger:
    led = Ledger()
    led.add_user_turn("Route to Prague?")
    led.add_tool_call("get_routes_from_start_to_destination", {"start_id": "a", "destination_id": "b"}, "c1")
    led.add_tool_result(
        "get_routes_from_start_to_destination",
        json.dumps({"status": "SUCCESS", "result": {"routes": [{"distance_km": 350}]}}),
        "c1",
    )
    return led


class InabilityContradictionTest(unittest.TestCase):
    """Fix 1 / C6: inability claims vs successful tool calls."""

    def test_inability_caught_when_tool_succeeded(self):
        successful = {"open_close_window", "set_window_defrost"}
        sentence = "I'm sorry, but I'm not able to control the windows in this vehicle."
        tool = _inability_contradicts_ledger(sentence, successful)
        self.assertIsNotNone(tool)
        self.assertIn("window", tool)

    def test_inability_allowed_when_tool_not_called(self):
        successful = {"set_fan_airflow_direction"}
        sentence = "I'm sorry, but I'm not able to control the fan speed."
        tool = _inability_contradicts_ledger(sentence, successful)
        self.assertIsNone(tool, "fan speed was not called — inability is honest")

    def test_inability_caught_for_defrost(self):
        successful = {"set_window_defrost", "set_fan_speed"}
        sentence = "I'm not able to control the front window defrost in this vehicle."
        tool = _inability_contradicts_ledger(sentence, successful)
        self.assertIsNotNone(tool)

    def test_no_match_on_unrelated_tool(self):
        successful = {"get_weather"}
        sentence = "I cannot control the windows."
        tool = _inability_contradicts_ledger(sentence, successful)
        self.assertIsNone(tool)

    def test_non_inability_sentence_ignored(self):
        successful = {"open_close_window"}
        sentence = "Done! I've closed all windows."
        tool = _inability_contradicts_ledger(sentence, successful)
        self.assertIsNone(tool)


class SuccessfulToolNamesTest(unittest.TestCase):

    def test_extracts_success_tools(self):
        led = _ledger_with_successful_call(
            "open_close_window", {"window": "ALL", "percentage": 0}, {"window": "ALL", "percentage": 0}
        )
        names = _successful_tool_names(led)
        self.assertIn("open_close_window", names)

    def test_ignores_failed_tools(self):
        led = Ledger()
        led.add_user_turn("test")
        led.add_tool_call("set_fan_speed", {"level": 3}, "c1")
        led.add_tool_result("set_fan_speed", json.dumps({"status": "FAILURE", "errors": {"msg": "not found"}}), "c1")
        names = _successful_tool_names(led)
        self.assertNotIn("set_fan_speed", names)


class SanitizeInabilityIntegrationTest(unittest.TestCase):
    """Full sanitize() path with C6 inability fix."""

    def test_draft_inability_replaced_when_tool_succeeded(self):
        led = _ledger_with_successful_call(
            "open_close_window", {"window": "ALL", "percentage": 0}, {"window": "ALL", "percentage": 0}
        )
        draft = "I'm sorry, but I'm not able to control the windows or the front window defrost in this vehicle. You'll need to adjust those manually."
        guard = FabricationGuard()

        fake_claims = ClaimExtractionResponse(claims=[])
        with patch("track_1_agent_under_test.glassbox.guard.llm.call_structured", return_value=fake_claims):
            result = guard.sanitize(draft, led)

        self.assertNotIn("not able to control the windows", result)

    def test_draft_honest_inability_preserved(self):
        led = _ledger_with_successful_call(
            "set_fan_airflow_direction", {"direction": "FEET"}, {"direction": "FEET"}
        )
        draft = "Done! Fan airflow is set to feet. I'm sorry, but I'm not able to adjust the fan speed as that capability is not available."
        guard = FabricationGuard()

        fake_claims = ClaimExtractionResponse(claims=[])
        with patch("track_1_agent_under_test.glassbox.guard.llm.call_structured", return_value=fake_claims):
            result = guard.sanitize(draft, led)

        self.assertIn("fan speed", result.lower())


class RelativeDistanceClaimTest(unittest.TestCase):
    """Fix 3: relative distance claims flagged when route data is unknown."""

    def test_relative_distance_detected(self):
        self.assertTrue(_is_relative_distance_claim("way further"))
        self.assertTrue(_is_relative_distance_claim("you'd need to stop and charge"))
        self.assertTrue(_is_relative_distance_claim("definitely need to charge"))

    def test_non_distance_not_detected(self):
        self.assertFalse(_is_relative_distance_claim("50%"))
        self.assertFalse(_is_relative_distance_claim("level 2"))

    def test_route_data_unknown_detected(self):
        led = _ledger_with_route_unknown()
        self.assertTrue(_route_data_is_unknown(led))

    def test_route_data_present_not_flagged(self):
        led = _ledger_with_route_data()
        self.assertFalse(_route_data_is_unknown(led))

    def test_sanitize_removes_fabricated_distance_claim(self):
        led = _ledger_with_route_unknown()
        draft = "Your battery is at 50% with 178 km range. Prague is way further than that, so you'd definitely need to stop and charge along the way."
        guard = FabricationGuard()

        fake_claims = ClaimExtractionResponse(claims=[
            FactualClaim(value="50%", sentence="Your battery is at 50% with 178 km range."),
            FactualClaim(value="178 km", sentence="Your battery is at 50% with 178 km range."),
            FactualClaim(value="way further", sentence="Prague is way further than that, so you'd definitely need to stop and charge along the way."),
        ])
        with patch("track_1_agent_under_test.glassbox.guard.llm.call_structured", return_value=fake_claims):
            result = guard.sanitize(draft, led)

        self.assertNotIn("way further", result)
        self.assertIn("50%", result)

    def test_sanitize_keeps_distance_claim_when_route_known(self):
        led = _ledger_with_route_data()
        draft = "The route is 350 km long."
        guard = FabricationGuard()

        fake_claims = ClaimExtractionResponse(claims=[
            FactualClaim(value="350 km", sentence="The route is 350 km long."),
        ])
        with patch("track_1_agent_under_test.glassbox.guard.llm.call_structured", return_value=fake_claims):
            result = guard.sanitize(draft, led)

        self.assertIn("350 km", result)


class Fix3NavArgumentValidatorTest(unittest.TestCase):
    """Fix 3 — navigation route_id arguments must point at the correct
    waypoint anchor (hall_48/64, hall_80). Deterministic re-anchor from the
    ledger's route metadata."""

    def _ledger_with_routes(self, routes: list[dict]) -> Ledger:
        led = Ledger()
        led.add_user_turn("plan route")
        led.add_tool_call("get_routes_from_start_to_destination", {}, "c1")
        led.add_tool_result(
            "get_routes_from_start_to_destination",
            json.dumps({"status": "SUCCESS", "result": {"routes": routes}}),
            "c1",
        )
        return led

    def test_replace_waypoint_start_id_mismatch_repaired(self):
        led = self._ledger_with_routes([
            {"route_id": "r_bad", "start_id": "loc_A", "destination_id": "loc_C",
             "duration_hours": 3, "distance_km": 200},
            {"route_id": "r_good", "start_id": "loc_B", "destination_id": "loc_C",
             "duration_hours": 2, "distance_km": 150},
            {"route_id": "r_alt", "start_id": "loc_B", "destination_id": "loc_C",
             "duration_hours": 4, "distance_km": 300},
        ])
        args = {
            "new_waypoint_id": "loc_B",
            "route_id_leading_away_from_new_waypoint": "r_bad",
        }
        res = check_navigation_arguments(
            "navigation_replace_one_waypoint", args, led)
        self.assertFalse(res.ok)
        self.assertEqual(res.repaired["route_id_leading_away_from_new_waypoint"], "r_good")
        self.assertIn("route_id_leading_away_from_new_waypoint", res.replaced)

    def test_replace_final_destination_end_id_mismatch_repaired(self):
        led = self._ledger_with_routes([
            {"route_id": "r_bad", "start_id": "loc_A", "destination_id": "loc_X",
             "duration_hours": 3, "distance_km": 200},
            {"route_id": "r_good", "start_id": "loc_A", "destination_id": "loc_MUC",
             "duration_hours": 5, "distance_km": 400},
        ])
        args = {
            "new_destination_id": "loc_MUC",
            "route_id_leading_to_new_destination": "r_bad",
        }
        res = check_navigation_arguments(
            "navigation_replace_final_destination", args, led)
        self.assertFalse(res.ok)
        self.assertEqual(res.repaired["route_id_leading_to_new_destination"], "r_good")

    def test_correct_route_id_passes_untouched(self):
        led = self._ledger_with_routes([
            {"route_id": "r_ok", "start_id": "loc_B", "destination_id": "loc_C",
             "duration_hours": 2, "distance_km": 150},
        ])
        args = {"new_waypoint_id": "loc_B",
                "route_id_leading_away_from_new_waypoint": "r_ok"}
        res = check_navigation_arguments(
            "navigation_replace_one_waypoint", args, led)
        self.assertTrue(res.ok)
        self.assertEqual(res.replaced, {})

    def test_unknown_route_id_emits_hint_no_repair(self):
        """Route id not in the ledger — surface a hint, do not guess a replacement."""
        led = self._ledger_with_routes([])
        args = {"new_waypoint_id": "loc_B",
                "route_id_leading_away_from_new_waypoint": "r_never_seen"}
        res = check_navigation_arguments(
            "navigation_replace_one_waypoint", args, led)
        self.assertFalse(res.ok)
        self.assertEqual(res.replaced, {})
        self.assertTrue(any("r_never_seen" in h for h in res.hints))

    def test_non_navigation_tool_ignored(self):
        led = Ledger()
        args = {"level": 3}
        res = check_navigation_arguments("set_fan_speed", args, led)
        self.assertTrue(res.ok)
        self.assertEqual(res.replaced, {})


class Fix5AnnounceStallTest(unittest.TestCase):
    """Fix 5 — an action promise sentence at turn end is a HALLUCINATION_ERROR
    trigger (hall_44 T1, hall_76, hall_82, hall_86, dis_54). Strip it."""

    def test_strip_let_me_promise(self):
        draft = "The window is at 50%. Let me switch it off for you."
        result = strip_action_promises(draft)
        self.assertEqual(result, "The window is at 50%.")

    def test_strip_i_will_promise(self):
        draft = "Route calculated. I'll now check the weather for you."
        result = strip_action_promises(draft)
        self.assertEqual(result, "Route calculated.")

    def test_strip_now_let_me(self):
        draft = "That's set. Now let me adjust the fan speed."
        result = strip_action_promises(draft)
        self.assertEqual(result, "That's set.")

    def test_no_promise_left_untouched(self):
        draft = "Done. The seat heater is now at level 3."
        self.assertEqual(strip_action_promises(draft), draft)

    def test_only_promise_returns_original_fallback(self):
        """If stripping would empty the reply, keep the draft — something is
        better than nothing (Auditor/C5 catch the actual fabrication)."""
        draft = "Let me do that now."
        self.assertEqual(strip_action_promises(draft), draft)

    def test_past_action_not_flagged(self):
        """Reporting what has been done is not a promise — never strip."""
        draft = "I've opened the sunroof to 50%."
        self.assertEqual(strip_action_promises(draft), draft)


class MultiStopEnforcerTest(unittest.TestCase):
    """G — Multi-Stop-Message-Enforcer (LLM-POL:022, Phase 2 §4.2)."""

    def _ledger_with_multi_stop(self, route_ids, toll_routes=None):
        """Build a ledger with get_routes + set_new_navigation SUCCESS."""
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("Navigate from A via B to C.")
        routes_result = {"status": "SUCCESS", "result": []}
        for rid in route_ids:
            route = {"id": rid, "includes_toll": rid in (toll_routes or set())}
            routes_result["result"].append(route)
        ledger.add_tool_call("get_routes_from_start_to_destination", {}, "c0")
        ledger.add_tool_result("get_routes_from_start_to_destination",
                               json.dumps(routes_result), "c0")
        ledger.add_tool_call("set_new_navigation",
                             {"route_ids": route_ids}, "c1")
        ledger.add_tool_result("set_new_navigation",
                               json.dumps({"status": "SUCCESS",
                                           "result": {"navigation_set": True}}),
                               "c1")
        return ledger

    def test_appends_all_missing_blocks(self):
        from track_1_agent_under_test.glassbox.guard import enforce_multi_stop_message
        ledger = self._ledger_with_multi_stop(["r1", "r2"])
        draft = "Navigation is set!"
        result = enforce_multi_stop_message(draft, ledger)
        self.assertIn("fastest", result.lower())
        self.assertIn("alternative", result.lower())
        self.assertNotIn("toll", result.lower())

    def test_no_change_when_all_present(self):
        from track_1_agent_under_test.glassbox.guard import enforce_multi_stop_message
        ledger = self._ledger_with_multi_stop(["r1", "r2"])
        draft = ("I've selected the fastest route per segment. "
                 "Would you like alternative routes? No toll roads on this trip.")
        result = enforce_multi_stop_message(draft, ledger)
        self.assertEqual(result, draft)

    def test_no_trigger_for_single_stop(self):
        from track_1_agent_under_test.glassbox.guard import enforce_multi_stop_message
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("Navigate to B.")
        ledger.add_tool_call("set_new_navigation", {"route_ids": ["r1"]}, "c1")
        ledger.add_tool_result("set_new_navigation",
                               json.dumps({"status": "SUCCESS",
                                           "result": {"navigation_set": True}}),
                               "c1")
        draft = "Navigation is set!"
        result = enforce_multi_stop_message(draft, ledger)
        self.assertEqual(result, draft)

    def test_toll_appended_only_when_flag_set(self):
        from track_1_agent_under_test.glassbox.guard import enforce_multi_stop_message
        ledger = self._ledger_with_multi_stop(["r1", "r2"], toll_routes={"r2"})
        draft = "I've selected the fastest route. Would you like alternative routes?"
        result = enforce_multi_stop_message(draft, ledger)
        self.assertIn("toll", result.lower())

    def test_no_trigger_without_success(self):
        from track_1_agent_under_test.glassbox.guard import enforce_multi_stop_message
        ledger = Ledger()
        ledger.add_system("You are a car assistant.")
        ledger.add_user_turn("Navigate from A via B to C.")
        ledger.add_tool_call("set_new_navigation", {"route_ids": ["r1", "r2"]}, "c1")
        ledger.add_tool_result("set_new_navigation",
                               json.dumps({"status": "FAILURE",
                                           "errors": {"msg": "nav active"}}),
                               "c1")
        draft = "Navigation is set!"
        result = enforce_multi_stop_message(draft, ledger)
        self.assertEqual(result, draft)


if __name__ == "__main__":
    unittest.main()
