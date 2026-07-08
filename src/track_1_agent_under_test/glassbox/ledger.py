"""
Provenienz-Ledger — Stufe 1.

Jeder Dialog-Schritt wird mit Quelle protokolliert. Alle anderen Glassbox-Module
lesen ausschliesslich aus dem Ledger. Kein Modul darf Fakten ohne Ledger-Deckung
behaupten oder verwenden.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


SourceKind = Literal["user", "agent", "tool_call", "tool_result", "system"]


@dataclass
class LedgerEntry:
    turn: int
    timestamp: datetime
    kind: SourceKind
    content: Any
    source: str
    tool_name: str | None = None
    tool_call_id: str | None = None


class Ledger:
    """Append-only record of every dialog step with provenance."""

    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []
        self._turn: int = 0

    # --- write ---

    def add_system(self, text: str) -> None:
        self._append(LedgerEntry(
            turn=0, timestamp=_now(), kind="system",
            content=text, source="system",
        ))

    def add_user_turn(self, text: str) -> None:
        self._turn += 1
        self._append(LedgerEntry(
            turn=self._turn, timestamp=_now(), kind="user",
            content=text, source="user",
        ))

    def add_tool_call(self, name: str, arguments: dict, call_id: str) -> None:
        self._append(LedgerEntry(
            turn=self._turn, timestamp=_now(), kind="tool_call",
            content=arguments, source="agent",
            tool_name=name, tool_call_id=call_id,
        ))

    def add_tool_result(self, name: str, result: Any, call_id: str) -> None:
        self._append(LedgerEntry(
            turn=self._turn, timestamp=_now(), kind="tool_result",
            content=result, source=f"tool:{name}",
            tool_name=name, tool_call_id=call_id,
        ))

    def add_agent_response(self, text: str) -> None:
        self._append(LedgerEntry(
            turn=self._turn, timestamp=_now(), kind="agent",
            content=text, source="agent",
        ))

    # --- read ---

    def has_tool_result(self, tool_name: str) -> bool:
        return any(
            e.kind == "tool_result" and e.tool_name == tool_name
            for e in self._entries
        )

    def get_tool_results(self, tool_name: str) -> list[Any]:
        return [
            e.content for e in self._entries
            if e.kind == "tool_result" and e.tool_name == tool_name
        ]

    def get_tool_calls_this_turn(self) -> list[LedgerEntry]:
        return [
            e for e in self._entries
            if e.kind == "tool_call" and e.turn == self._turn
        ]

    def failed_call_signatures(self) -> set[str]:
        """Signatures of calls whose result reported a runtime failure.

        Format matches ``PlannedCall.signature`` (``tool:json(args)``) so the
        state machine can refuse to re-emit an identical call the evaluator
        already rejected (OI-017 retry bound). This survives the fresh
        TurnContext each user turn creates, because the ledger persists per
        context while executed_signatures does not.
        """
        import json as _json

        calls_by_id: dict[str, LedgerEntry] = {
            e.tool_call_id: e for e in self._entries
            if e.kind == "tool_call" and e.tool_call_id is not None
        }
        sigs: set[str] = set()
        for e in self._entries:
            if e.kind != "tool_result" or not _is_failure_result(e.content):
                continue
            call = calls_by_id.get(e.tool_call_id)
            if call is not None:
                sigs.add(f"{call.tool_name}:{_json.dumps(call.content, sort_keys=True)}")
        return sigs

    def get_state_changing_tools_called(self) -> set[str]:
        """Returns all tool names that were called (state-changing or not)."""
        return {
            e.tool_name for e in self._entries
            if e.kind == "tool_call" and e.tool_name is not None
        }

    @property
    def current_turn(self) -> int:
        return self._turn

    @property
    def entries(self) -> list[LedgerEntry]:
        return list(self._entries)

    def to_messages(self) -> list[dict]:
        """Convert ledger to LiteLLM-compatible message list."""
        msgs: list[dict] = []
        pending_calls: dict[str, str] = {}

        for e in self._entries:
            if e.kind == "system":
                msgs.append({"role": "system", "content": e.content})
            elif e.kind == "user":
                msgs.append({"role": "user", "content": e.content})
            elif e.kind == "agent":
                msgs.append({"role": "assistant", "content": e.content})
            elif e.kind == "tool_call":
                # group tool calls into the assistant message they belong to
                if msgs and msgs[-1]["role"] == "assistant" and "tool_calls" in msgs[-1]:
                    msgs[-1]["tool_calls"].append(_tc_dict(e))
                else:
                    msgs.append({
                        "role": "assistant", "content": None,
                        "tool_calls": [_tc_dict(e)],
                    })
                pending_calls[e.tool_call_id] = e.tool_name  # type: ignore[index]
            elif e.kind == "tool_result":
                msgs.append({
                    "role": "tool",
                    "tool_call_id": e.tool_call_id,
                    "content": str(e.content),
                })
        return msgs

    # --- internal ---

    def _append(self, entry: LedgerEntry) -> None:
        self._entries.append(entry)


def _is_failure_result(content: Any) -> bool:
    """True if a tool result reports a runtime failure (evaluator contract).

    Two shapes count as failure (OI-016):
      1. The structured evaluator contract — a JSON string like
         ``{"status": "FAILURE", "errors": {...}}`` on rejection.
      2. A plain-string result that a raising tool surfaces, e.g. a
         ``TypeError`` on an unexpected keyword argument comes back as
         ``"Error: SetAmbientLights.invoke() got an unexpected keyword ..."``.
         Recognising these lets the retry bound stop an identical failing call
         from looping to MAX_PLAN_ROUNDS instead of treating it as success.
    """
    import json as _json

    text = content if isinstance(content, str) else None
    if text is None:
        return False
    try:
        data = _json.loads(text)
    except (ValueError, TypeError):
        data = text  # not JSON — treat the raw string as the payload
    if isinstance(data, dict):
        status = data.get("status")
        if isinstance(status, str) and status.upper() == "FAILURE":
            return True
        return bool(data.get("errors"))
    if isinstance(data, str):
        s = data.lstrip().lower()
        # Match the shape a raising tool actually produces ("Error: ...",
        # "Exception: ...", a Python "Traceback (most recent call last):"),
        # not merely any word starting with "error" (e.g. "Error-free ...").
        return s.startswith(("error:", "exception:", "traceback ("))
    return False


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _tc_dict(e: LedgerEntry) -> dict:
    import json
    return {
        "id": e.tool_call_id,
        "type": "function",
        "function": {
            "name": e.tool_name,
            "arguments": json.dumps(e.content),
        },
    }
