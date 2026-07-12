"""Stufe-4 unit tests: declarative policy pre-flight (ADR-0004).

One violation case and one non-violation case per generic rule type,
plus Null-FP discipline (unknown state never blocks). No LLM, no API key:
PolicyChecker.pre_flight is exercised directly against a scripted ledger.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from track_1_agent_under_test.glassbox import Ledger
from track_1_agent_under_test.glassbox.capability import CapabilityIndex
from track_1_agent_under_test.glassbox.policies import PolicyChecker
from track_1_agent_under_test.glassbox.state_machine import PlannedCall


_TOOL_NAMES = [
    "get_weather",
    "get_entries_from_calendar",
    "get_climate_settings",
    "get_exterior_lights_status",
    "get_vehicle_window_positions",
    "get_sunroof_and_sunshade_position",
    "get_current_navigation_state",
    "open_close_sunshade",
    "open_close_sunroof",
    "open_close_window",
    "set_window_defrost",
    "set_fan_speed",
    "set_fan_airflow_direction",
    "set_air_conditioning",
    "set_fog_lights",
    "set_head_lights_low_beams",
    "set_head_lights_high_beams",
    "set_new_navigation",
    "navigation_add_one_waypoint",
    "navigation_delete_waypoint",
    "navigation_delete_destination",
]

TOOLS = [
    {"function": {"name": n, "description": "",
                  "parameters": {"properties": {}, "required": []}}}
    for n in _TOOL_NAMES
]

INDEX = CapabilityIndex(TOOLS)


def index_without(*names: str) -> CapabilityIndex:
    return CapabilityIndex(
        [t for t in TOOLS if t["function"]["name"] not in names]
    )


SYS_PLAIN = "You are a car assistant. Follow the policies."

SYS_WITH_CTX = (
    "You are a car assistant. Follow the policies.\n"
    'CURRENT_LOCATION = {"id": "loc_home", "name": "Home", '
    '"position": {"lat": 48.1, "lng": 11.5}}\n'
    'DATETIME = {"year": 2026, "month": 7, "day": 4, "hour": 10, "minute": 0}\n'
)


def make_ledger(system: str = SYS_PLAIN) -> Ledger:
    ledger = Ledger()
    ledger.add_system(system)
    ledger.add_user_turn("Please do the thing.")
    return ledger


_cid = 0


def observe(ledger: Ledger, tool: str, fields: dict) -> None:
    """Record a SUCCESS tool call+result pair (observation or state change)."""
    global _cid
    _cid += 1
    cid = f"obs_{_cid}"
    ledger.add_tool_call(tool, {}, cid)
    ledger.add_tool_result(
        tool, json.dumps({"status": "SUCCESS", "result": fields}), cid
    )


def call(tool: str, args: dict, cid: str = "p0") -> PlannedCall:
    return PlannedCall(tool=tool, arguments=args, call_id=cid)


def pre_flight(calls, ledger, index=INDEX):
    return PolicyChecker().pre_flight(calls, ledger, index)


def policy_ids(violations) -> list[str]:
    return [v.policy_id for v in violations]


# ---------------------------------------------------------------------------
# companion_available — AUT-POL:005 availability aspect
# ---------------------------------------------------------------------------

class CompanionAvailableTest(unittest.TestCase):
    def test_missing_companion_tool_yields_missing_capability(self):
        ledger = make_ledger()
        pf = pre_flight([call("open_close_sunroof", {"percentage": 50})],
                        ledger, index_without("open_close_sunshade"))
        self.assertIn("AUT-POL:005", policy_ids(pf.missing_capability))
        self.assertEqual(pf.blocked, [])

    def test_companion_in_catalog_passes(self):
        ledger = make_ledger()
        pf = pre_flight([call("open_close_sunroof", {"percentage": 50})], ledger)
        self.assertEqual(pf.missing_capability, [])

    def test_satisfied_by_known_state_passes_without_companion_tool(self):
        ledger = make_ledger()
        observe(ledger, "get_sunroof_and_sunshade_position",
                {"sunshade_position": 100, "sunroof_position": 0})
        observe(ledger, "get_weather", {"weather": "sunny"})
        pf = pre_flight([call("open_close_sunroof", {"percentage": 50})],
                        ledger, index_without("open_close_sunshade"))
        self.assertEqual(pf.missing_capability, [])
        self.assertEqual(pf.blocked, [])
        self.assertEqual([c.tool for c in pf.kept], ["open_close_sunroof"])


# ---------------------------------------------------------------------------
# value_bound — AUT-POL:023 / 024 current-day bounds
# ---------------------------------------------------------------------------

class ValueBoundTest(unittest.TestCase):
    def test_calendar_wrong_day_blocked(self):
        ledger = make_ledger(SYS_WITH_CTX)
        pf = pre_flight(
            [call("get_entries_from_calendar", {"month": 7, "day": 5})], ledger)
        self.assertIn("AUT-POL:023", policy_ids(pf.blocked))

    def test_calendar_current_day_passes(self):
        ledger = make_ledger(SYS_WITH_CTX)
        pf = pre_flight(
            [call("get_entries_from_calendar", {"month": 7, "day": 4})], ledger)
        self.assertEqual(pf.blocked, [])

    def test_weather_wrong_month_blocked(self):
        ledger = make_ledger(SYS_WITH_CTX)
        pf = pre_flight(
            [call("get_weather", {"location_or_poi_id": "loc_home", "month": 6,
                                  "day": 4, "time_hour_24hformat": 10})], ledger)
        self.assertIn("AUT-POL:024", policy_ids(pf.blocked))

    def test_unparseable_task_context_never_blocks(self):
        ledger = make_ledger(SYS_PLAIN)  # no DATETIME literal in system prompt
        pf = pre_flight(
            [call("get_entries_from_calendar", {"month": 1, "day": 1})], ledger)
        self.assertEqual(pf.blocked, [])


# ---------------------------------------------------------------------------
# state_precondition — AUT-POL:014 / 017
# ---------------------------------------------------------------------------

class StatePreconditionTest(unittest.TestCase):
    def test_high_beams_blocked_when_fog_lights_known_on(self):
        ledger = make_ledger()
        observe(ledger, "get_exterior_lights_status", {"fog_lights": True})
        pf = pre_flight([call("set_head_lights_high_beams", {"on": True})], ledger)
        self.assertIn("AUT-POL:014", policy_ids(pf.blocked))
        self.assertEqual(pf.kept, [])
        self.assertEqual(pf.missing_capability, [])

    def test_high_beams_pass_when_fog_lights_known_off(self):
        ledger = make_ledger()
        observe(ledger, "get_exterior_lights_status", {"fog_lights": False})
        c = call("set_head_lights_high_beams", {"on": True})
        pf = pre_flight([c], ledger)
        self.assertEqual(pf.blocked, [])
        self.assertEqual(pf.kept, [c])
        self.assertEqual(pf.injected, [])

    def test_unknown_state_injects_observation_and_defers(self):
        ledger = make_ledger()
        c = call("set_head_lights_high_beams", {"on": True})
        pf = pre_flight([c], ledger)
        self.assertEqual(pf.blocked, [])  # Null-FP: unknown never blocks
        self.assertEqual([i.tool for i in pf.injected],
                         ["get_exterior_lights_status"])
        self.assertEqual(pf.deferred, [c])

    def test_loop_protection_keeps_call_with_note(self):
        ledger = make_ledger()
        # observation already attempted this turn, result unparseable → still unknown
        ledger.add_tool_call("get_exterior_lights_status", {}, "c_loop")
        ledger.add_tool_result("get_exterior_lights_status", "garbled", "c_loop")
        c = call("set_head_lights_high_beams", {"on": True})
        pf = pre_flight([c], ledger)
        self.assertEqual(pf.blocked, [])
        self.assertEqual(pf.injected, [])
        self.assertEqual(pf.kept, [c])
        self.assertTrue(any("AUT-POL:014" in n for n in pf.notes))

    def test_nav_edit_blocked_when_navigation_known_inactive(self):
        ledger = make_ledger()
        observe(ledger, "get_current_navigation_state",
                {"navigation_active": False, "waypoints_id": []})
        pf = pre_flight(
            [call("navigation_add_one_waypoint", {"waypoint_id": "w9"})], ledger)
        self.assertIn("AUT-POL:017", policy_ids(pf.blocked))

    def test_delete_destination_blocked_without_intermediate_stop(self):
        ledger = make_ledger()
        observe(ledger, "get_current_navigation_state",
                {"navigation_active": True, "waypoints_id": ["start", "dest"]})
        pf = pre_flight([call("navigation_delete_destination", {})], ledger)
        self.assertIn("AUT-POL:019", policy_ids(pf.blocked))

    def test_delete_waypoint_passes_with_intermediate_stop(self):
        # B6 regression (base_56): the precondition must be evaluated on the
        # state BEFORE the call — its own decrement must not veto it.
        ledger = make_ledger()
        observe(ledger, "get_current_navigation_state",
                {"navigation_active": True,
                 "waypoints_id": ["start", "stop1", "dest"]})
        c = call("navigation_delete_waypoint", {"waypoint_id": "stop1"})
        pf = pre_flight([c], ledger)
        self.assertEqual(pf.blocked, [])
        self.assertEqual(pf.kept, [c])

    def test_second_delete_in_same_batch_still_blocked(self):
        # After the first delete the projected count drops to 2 — a second
        # delete in the same batch would empty the intermediate stops.
        ledger = make_ledger()
        observe(ledger, "get_current_navigation_state",
                {"navigation_active": True,
                 "waypoints_id": ["start", "stop1", "dest"]})
        first = call("navigation_delete_waypoint", {"waypoint_id": "stop1"}, "c1")
        second = call("navigation_delete_destination", {}, "c2")
        pf = pre_flight([first, second], ledger)
        self.assertIn("AUT-POL:019", policy_ids(pf.blocked))
        self.assertIn(first, pf.kept)
        self.assertNotIn(second, pf.kept)


# ---------------------------------------------------------------------------
# prior_observation — AUT-POL:009 weather check before sunroof / fog lights
# ---------------------------------------------------------------------------

class PriorObservationTest(unittest.TestCase):
    def test_weather_injected_before_sunroof_open(self):
        ledger = make_ledger(SYS_WITH_CTX)
        c = call("open_close_sunroof", {"percentage": 50})
        pf = pre_flight([c], ledger)
        self.assertEqual(len(pf.injected), 1)
        self.assertEqual(pf.injected[0].tool, "get_weather")
        self.assertEqual(pf.injected[0].arguments,
                         {"location_or_poi_id": "loc_home", "month": 7,
                          "day": 4, "time_hour_24hformat": 10})
        self.assertEqual(pf.deferred, [c])
        self.assertEqual(pf.blocked, [])

    def test_existing_weather_result_passes(self):
        ledger = make_ledger(SYS_WITH_CTX)
        observe(ledger, "get_weather", {"weather": "sunny"})
        observe(ledger, "get_sunroof_and_sunshade_position",
                {"sunshade_position": 100, "sunroof_position": 0})
        c = call("open_close_sunroof", {"percentage": 50})
        pf = pre_flight([c], ledger)
        self.assertEqual(pf.kept, [c])
        self.assertEqual(pf.injected, [])
        self.assertEqual(pf.deferred, [])

    def test_unconstructible_args_keeps_call_with_note(self):
        ledger = make_ledger(SYS_PLAIN)  # no CURRENT_LOCATION/DATETIME
        c = call("open_close_sunroof", {"percentage": 50})
        pf = pre_flight([c], ledger)
        self.assertIn(c, pf.kept)  # Null-FP: never dropped, only noted
        self.assertEqual(pf.blocked, [])
        self.assertTrue(any("AUT-POL:009" in n for n in pf.notes))


# ---------------------------------------------------------------------------
# state_companion — AUT-POL:005 value aspect / 010 / 011 / 013
# ---------------------------------------------------------------------------

class StateCompanionTest(unittest.TestCase):
    def test_fog_lights_inject_low_beams_on_and_high_beams_off(self):
        ledger = make_ledger()
        observe(ledger, "get_weather", {"weather": "cloudy_and_thunderstorm"})
        observe(ledger, "get_exterior_lights_status",
                {"fog_lights": False, "head_lights_low_beams": False,
                 "head_lights_high_beams": True})
        c = call("set_fog_lights", {"on": True})
        pf = pre_flight([c], ledger)
        self.assertEqual(
            [(i.tool, i.arguments) for i in pf.injected],
            [("set_head_lights_low_beams", {"on": True}),
             ("set_head_lights_high_beams", {"on": False})])
        self.assertEqual(pf.kept, [c])

    def test_fog_lights_no_injection_when_state_conform(self):
        ledger = make_ledger()
        observe(ledger, "get_weather", {"weather": "cloudy_and_thunderstorm"})
        observe(ledger, "get_exterior_lights_status",
                {"fog_lights": False, "head_lights_low_beams": True,
                 "head_lights_high_beams": False})
        c = call("set_fog_lights", {"on": True})
        pf = pre_flight([c], ledger)
        self.assertEqual(pf.injected, [])
        self.assertEqual(pf.kept, [c])

    def test_unknown_light_state_injects_observation_and_defers(self):
        ledger = make_ledger()
        observe(ledger, "get_weather", {"weather": "cloudy_and_thunderstorm"})
        c = call("set_fog_lights", {"on": True})
        pf = pre_flight([c], ledger)
        self.assertEqual([i.tool for i in pf.injected],
                         ["get_exterior_lights_status"])
        self.assertEqual(pf.deferred, [c])
        self.assertEqual(pf.blocked, [])

    def test_sunshade_injected_when_position_unknown(self):
        ledger = make_ledger()
        observe(ledger, "get_weather", {"weather": "sunny"})
        c = call("open_close_sunroof", {"percentage": 50})
        pf = pre_flight([c], ledger)
        self.assertEqual([(i.tool, i.arguments) for i in pf.injected],
                         [("open_close_sunshade", {"percentage": 100})])
        self.assertEqual(pf.kept, [c])  # inject_when_unknown: no defer needed

    def test_no_sunshade_injection_when_parallel_in_batch(self):
        ledger = make_ledger()
        observe(ledger, "get_weather", {"weather": "sunny"})
        batch = [call("open_close_sunshade", {"percentage": 100}, "p0"),
                 call("open_close_sunroof", {"percentage": 50}, "p1")]
        pf = pre_flight(batch, ledger)
        self.assertEqual(pf.injected, [])
        self.assertEqual(pf.kept, batch)

    def test_ac_on_injects_window_close_for_open_window(self):
        ledger = make_ledger()
        observe(ledger, "get_vehicle_window_positions",
                {"window_driver_position": 60, "window_passenger_position": 0,
                 "window_driver_rear_position": 0,
                 "window_passenger_rear_position": 0})
        observe(ledger, "get_climate_settings", {"fan_speed": 3})
        c = call("set_air_conditioning", {"on": True})
        pf = pre_flight([c], ledger)
        self.assertEqual([(i.tool, i.arguments) for i in pf.injected],
                         [("open_close_window",
                           {"window": "DRIVER", "percentage": 0})])
        self.assertEqual(pf.kept, [c])

    def test_missing_companion_tool_yields_missing_capability(self):
        ledger = make_ledger()
        observe(ledger, "get_vehicle_window_positions",
                {"window_driver_position": 0, "window_passenger_position": 0,
                 "window_driver_rear_position": 0,
                 "window_passenger_rear_position": 0})
        observe(ledger, "get_climate_settings", {"fan_speed": 0})
        pf = pre_flight([call("set_air_conditioning", {"on": True})],
                        ledger, index_without("set_fan_speed"))
        self.assertIn("AUT-POL:011", policy_ids(pf.missing_capability))

    # --- AUT-POL:010 airflow merge: WINDSHIELD wird ERGÄNZT, nicht hart gesetzt ---

    def _defrost_pf(self, climate: dict):
        ledger = make_ledger()
        observe(ledger, "get_climate_settings", climate)
        c = call("set_window_defrost", {"defrost_window": "FRONT", "on": True})
        return pre_flight([c], ledger), c

    def test_defrost_merges_windshield_into_feet(self):
        pf, c = self._defrost_pf({"fan_speed": 2, "fan_airflow_direction": "FEET",
                                  "air_conditioning": True})
        self.assertEqual([(i.tool, i.arguments) for i in pf.injected],
                         [("set_fan_airflow_direction",
                           {"direction": "WINDSHIELD_FEET"})])
        self.assertEqual(pf.kept, [c])

    def test_defrost_merges_windshield_into_head_feet(self):
        pf, _ = self._defrost_pf({"fan_speed": 2,
                                  "fan_airflow_direction": "HEAD_FEET",
                                  "air_conditioning": True})
        self.assertEqual([(i.tool, i.arguments) for i in pf.injected],
                         [("set_fan_airflow_direction",
                           {"direction": "WINDSHIELD_HEAD_FEET"})])

    def test_defrost_no_airflow_injection_when_windshield_included(self):
        pf, c = self._defrost_pf({"fan_speed": 2,
                                  "fan_airflow_direction": "WINDSHIELD_HEAD",
                                  "air_conditioning": True})
        self.assertEqual(pf.injected, [])
        self.assertEqual(pf.kept, [c])

    def test_airflow_merge_unknown_value_falls_back_to_windshield(self):
        from track_1_agent_under_test.glassbox.policies import (
            _airflow_merge_windshield,
        )
        self.assertEqual(_airflow_merge_windshield("SOMETHING_ELSE"),
                         {"direction": "WINDSHIELD"})
        self.assertEqual(_airflow_merge_windshield(None),
                         {"direction": "WINDSHIELD"})

    # --- Planner-supplied companions: naive WINDSHIELD wird zum Merge umgeschrieben ---

    def _defrost_with_planned_airflow(self, climate: dict, direction: str):
        ledger = make_ledger()
        observe(ledger, "get_climate_settings", climate)
        defrost = call("set_window_defrost",
                       {"defrost_window": "FRONT", "on": True}, "p0")
        airflow = call("set_fan_airflow_direction",
                       {"direction": direction}, "p1")
        return pre_flight([defrost, airflow], ledger), defrost, airflow

    def test_defrost_planner_supplied_windshield_rewritten_to_merge(self):
        """dis_22: planner plans the companion itself with the naive hard value;
        its effect pre-empts needs() → rewrite to the state-preserving merge."""
        pf, defrost, airflow = self._defrost_with_planned_airflow(
            {"fan_speed": 2, "fan_airflow_direction": "FEET",
             "air_conditioning": True}, "WINDSHIELD")
        self.assertEqual(airflow.arguments, {"direction": "WINDSHIELD_FEET"})
        self.assertEqual(pf.injected, [])
        self.assertEqual(pf.kept, [defrost, airflow])

    def test_defrost_planner_explicit_direction_not_rewritten_null_fp(self):
        """An argument that differs from the value-blind fallback is treated as
        deliberate and never touched; the policy injects its merge separately."""
        pf, _, airflow = self._defrost_with_planned_airflow(
            {"fan_speed": 2, "fan_airflow_direction": "HEAD",
             "air_conditioning": True}, "FEET")
        self.assertEqual(airflow.arguments, {"direction": "FEET"})
        self.assertEqual([(i.tool, i.arguments) for i in pf.injected],
                         [("set_fan_airflow_direction",
                           {"direction": "WINDSHIELD_FEET"})])

    def test_defrost_planner_windshield_untouched_when_already_included_null_fp(self):
        """Current direction already includes WINDSHIELD → needs() is False →
        the planner's call counts as a deliberate change and stays as-is."""
        pf, _, airflow = self._defrost_with_planned_airflow(
            {"fan_speed": 2, "fan_airflow_direction": "WINDSHIELD_HEAD",
             "air_conditioning": True}, "WINDSHIELD")
        self.assertEqual(airflow.arguments, {"direction": "WINDSHIELD"})
        self.assertEqual(pf.injected, [])

    def test_airflow_alone_without_defrost_trigger_untouched_null_fp(self):
        """Explicit user wish (dis_6/base_8): hard WINDSHIELD without a defrost
        trigger in the batch — the rule never evaluates, nothing is rewritten."""
        ledger = make_ledger()
        observe(ledger, "get_climate_settings",
                {"fan_speed": 2, "fan_airflow_direction": "FEET",
                 "air_conditioning": True})
        airflow = call("set_fan_airflow_direction", {"direction": "WINDSHIELD"})
        pf = pre_flight([airflow], ledger)
        self.assertEqual(airflow.arguments, {"direction": "WINDSHIELD"})
        self.assertEqual(pf.injected, [])
        self.assertEqual(pf.kept, [airflow])


# ---------------------------------------------------------------------------
# no_parallel — AUT-POL:018 waypoint edits strictly sequential
# ---------------------------------------------------------------------------

class NoParallelTest(unittest.TestCase):
    def _nav_ledger(self) -> Ledger:
        ledger = make_ledger()
        observe(ledger, "get_current_navigation_state",
                {"navigation_active": True,
                 "waypoints_id": ["start", "stop1", "dest"]})
        return ledger

    def test_second_nav_edit_deferred(self):
        first = call("navigation_add_one_waypoint", {"waypoint_id": "w9"}, "p0")
        second = call("navigation_delete_waypoint", {"waypoint_id": "stop1"}, "p1")
        pf = pre_flight([first, second], self._nav_ledger())
        self.assertEqual(pf.kept, [first])
        self.assertEqual(pf.deferred, [second])
        self.assertEqual(pf.blocked, [])
        self.assertTrue(any("AUT-POL:018" in n for n in pf.notes))

    def test_single_nav_edit_passes(self):
        c = call("navigation_add_one_waypoint", {"waypoint_id": "w9"})
        pf = pre_flight([c], self._nav_ledger())
        self.assertEqual(pf.kept, [c])
        self.assertEqual(pf.deferred, [])


# ---------------------------------------------------------------------------
# obligation_note — LLM-POL:007 semantic rest surfaced as a marked note
# ---------------------------------------------------------------------------

class ObligationNoteTest(unittest.TestCase):
    def test_window_over_25_with_ac_on_produces_note(self):
        ledger = make_ledger()
        observe(ledger, "get_climate_settings", {"air_conditioning": True})
        c = call("open_close_window", {"window": "DRIVER", "percentage": 50})
        pf = pre_flight([c], ledger)
        self.assertTrue(any("LLM-POL:007" in n for n in pf.notes))
        self.assertEqual(pf.kept, [c])  # note only — never blocks

    def test_window_over_25_with_ac_unknown_no_note(self):
        ledger = make_ledger()
        c = call("open_close_window", {"window": "DRIVER", "percentage": 50})
        pf = pre_flight([c], ledger)
        self.assertEqual(pf.notes, [])
        self.assertEqual(pf.kept, [c])


# ---------------------------------------------------------------------------
# Null-FP discipline — harmless batch passes completely untouched
# ---------------------------------------------------------------------------

class NullFalsePositiveTest(unittest.TestCase):
    def test_harmless_batch_untouched(self):
        ledger = make_ledger(SYS_WITH_CTX)
        batch = [
            call("get_weather", {"location_or_poi_id": "loc_home", "month": 7,
                                 "day": 4, "time_hour_24hformat": 10}, "p0"),
            call("open_close_window", {"window": "DRIVER", "percentage": 10}, "p1"),
        ]
        pf = pre_flight(batch, ledger)
        self.assertEqual(pf.kept, batch)
        self.assertEqual(pf.injected, [])
        self.assertEqual(pf.deferred, [])
        self.assertEqual(pf.blocked, [])
        self.assertEqual(pf.missing_capability, [])
        self.assertEqual(pf.notes, [])


# ---------------------------------------------------------------------------
# requires_confirmation — LLM-POL:008 adverse-weather confirmation gate (OI-007)
# ---------------------------------------------------------------------------

def observe_weather(ledger: Ledger, condition: str) -> None:
    """Record a SUCCESS get_weather result in the real current_slot.condition shape."""
    global _cid
    _cid += 1
    cid = f"wx_{_cid}"
    ledger.add_tool_call("get_weather", {}, cid)
    ledger.add_tool_result(
        "get_weather",
        json.dumps({"status": "SUCCESS",
                    "result": {"current_slot": {"condition": condition},
                               "next_slot": None}}),
        cid,
    )


class WeatherConfirmationTest(unittest.TestCase):
    # --- violation → BLOCK + Rückfrage ---
    def test_adverse_weather_sunroof_requests_confirmation(self):
        ledger = make_ledger()
        observe_weather(ledger, "rainy")
        c = call("open_close_sunroof", {"percentage": 50})
        pf = pre_flight([c], ledger)
        self.assertEqual([r.policy_id for r in pf.confirmations], ["LLM-POL:008"])
        self.assertNotIn(c, pf.kept)
        self.assertEqual(pf.blocked, [])
        self.assertEqual(pf.missing_capability, [])
        self.assertIn("sunroof", pf.confirmations[0].question.lower())
        self.assertIn("rainy", pf.confirmations[0].question.lower())

    def test_fog_thunderstorm_no_confirmation(self):
        # wiki.md:90 — fog confirmation NOT needed for thunderstorm/hail
        ledger = make_ledger()
        observe_weather(ledger, "cloudy_and_thunderstorm")
        c = call("set_fog_lights", {"on": True})
        pf = pre_flight([c], ledger)
        self.assertEqual(pf.confirmations, [])
        self.assertEqual(pf.blocked, [])

    def test_fog_hail_no_confirmation(self):
        # wiki.md:90 — fog confirmation NOT needed for thunderstorm/hail
        ledger = make_ledger()
        observe_weather(ledger, "cloudy_and_hail")
        c = call("set_fog_lights", {"on": True})
        pf = pre_flight([c], ledger)
        self.assertEqual(pf.confirmations, [])
        self.assertEqual(pf.blocked, [])

    # --- benign weather → no block (Null-FP!) ---
    def test_benign_weather_sunroof_no_confirmation(self):
        ledger = make_ledger()
        observe_weather(ledger, "sunny")      # in the allowed set for sunroof
        c = call("open_close_sunroof", {"percentage": 50})
        pf = pre_flight([c], ledger)
        self.assertEqual(pf.confirmations, [])
        self.assertIn(c, pf.kept)

    def test_fog_rainy_requests_confirmation(self):
        # wiki.md:90 — fog confirmation needed for weather NOT in {thunderstorm, hail}
        ledger = make_ledger()
        observe_weather(ledger, "rainy")
        c = call("set_fog_lights", {"on": True})
        pf = pre_flight([c], ledger)
        self.assertEqual([r.policy_id for r in pf.confirmations], ["LLM-POL:008"])
        self.assertNotIn(c, pf.kept)

    def test_unknown_weather_never_asks(self):
        # Null-FP: no weather observation in the ledger at all
        ledger = make_ledger()
        c = call("open_close_sunroof", {"percentage": 50})
        pf = pre_flight([c], ledger)
        self.assertEqual(pf.confirmations, [])

    def test_closing_sunroof_never_asks(self):
        # when=_is_opening_strict → closing (percentage 0) does not trigger
        ledger = make_ledger()
        observe_weather(ledger, "rainy")
        c = call("open_close_sunroof", {"percentage": 0})
        pf = pre_flight([c], ledger)
        self.assertEqual(pf.confirmations, [])

    # --- confirmation already in the ledger → PASS ---
    def test_confirmation_in_ledger_passes(self):
        ledger = make_ledger()
        observe_weather(ledger, "rainy")       # turn 1
        ledger.add_user_turn("Yes, go ahead.")  # turn 2 → explicit confirmation
        c = call("open_close_sunroof", {"percentage": 50})
        pf = pre_flight([c], ledger)
        self.assertEqual(pf.confirmations, [])
        self.assertIn(c, pf.kept)

    def test_affirmative_before_weather_does_not_count(self):
        # a "yes" in the same/earlier turn as the weather read is not a confirmation
        ledger = make_ledger()               # user turn 1 "Please do the thing."
        observe_weather(ledger, "rainy")     # weather read also in turn 1
        c = call("open_close_sunroof", {"percentage": 50})
        pf = pre_flight([c], ledger)
        self.assertEqual([r.policy_id for r in pf.confirmations], ["LLM-POL:008"])


# ---------------------------------------------------------------------------
# OI-008 — LLM-POL:012 zone temperature >3°C
# ---------------------------------------------------------------------------

# Add temperature tools to the index for these tests
_TEMP_TOOLS = TOOLS + [
    {"function": {"name": "get_temperature_inside_car", "description": "",
                  "parameters": {"properties": {}, "required": []}}},
    {"function": {"name": "set_climate_temperature", "description": "",
                  "parameters": {"properties": {
                      "temperature": {"type": "number"},
                      "seat_zone": {"type": "string",
                                    "enum": ["ALL_ZONES", "DRIVER", "PASSENGER"]},
                  }, "required": ["temperature", "seat_zone"]}}},
]
_TEMP_INDEX = CapabilityIndex(_TEMP_TOOLS)


class ZoneTemperatureTest(unittest.TestCase):
    def test_observation_injected_before_single_zone_temp_change(self):
        ledger = make_ledger()
        c = call("set_climate_temperature",
                 {"temperature": 25, "seat_zone": "DRIVER"})
        pf = pre_flight([c], ledger, _TEMP_INDEX)
        self.assertEqual([i.tool for i in pf.injected],
                         ["get_temperature_inside_car"])
        self.assertEqual(pf.deferred, [c])

    def test_all_zones_no_observation(self):
        ledger = make_ledger()
        c = call("set_climate_temperature",
                 {"temperature": 25, "seat_zone": "ALL_ZONES"})
        pf = pre_flight([c], ledger, _TEMP_INDEX)
        self.assertEqual(pf.injected, [])
        self.assertEqual(pf.kept, [c])

    def test_note_when_diff_exceeds_3(self):
        ledger = make_ledger()
        observe(ledger, "get_temperature_inside_car",
                {"climate_temperature_driver": 22,
                 "climate_temperature_passenger": 22})
        c = call("set_climate_temperature",
                 {"temperature": 26, "seat_zone": "DRIVER"})
        pf = pre_flight([c], ledger, _TEMP_INDEX)
        self.assertTrue(any("LLM-POL:012" in n for n in pf.notes))
        self.assertIn(c, pf.kept)  # note only, no block

    def test_no_note_when_diff_within_3(self):
        ledger = make_ledger()
        observe(ledger, "get_temperature_inside_car",
                {"climate_temperature_driver": 22,
                 "climate_temperature_passenger": 22})
        c = call("set_climate_temperature",
                 {"temperature": 24, "seat_zone": "DRIVER"})
        pf = pre_flight([c], ledger, _TEMP_INDEX)
        self.assertFalse(any("LLM-POL:012" in n for n in pf.notes))

    def test_unknown_other_zone_no_note(self):
        """Null-FP: if the other zone temperature is unknown, no note."""
        ledger = make_ledger()
        c = call("set_climate_temperature",
                 {"temperature": 30, "seat_zone": "DRIVER"})
        # temperature already observed (to skip prior-observation deferral)
        observe(ledger, "get_temperature_inside_car",
                {"climate_temperature_driver": 22})
        pf = pre_flight([c], ledger, _TEMP_INDEX)
        self.assertFalse(any("LLM-POL:012" in n for n in pf.notes))


# ---------------------------------------------------------------------------
# OI-007r — LLM-POL:004 REQUIRES_CONFIRMATION tools
# ---------------------------------------------------------------------------

# Tools with REQUIRES_CONFIRMATION descriptions
_RC_TOOL_DEFS = [
    {"function": {"name": "open_close_trunk_door",
                  "description": "REQUIRES_CONFIRMATION, Vehicle Control: Open or close the trunk door.",
                  "parameters": {"properties": {
                      "open": {"type": "boolean"},
                  }, "required": ["open"]}}},
    {"function": {"name": "send_email",
                  "description": "REQUIRES_CONFIRMATION, Email Tool: sends an email.",
                  "parameters": {"properties": {
                      "to": {"type": "string"},
                      "message": {"type": "string"},
                  }, "required": ["to", "message"]}}},
]
_RC_INDEX = CapabilityIndex(TOOLS + _RC_TOOL_DEFS)


class RequiresConfirmationToolTest(unittest.TestCase):
    def test_trunk_door_blocks_without_confirmation(self):
        ledger = make_ledger()
        c = call("open_close_trunk_door", {"open": True})
        pf = pre_flight([c], ledger, _RC_INDEX)
        self.assertEqual([r.policy_id for r in pf.confirmations], ["LLM-POL:004"])
        self.assertNotIn(c, pf.kept)
        self.assertIn("trunk", pf.confirmations[0].question.lower())

    def test_trunk_door_passes_after_user_confirms(self):
        ledger = make_ledger()  # turn 0
        ledger.add_agent_response("I'd like to operate the trunk door.")
        ledger.add_user_turn("Yes, go ahead.")  # turn > 0, affirmative
        c = call("open_close_trunk_door", {"open": True})
        pf = pre_flight([c], ledger, _RC_INDEX)
        self.assertEqual(pf.confirmations, [])
        self.assertIn(c, pf.kept)

    def test_email_blocks_without_confirmation(self):
        ledger = make_ledger()
        c = call("send_email", {"to": "a@b.com", "message": "hello"})
        pf = pre_flight([c], ledger, _RC_INDEX)
        self.assertEqual([r.policy_id for r in pf.confirmations], ["LLM-POL:004"])

    def test_high_beams_blocks_without_confirmation(self):
        # Use index with REQUIRES_CONFIRMATION description for high beams
        rc_hb_tools = [
            {"function": {"name": n,
                          "description": ("REQUIRES_CONFIRMATION, high beams"
                                          if n == "set_head_lights_high_beams" else ""),
                          "parameters": {"properties": {}, "required": []}}}
            for n in _TOOL_NAMES
        ]
        rc_hb_index = CapabilityIndex(rc_hb_tools)
        ledger = make_ledger()
        observe(ledger, "get_exterior_lights_status", {"fog_lights": False})
        c = call("set_head_lights_high_beams", {"on": True})
        pf = pre_flight([c], ledger, rc_hb_index)
        self.assertEqual([r.policy_id for r in pf.confirmations], ["LLM-POL:004"])
        self.assertNotIn(c, pf.kept)

    def test_negation_voids_confirmation(self):
        ledger = make_ledger()
        ledger.add_agent_response("Shall I open the trunk?")
        ledger.add_user_turn("No, don't do that.")
        c = call("open_close_trunk_door", {"open": True})
        pf = pre_flight([c], ledger, _RC_INDEX)
        self.assertEqual([r.policy_id for r in pf.confirmations], ["LLM-POL:004"])


# ---------------------------------------------------------------------------
# OI-012 — LLM-POL:022 fastest route for multi-stop navigation
# ---------------------------------------------------------------------------

_NAV_FULL_TOOLS = TOOLS + [
    {"function": {"name": "navigation_replace_final_destination",
                  "description": "",
                  "parameters": {"properties": {
                      "route_id_leading_to_new_destination": {"type": "string"},
                  }, "required": ["route_id_leading_to_new_destination"]}}},
]
_NAV_FULL_INDEX = CapabilityIndex(_NAV_FULL_TOOLS)


class FastestRouteNoteTest(unittest.TestCase):
    def test_multi_stop_produces_note(self):
        ledger = make_ledger()
        # AUT-POL:018 needs navigation_active=False for set_new_navigation to pass
        observe(ledger, "get_current_navigation_state",
                {"navigation_active": False, "waypoints_id": []})
        c = call("set_new_navigation",
                 {"route_ids": ["route_a", "route_b"]})
        pf = pre_flight([c], ledger)
        self.assertTrue(any("LLM-POL:022" in n for n in pf.notes))
        self.assertIn(c, pf.kept)

    def test_single_segment_no_note(self):
        ledger = make_ledger()
        observe(ledger, "get_current_navigation_state",
                {"navigation_active": False, "waypoints_id": []})
        c = call("set_new_navigation", {"route_ids": ["route_a"]})
        pf = pre_flight([c], ledger)
        self.assertFalse(any("LLM-POL:022" in n for n in pf.notes))

    def test_replace_destination_multi_stop_produces_note(self):
        ledger = make_ledger()
        observe(ledger, "get_current_navigation_state",
                {"navigation_active": True,
                 "waypoints_id": ["start", "wp1", "dest"]})
        c = call("navigation_replace_final_destination",
                 {"route_id_leading_to_new_destination": "route_x"})
        pf = pre_flight([c], ledger, _NAV_FULL_INDEX)
        self.assertTrue(any("LLM-POL:022" in n for n in pf.notes))

    def test_replace_destination_simple_route_no_note(self):
        ledger = make_ledger()
        observe(ledger, "get_current_navigation_state",
                {"navigation_active": True,
                 "waypoints_id": ["start", "dest"]})
        c = call("navigation_replace_final_destination",
                 {"route_id_leading_to_new_destination": "route_x"})
        pf = pre_flight([c], ledger, _NAV_FULL_INDEX)
        self.assertFalse(any("LLM-POL:022" in n for n in pf.notes))


# ---------------------------------------------------------------------------
# Fix 4 — Confirmation question templates include tool parameters
# ---------------------------------------------------------------------------


class ConfirmationTemplateParamsTest(unittest.TestCase):
    """Confirmation questions must mention the concrete tool parameters."""

    def test_sunroof_confirmation_includes_percentage(self):
        ledger = make_ledger()
        observe_weather(ledger, "rainy")
        c = call("open_close_sunroof", {"percentage": 50})
        pf = pre_flight([c], ledger)
        self.assertTrue(pf.confirmations)
        q = pf.confirmations[0].question
        self.assertIn("50", q)
        self.assertIn("sunroof", q.lower())

    def test_fog_lights_confirmation_mentions_action(self):
        ledger = make_ledger()
        observe_weather(ledger, "rainy")
        c = call("set_fog_lights", {"on": True})
        pf = pre_flight([c], ledger)
        self.assertTrue(pf.confirmations)
        q = pf.confirmations[0].question
        self.assertIn("fog lights", q.lower())

    def test_trunk_door_confirmation_includes_position(self):
        ledger = make_ledger()
        c = call("open_close_trunk_door", {"position": "OPEN"})
        pf = pre_flight([c], ledger, _RC_INDEX)
        self.assertTrue(pf.confirmations)
        q = pf.confirmations[0].question
        self.assertIn("OPEN", q)
        self.assertIn("trunk", q.lower())

    def test_high_beams_confirmation_includes_state(self):
        rc_hb_tools = [
            {"function": {"name": n,
                          "description": ("REQUIRES_CONFIRMATION, high beams"
                                          if n == "set_head_lights_high_beams" else ""),
                          "parameters": {"properties": {}, "required": []}}}
            for n in _TOOL_NAMES
        ]
        rc_hb_index = CapabilityIndex(rc_hb_tools)
        ledger = make_ledger()
        observe(ledger, "get_exterior_lights_status", {"fog_lights": False})
        c = call("set_head_lights_high_beams", {"on": True})
        pf = pre_flight([c], ledger, rc_hb_index)
        self.assertTrue(pf.confirmations)
        q = pf.confirmations[0].question
        self.assertIn("on", q.lower())

    def test_email_confirmation_includes_recipient(self):
        ledger = make_ledger()
        c = call("send_email", {"recipient": "alice@example.com", "subject": "Hello"})
        pf = pre_flight([c], ledger, _RC_INDEX)
        self.assertTrue(pf.confirmations)
        q = pf.confirmations[0].question
        self.assertIn("alice@example.com", q)
        self.assertIn("Hello", q)


if __name__ == "__main__":
    unittest.main()
