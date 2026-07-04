"""PLAN state — produce the next executable batch of tool calls. Stufe 2.

The planner is called once per PLAN round (see ADR-0002). Each round it must
return ONLY steps whose argument values are fully known from the ledger right
now; steps that depend on results of earlier steps come in the next round,
after those results arrived. An empty step list means the turn's tool work is
complete.
"""
from __future__ import annotations

import json

from pydantic import BaseModel, Field, field_validator

from .. import llm
from . import common


class PlanStep(BaseModel):
    tool: str
    arguments_json: str  # JSON object string — provider-safe free-form args
    rationale: str = ""

    @field_validator("arguments_json")
    @classmethod
    def _must_be_json_object(cls, v: str) -> str:
        parsed = json.loads(v)
        if not isinstance(parsed, dict):
            raise ValueError("arguments_json must encode a JSON object")
        return v

    @property
    def arguments(self) -> dict:
        return json.loads(self.arguments_json)


class Plan(BaseModel):
    reasoning: str = ""
    steps: list[PlanStep] = Field(default_factory=list)
    done_reason: str = ""  # why no further steps are needed (when steps is empty)
    capability_missing: bool = False  # True when a required tool is absent from schemas


_PLAN_SYSTEM = """\
You are the planning layer of an in-car voice assistant. You decide which tool \
calls to execute NEXT for the user's current request.

Hard rules:
- Only use tools from the provided tool schemas, with EXACT tool and parameter \
names. Never invent tools or parameters.
- arguments_json must be a JSON object string with concrete values only.
- Include ONLY steps whose argument values are already known from the \
conversation, the tool results shown, or the tool schema defaults. If a value \
depends on a tool result that does not exist yet, plan the producing call now \
and leave the dependent call for the next round.
- Steps you return are executed as one parallel batch: never put a step and \
another step that depends on its result in the same batch.
- Never repeat a tool call that already appears with the same arguments in \
this turn's executed calls.
- Prefer the minimum number of calls necessary. Read state before changing it \
only when the correct target value depends on the current state.
- If the request is fully handled (or needs no tools at all), return an empty \
steps list and state why in done_reason. "Fully handled" means ALL state \
changes the user requested have been executed in this turn (confirmed by \
tool call entries in the conversation). Never declare done after only \
state-reading calls (get_* tools) while the requested state changes \
(open_*, set_*, close_*, etc.) have not been executed yet.
- CRITICAL — missing capability: if completing the request requires a tool or \
prerequisite step that is NOT in the provided tool schemas, do NOT improvise a \
workaround. Set capability_missing=true, return steps=[], and explain in \
done_reason. A workaround that skips a required step is a fabrication. This \
includes prerequisite steps: if opening the sunroof requires opening the sunshade \
first but open_close_sunshade is absent from schemas, set capability_missing=true.
"""


def build_plan(ctx: "TurnContext") -> list[dict]:  # type: ignore[name-defined]
    """Return the next executable steps as dicts: tool, arguments, rationale.

    Empty list = turn complete. call_ids are assigned by the state machine.
    """
    system = (
        _PLAN_SYSTEM
        + "\n" + common.SEMANTIC_POLICY_OBLIGATIONS
        + "\n\n# Task context\n" + common.task_system_text(ctx.ledger)
        + "\n\n# Tool schemas\n" + common.render_tool_schemas(ctx.tools)
    )
    transcript = common.render_transcript(ctx.ledger, include_tools=True)
    intent = json.dumps(ctx.intent, sort_keys=True)
    messages = [{
        "role": "user",
        "content": (
            f"# Conversation so far (tool calls/results included)\n{transcript}\n\n"
            f"# Extracted intent for the current user message\n{intent}"
            f"{common.render_policy_notes(ctx.policy_notes)}\n\n"
            f"# Planning round {ctx.plan_round} of this turn\n"
            "Return the next batch of executable tool calls, or an empty steps "
            "list if the request is fully handled."
        ),
    }]
    result = llm.call_structured(messages, Plan, model=ctx.model, system=system)
    if result.capability_missing:
        ctx.capability_missing = True
    return [
        {"tool": s.tool, "arguments": s.arguments, "rationale": s.rationale}
        for s in result.steps
    ]


try:
    from ..state_machine import TurnContext
except ImportError:
    pass
