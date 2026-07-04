"""
Capability-Matcher — Stufe 3.

Kompiliert beim Start einen Faehigkeits-Index aus den 58 Tool-Schemas.
Prueft bei jeder Anfrage deterministisch, ob die geforderte Faehigkeit gedeckt ist.
Drei Ausgaenge: covered → Planner; uncovered → ehrliches Eingestaendnis; ambiguous → Disambiguierung.

Faengt alle drei Entzugsarten: entferntes Tool, entfernter Parameter, entferntes Result-Feld.
Check laeuft an jedem Planungsschritt, nicht nur beim Intake.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Literal

# Conservative fuzzy threshold: only match when names are very similar.
# At 0.80 "open_close_sunshade" vs "open_close_sunroof" scores ~0.757 → no match
# (genuine missing-capability refusals preserved). Invented aliases like
# "navigation_remove_waypoint" vs "navigation_delete_waypoint" score ~0.81 → match.
FUZZY_THRESHOLD = 0.80


def fuzzy_catalog_hint(tool_name: str, catalog_names: list[str]) -> list[str]:
    """Return up to 2 catalog names close to tool_name (empty = no match)."""
    return difflib.get_close_matches(tool_name, catalog_names, n=2, cutoff=FUZZY_THRESHOLD)

CapabilityResult = Literal["covered", "uncovered", "ambiguous"]


@dataclass
class ToolCapability:
    name: str
    description: str
    parameters: dict          # JSON-Schema der Parameter
    required_params: list[str]


class CapabilityIndex:
    """Index compiled from the tool schemas provided by the evaluator at runtime."""

    def __init__(self, tools: list[dict]) -> None:
        self._caps: dict[str, ToolCapability] = {}
        for t in tools:
            fn = t.get("function", t)
            name = fn["name"]
            params = fn.get("parameters", {})
            self._caps[name] = ToolCapability(
                name=name,
                description=fn.get("description", ""),
                parameters=params.get("properties", {}),
                required_params=params.get("required", []),
            )

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._caps

    def has_parameter(self, tool_name: str, param_name: str) -> bool:
        cap = self._caps.get(tool_name)
        return cap is not None and param_name in cap.parameters

    def get_tool(self, tool_name: str) -> ToolCapability | None:
        return self._caps.get(tool_name)

    @property
    def tool_names(self) -> list[str]:
        return list(self._caps.keys())

    @property
    def tool_descriptions(self) -> dict[str, str]:
        return {name: cap.description for name, cap in self._caps.items()}


class CapabilityMatcher:
    """Checks whether an intent can be fulfilled by available tools."""

    def __init__(self, tools: list[dict]) -> None:
        self.index = CapabilityIndex(tools)

    def check(self, intent: dict) -> CapabilityResult:
        """
        Deterministic capability check (Stufe 3).

        intent keys (from prompts.intake.Intent):
          - required_tools: list[str]            tools the plan will use
          - required_params: list[{tool, params}] params the request constrains
          - required_but_missing_tools: list[str] tools needed but absent from catalog
          - is_ambiguous: bool

        Returns:
          "covered"   — all required tools + params exist in index
          "uncovered" — at least one required tool/param is missing
          "ambiguous" — intent is ambiguous and needs clarification first
        """
        if intent.get("is_ambiguous"):
            return "ambiguous"

        # Tools the LLM detected as needed but absent from catalog.
        # Cross-validate deterministically: only count tools that are genuinely
        # not in the index (LLMs can over-report; the index is ground truth).
        actually_missing = [
            t for t in intent.get("required_but_missing_tools", [])
            if not self.index.has_tool(t)
        ]
        if actually_missing:
            return "uncovered"

        # Tools the plan will actively call — must all be in the index
        for tool_name in intent.get("required_tools", []):
            if not self.index.has_tool(tool_name):
                return "uncovered"

        # Parameters the request constrains — must all be present in schema
        for tp in intent.get("required_params", []):
            if isinstance(tp, dict):
                tool_name = tp.get("tool", "")
                params = tp.get("params", [])
            else:
                tool_name = tp.tool
                params = tp.params
            for param in params:
                # Defensive: LLMs sometimes output "name=value" — strip the value part
                param_name = param.split("=")[0].strip()
                if not self.index.has_parameter(tool_name, param_name):
                    return "uncovered"

        return "covered"

    def check_step(self, tool_name: str, arguments: dict) -> CapabilityResult:
        """Per-step check during EXECUTE — catches mid-conversation capability removal."""
        if not self.index.has_tool(tool_name):
            return "uncovered"
        for param in arguments:
            if not self.index.has_parameter(tool_name, param):
                return "uncovered"
        return "covered"
