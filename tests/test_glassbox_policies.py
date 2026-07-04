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


if __name__ == "__main__":
    unittest.main()
