"""
Policy-Compiler — Stufe 4.

Die 19 Policies aus wiki.md als deterministische Praedikate.
Laeuft als Pre-Flight-Check vor jedem zustandsaendernden Tool-Call.

AUT-POL = automatisch geprueft (durch Evaluator UND hier praeventiiv).
LLM-POL = durch Gemini-Judge bewertet; wir muessen sie trotzdem beachten
          und den Agenten entsprechend anweisen.

Compliance-Grenze: Pruefung nur gegen Wahrheit, Ledger-Herkunft und diese
19 veroeffentlichten Policies — niemals gegen nachgebildete Evaluator-Subscores.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .ledger import Ledger


PolicyKind = Literal["AUT", "LLM", "BOTH"]


@dataclass
class PolicyViolation:
    policy_id: str
    kind: PolicyKind
    reason: str


@dataclass
class Policy:
    policy_id: str
    kind: PolicyKind
    description: str


# All 19 policies from wiki.md, canonically ordered
ALL_POLICIES: list[Policy] = [
    Policy("LLM-POL:002", "LLM",
           "Metric system: distance in km/m, temperature in °C, datetime in 24h format."),
    Policy("LLM-POL:004", "LLM",
           "REQUIRES_CONFIRMATION tools: list details and get explicit 'yes' before executing."),
    Policy("AUT-POL:005", "AUT",
           "Sunroof can only be opened if sunshade is already fully open or opening in parallel."),
    Policy("LLM-POL:007", "LLM",
           "Window open >25% with AC ON: prompt for confirmation and warn about energy inefficiency."),
    Policy("LLM-POL:008", "BOTH",
           "Weather-gated actions (sunroof, fog lights): require explicit 'yes' in certain conditions."),
    Policy("AUT-POL:009", "AUT",
           "Weather check must precede sunroof/fog-lights activation in adverse weather."),
    Policy("AUT-POL:010", "AUT",
           "Window defrost activation: must also set AC on, open air circulation, set fan speed."),
    Policy("AUT-POL:011", "AUT",
           "AC set to ON: must automatically close all windows and close air circulation."),
    Policy("LLM-POL:012", "LLM",
           "Single-zone temperature set with >3°C diff to other zones: inform user."),
    Policy("AUT-POL:013", "AUT",
           "Fog lights activation: must automatically set low beam headlights."),
    Policy("AUT-POL:014", "AUT",
           "High beam headlights cannot be activated if fog lights are already on."),
    Policy("AUT-POL:016", "AUT",
           "Route start must always be the current car location."),
    Policy("AUT-POL:017", "AUT",
           "Waypoint edit tools only usable when navigation system is already active with a route."),
    Policy("AUT-POL:018", "AUT",
           "Active navigation: use edit tools (not set_new_navigation) for changes; sequential only."),
    Policy("AUT-POL:019", "AUT",
           "Route must always have at least start + destination; cannot delete destination alone."),
    Policy("LLM-POL:021", "LLM",
           "Route presented in detail with toll roads: must inform user about toll."),
    Policy("LLM-POL:022", "LLM",
           "Multi-stop route, no selection specified: take fastest per segment, inform user, ask about alternatives."),
    Policy("AUT-POL:023", "AUT",
           "Calendar entries only requestable for the current day."),
    Policy("AUT-POL:024", "AUT",
           "Weather only requestable for the current day at a specified time."),
]

# Tools that require explicit user confirmation before execution (AUT-POL:004 / wiki)
REQUIRES_CONFIRMATION_TOOLS = frozenset({
    "send_email",
    "open_close_trunk_door",
    "set_head_lights_high_beams",
})

# Tools that change vehicle state (not pure reads)
STATE_CHANGING_TOOLS = frozenset({
    "open_close_sunroof", "open_close_sunshade", "open_close_window",
    "open_close_trunk_door", "set_climate_temperature", "set_air_conditioning",
    "set_air_circulation", "set_fan_speed", "set_fan_airflow_direction",
    "set_window_defrost", "set_seat_heating", "set_steering_wheel_heating",
    "set_ambient_lights", "set_reading_light", "set_fog_lights",
    "set_head_lights_high_beams", "set_head_lights_low_beams",
    "set_new_navigation", "delete_current_navigation",
    "navigation_add_one_waypoint", "navigation_delete_one_waypoint",
    "navigation_replace_one_waypoint", "navigation_replace_final_destination",
    "navigation_delete_final_destination",
    "send_email", "call_phone_by_number",
})


class PolicyChecker:
    """
    Pre-flight check before state-changing tool calls.

    Stufe 4 implementation point: each AUT-POL predicate must be implemented
    as a method below and registered in `_AUT_CHECKS`.
    """

    def pre_flight(self, ctx: "TurnContext") -> list[PolicyViolation]:  # type: ignore[name-defined]
        """Check all AUT-POL policies before executing the plan."""
        raise NotImplementedError("PolicyChecker.pre_flight — implement in Stufe 4")

    def post_execution(
        self, tool_name: str, args: dict, result: Any, ctx: "TurnContext"  # type: ignore[name-defined]
    ) -> list[PolicyViolation]:
        """Check policies that can only be verified after a tool result is available."""
        raise NotImplementedError("PolicyChecker.post_execution — implement in Stufe 4")

    # --- AUT-POL predicates (one method per policy) ---

    def _check_005_sunroof_sunshade(self, tool_name: str, args: dict, ledger: Ledger) -> PolicyViolation | None:
        """Sunroof open requires sunshade fully open or opening in parallel."""
        raise NotImplementedError

    def _check_009_weather_gate(self, tool_name: str, args: dict, ledger: Ledger) -> PolicyViolation | None:
        """Weather-gated tools need get_weather result in ledger."""
        raise NotImplementedError

    def _check_010_window_defrost(self, tool_name: str, args: dict, plan: list[dict]) -> PolicyViolation | None:
        """Window defrost must be accompanied by AC on, air circulation open, fan speed set."""
        raise NotImplementedError

    def _check_011_ac_on(self, tool_name: str, args: dict, plan: list[dict]) -> PolicyViolation | None:
        """AC ON must close all windows and close air circulation."""
        raise NotImplementedError

    def _check_013_fog_lights(self, tool_name: str, args: dict, plan: list[dict]) -> PolicyViolation | None:
        """Fog lights activation must include low beam headlights."""
        raise NotImplementedError

    def _check_014_high_beams_fog(self, tool_name: str, args: dict, ledger: Ledger) -> PolicyViolation | None:
        """High beams blocked if fog lights are on."""
        raise NotImplementedError

    def _check_016_route_start(self, tool_name: str, args: dict) -> PolicyViolation | None:
        """Route start must be current car location."""
        raise NotImplementedError

    def _check_017_waypoint_edit_requires_active(self, tool_name: str, args: dict, ledger: Ledger) -> PolicyViolation | None:
        """Edit tools only when navigation active."""
        raise NotImplementedError

    def _check_018_active_nav_edit_not_new(self, tool_name: str, args: dict, ledger: Ledger) -> PolicyViolation | None:
        """Use edit tools (not set_new_navigation) when navigation is active."""
        raise NotImplementedError

    def _check_019_route_has_destination(self, tool_name: str, args: dict, ledger: Ledger) -> PolicyViolation | None:
        """Cannot delete the only destination; route needs start + destination."""
        raise NotImplementedError

    def _check_023_calendar_current_day(self, tool_name: str, args: dict) -> PolicyViolation | None:
        """Calendar only for current day."""
        raise NotImplementedError

    def _check_024_weather_current_day(self, tool_name: str, args: dict) -> PolicyViolation | None:
        """Weather only for current day."""
        raise NotImplementedError


# Avoid circular import at module load time
from typing import Any
try:
    from .state_machine import TurnContext
except ImportError:
    pass
