"""PLAN state — build ordered tool-call plan from intent."""
from __future__ import annotations

from pydantic import BaseModel
from uuid import uuid4

from ..llm import call_structured


class PlanStep(BaseModel):
    tool: str
    arguments: dict
    rationale: str


class Plan(BaseModel):
    steps: list[PlanStep]


_PLAN_SYSTEM = """\
You are the planning layer of an in-car voice assistant.
Given the user's intent and available tools, produce an ORDERED list of tool calls
needed to fulfill the request.
Rules:
- Only include tools that are in the provided tool list
- Only use parameter names that exist in the tool schema
- If a value is not known yet (needs a prior tool result), mark it as null
- Do NOT execute tools in parallel if later steps depend on earlier results
- Prefer the minimum number of steps necessary
"""


def build_plan(ctx: "TurnContext") -> list[dict]:  # type: ignore[name-defined]
    """
    Build ordered tool-call plan.
    Returns list of dicts with keys: tool, arguments, call_id, rationale.

    Stufe 2 implementation point.
    """
    raise NotImplementedError("plan.build_plan — implement in Stufe 2")


def _assign_call_ids(steps: list[PlanStep]) -> list[dict]:
    return [
        {"tool": s.tool, "arguments": s.arguments, "call_id": f"call_{uuid4().hex[:8]}", "rationale": s.rationale}
        for s in steps
    ]


try:
    from ..state_machine import TurnContext
except ImportError:
    pass
