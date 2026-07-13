"""Shared deterministic helpers for the per-state prompt modules."""
from __future__ import annotations

import json

from ..ledger import Ledger


def task_system_text(ledger: Ledger) -> str:
    """The evaluator's task system prompt (policies, persona, context)."""
    for e in ledger.entries:
        if e.kind == "system":
            return str(e.content)
    return ""


def render_transcript(
    ledger: Ledger,
    include_tools: bool = True,
    only_current_turn: bool = False,
) -> str:
    """Render ledger entries as a compact, deterministic plain-text transcript.

    Plain text (instead of native message lists) keeps every prompt module
    provider-agnostic and combinable with JSON-schema response formats.
    """
    lines: list[str] = []
    for e in ledger.entries:
        if only_current_turn and e.turn != ledger.current_turn:
            continue
        if e.kind == "user":
            lines.append(f"User: {e.content}")
        elif e.kind == "agent":
            lines.append(f"Assistant: {e.content}")
        elif e.kind == "tool_call" and include_tools:
            args = json.dumps(e.content, sort_keys=True)
            lines.append(f"[tool call] {e.tool_name}({args})")
        elif e.kind == "tool_result" and include_tools:
            lines.append(f"[tool result] {e.tool_name} -> {e.content}")
    return "\n".join(lines)


# SEMANTIC POLICY OBLIGATIONS — deliberately NOT machine-checked (ADR-0004).
# Class-C policies (and the semantic rest of class-B policies) cannot be
# enforced deterministically; they are re-stated verbatim-close here, clearly
# marked, for the PLAN and VERIFY prompts.
SEMANTIC_POLICY_OBLIGATIONS = """\
# SEMANTIC POLICY OBLIGATIONS (not machine-checked — YOU must uphold these)
- LLM-POL:002: metric units only (km/m, degrees Celsius) and 24h time format.
- LLM-POL:004: if a tool description starts with REQUIRES_CONFIRMATION, first \
list the intended action details and obtain an explicit expressive user \
confirmation (yes) BEFORE calling that tool.
- LLM-POL:007: opening a window beyond 25% while AC is ON requires a \
confirmation prompt plus an energy-inefficiency warning.
- LLM-POL:008: if the checked weather is adverse (sunroof: not sunny/cloudy/\
partly_cloudy; fog lights: not cloudy_and_thunderstorm/cloudy_and_hail), the \
action requires an explicit expressive user confirmation (yes) first.
- LLM-POL:012: setting a single seat zone temperature with a resulting \
difference of more than 3 degrees Celsius to other zones — inform the user.
- AUT-POL:016: the start of an overall route set must be the current car \
location.
- AUT-POL:018: while navigation is active, edit waypoints with the dedicated \
delete/replace/add tools (never a full new navigation), strictly one edit at \
a time.
- LLM-POL:021: whenever a route is presented in detail and it includes toll \
roads, the user must be informed about the toll.
- LLM-POL:022: multi-stop route without explicit selection — proactively take \
the fastest route per segment, say so, offer alternatives, and still mention \
toll roads. Single-stop with multiple alternatives — never choose proactively: \
present the fastest and shortest route in detail plus the number of further \
alternatives and ask which the user would like to start.
- ARRIVAL-CONDITION: When the user's request contains a condition tied to the \
destination or a specific arrival time ("if it isn't raining THERE", "if the \
supermarket is still open when I arrive", "check the weather at the meeting \
time"), you MUST evaluate the condition at that arrival time, not at the \
current time. Deterministic order: (1) get the route to compute the driving \
duration, (2) add it to the CURRENT_LOCATION DATETIME to obtain the arrival \
hour, (3) call get_weather / opening-hours lookups with \
time_hour_24hformat = arrival hour. Never omit time_hour_24hformat on \
get_weather — it is a required schema parameter; calls without it return \
FAILURE and kill the reward.
- PLAN-ONLY: If the user asks to PLAN a trip, compare options, or explore a \
route without saying "start", "go", "set", "activate" the navigation, do NOT \
call set_new_navigation or the waypoint mutators yet. Return the plan/answer \
first; only mutate navigation once the user says to start it.
"""


def render_policy_notes(notes: list[str]) -> str:
    """Marked pre-flight notes block for PLAN/VERIFY prompts ('' if none)."""
    if not notes:
        return ""
    lines = "\n".join(f"- {n}" for n in notes)
    return (
        "\n\n# Policy pre-flight notes (deterministic checker, this turn)\n"
        f"{lines}\n"
    )


def render_tool_catalog(tools: list[dict]) -> str:
    """Compact one-line-per-tool catalog: name — first sentence of description."""
    lines = []
    for t in tools:
        fn = t.get("function", t)
        desc = (fn.get("description") or "").strip().split("\n")[0]
        lines.append(f"- {fn.get('name', '?')}: {desc}")
    return "\n".join(lines)


def render_tool_schemas(tools: list[dict]) -> str:
    """Full JSON schemas, deterministically serialized (for the planner)."""
    fns = [t.get("function", t) for t in tools]
    return json.dumps(fns, sort_keys=True, indent=None, separators=(",", ":"))
