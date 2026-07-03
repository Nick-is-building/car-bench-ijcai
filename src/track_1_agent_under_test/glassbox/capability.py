"""
Capability-Matcher — Stufe 3.

Kompiliert beim Start einen Faehigkeits-Index aus den 58 Tool-Schemas.
Prueft bei jeder Anfrage deterministisch, ob die geforderte Faehigkeit gedeckt ist.
Drei Ausgaenge: covered → Planner; uncovered → ehrliches Eingestaendnis; ambiguous → Disambiguierung.

Faengt alle drei Entzugsarten: entferntes Tool, entfernter Parameter, entferntes Result-Feld.
Check laeuft an jedem Planungsschritt, nicht nur beim Intake.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
        Stufe 3 implementation point.

        intent keys (from prompts.intake):
          - required_tools: list[str]   tools the plan would need
          - required_params: dict[str, list[str]]  params per tool
          - is_ambiguous: bool

        Returns:
          "covered"   — all required tools + params exist in index
          "uncovered" — at least one required tool or param is missing
          "ambiguous" — intent is ambiguous and needs clarification first
        """
        raise NotImplementedError("CapabilityMatcher.check — implement in Stufe 3")

    def check_step(self, tool_name: str, arguments: dict) -> CapabilityResult:
        """Per-step check during EXECUTE — catches mid-conversation capability removal."""
        if not self.index.has_tool(tool_name):
            return "uncovered"
        for param in arguments:
            if not self.index.has_parameter(tool_name, param):
                return "uncovered"
        return "covered"
