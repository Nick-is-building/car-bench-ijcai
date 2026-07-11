"""PLAN state — produce the next executable batch of tool calls. Stufe 2.

The planner is called once per PLAN round (see ADR-0002). Each round it must
return ONLY steps whose argument values are fully known from the ledger right
now; steps that depend on results of earlier steps come in the next round,
after those results arrived. An empty step list means the turn's tool work is
complete.
"""
from __future__ import annotations

import json

from loguru import logger as _log
from pydantic import BaseModel, Field, field_validator

from .. import llm
from ..capability import fuzzy_catalog_hint
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
    # exact tool names that were required but absent (mandatory with
    # capability_missing=true — the claim is verified against the schemas)
    missing_tools: list[str] = Field(default_factory=list)


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
workaround. Set capability_missing=true, return steps=[], list the exact tool \
names you looked for in missing_tools, and explain in done_reason. A workaround \
that skips a required step is a fabrication. This includes prerequisite steps: \
if opening the sunroof requires opening the sunshade first but \
open_close_sunshade is absent from schemas, set capability_missing=true.
- Before claiming a missing capability, RE-SCAN the full tool schemas: the tool \
you need may exist under a different name than your first guess. A prerequisite \
whose tool IS in the schemas is simply planned as a step, never refused. Your \
missing_tools claim is verified against the schemas; a claim naming tools that \
exist will be rejected.
- UNKNOWN VALUES: If a tool result field shows "unknown", treat it as MISSING \
INFORMATION, not as a factual value. Do NOT apply policy rules based on unknown \
values. For example, if fog_lights status is "unknown", do NOT block high beams \
(the policy "high beams must not be on while fog lights are on" only applies \
when fog_lights is confirmed "on", not when it is "unknown"). Proceed with the \
user's request if the unknown field is not directly required for the action.
- When a request contains several independent parts, plan the feasible parts for \
execution NOW. Never hold a feasible action back behind a question or a limitation \
statement about a different part of the request.
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
    ctx.capability_claim_rebutted = False
    if result.capability_missing:
        # deterministic guard: honor the claim only if a named tool is truly
        # absent from the schemas (B6 root cause: false capability refusals)
        from ..capability import CapabilityIndex
        index = CapabilityIndex(ctx.tools)
        claimed = [t.strip() for t in result.missing_tools if t and t.strip()]
        truly_missing = [t for t in claimed if not index.has_tool(t)]
        if truly_missing:
            # Fuzzy-gate (OI-011 H-R1): invented alias near a real tool → re-plan
            # hint instead of immediate refusal.  Threshold 0.80 is conservative:
            # e.g. "navigation_remove_waypoint" scores ~0.81 against
            # "navigation_delete_waypoint" (match → re-plan), while
            # "open_close_sunshade" scores ~0.76 against "open_close_sunroof"
            # (no match → genuine refusal preserved).
            hints: list[str] = []
            no_match: list[str] = []
            for t in truly_missing:
                candidates = fuzzy_catalog_hint(t, index.tool_names)
                if candidates:
                    top = " / ".join(candidates)
                    hints.append(
                        f"PLAN-GUARD: '{t}' is not in the catalog; "
                        f"closest catalog match: {top}. "
                        "Use the exact catalog name."
                    )
                else:
                    no_match.append(t)

            if no_match:
                # at least one tool with no catalog neighbour → genuine missing capability
                ctx.capability_missing = True
                _log.warning(
                    "PLAN-GUARD: genuine missing capability — refusal",
                    source="planner_capability_missing",
                    missing_tools=no_match,
                    claimed=claimed,
                )
            elif ctx.capability_rebuttals < 2:
                # all claimed-missing tools have fuzzy matches → re-plan with hints
                ctx.capability_claim_rebutted = True
                for h in hints:
                    if h not in ctx.policy_notes:
                        ctx.policy_notes.append(h)
                _log.info(
                    "PLAN-GUARD: fuzzy match found — re-planning instead of refusal",
                    source="planner_capability_missing",
                    fuzzy_tools=truly_missing,
                    rebuttal_round=ctx.capability_rebuttals + 1,
                )
            else:
                # rebuttals exhausted (≥2 fuzzy re-plans gave no correction) → refusal
                ctx.capability_missing = True
                _log.warning(
                    "PLAN-GUARD: fuzzy re-plans exhausted — refusal",
                    source="planner_capability_missing_exhausted",
                    missing_tools=truly_missing,
                )
        else:
            ctx.capability_claim_rebutted = True
            names = ", ".join(claimed) if claimed else "(no tool named)"
            note = (
                "PLAN-GUARD: capability_missing claim rejected — the named "
                f"tools exist in the schemas or none were named: {names}. "
                "Plan the required steps using the exact schema names."
            )
            if note not in ctx.policy_notes:
                ctx.policy_notes.append(note)
            _log.info(
                "PLAN-GUARD: false capability claim rejected",
                source="planner_false_claim",
                named_tools=claimed,
            )
    return [
        {"tool": s.tool, "arguments": s.arguments, "rationale": s.rationale}
        for s in result.steps
    ]


try:
    from ..state_machine import TurnContext
except ImportError:
    pass
