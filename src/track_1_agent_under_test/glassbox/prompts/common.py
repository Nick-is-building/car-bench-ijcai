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
