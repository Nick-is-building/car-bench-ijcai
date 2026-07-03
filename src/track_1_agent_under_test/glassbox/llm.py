"""
LiteLLM-Wrapper fuer alle Glassbox-Module.

Einheitlicher Aufruf mit:
- Temperatur 0 (deterministisch)
- JSON-Schema-Output (strukturierter Output)
- Retry bei Fehlformat (max 2)
- Prompt-Caching (anthropic ephemeral)
"""
from __future__ import annotations

import os
import time
from contextvars import ContextVar
from typing import Any, Type

from litellm import completion
from pydantic import BaseModel


_DEFAULT_MODEL = os.getenv("AGENT_LLM", "anthropic/claude-sonnet-4-6")
_MAX_RETRIES = 2

# Per-turn usage accumulator (set by the A2A layer, async-safe via ContextVar).
# Keys: prompt_tokens, completion_tokens, thinking_tokens, cost, num_llm_calls,
# total_llm_time_ms.
_metrics_sink: ContextVar[dict | None] = ContextVar("glassbox_metrics_sink", default=None)


def set_metrics_sink(sink: dict | None) -> None:
    _metrics_sink.set(sink)


def _completion_with_metrics(**kwargs) -> Any:
    start = time.perf_counter()
    resp = completion(**kwargs)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    sink = _metrics_sink.get()
    if sink is not None:
        usage = getattr(resp, "usage", None)
        if usage:
            sink["prompt_tokens"] = sink.get("prompt_tokens", 0) + (getattr(usage, "prompt_tokens", 0) or 0)
            sink["completion_tokens"] = sink.get("completion_tokens", 0) + (getattr(usage, "completion_tokens", 0) or 0)
            details = getattr(usage, "completion_tokens_details", None)
            if details:
                sink["thinking_tokens"] = sink.get("thinking_tokens", 0) + (getattr(details, "reasoning_tokens", 0) or 0)
        sink["cost"] = sink.get("cost", 0.0) + (getattr(resp, "_hidden_params", {}).get("response_cost", 0.0) or 0.0)
        sink["num_llm_calls"] = sink.get("num_llm_calls", 0) + 1
        sink["total_llm_time_ms"] = sink.get("total_llm_time_ms", 0.0) + elapsed_ms
    return resp


def call_structured(
    messages: list[dict],
    schema: Type[BaseModel],
    model: str | None = None,
    system: str | None = None,
    tools: list[dict] | None = None,
    temperature: float = 0.0,
) -> BaseModel:
    """
    Call LLM and parse response into a Pydantic schema.
    Retries up to _MAX_RETRIES times on parse failure.
    """
    mdl = model or _DEFAULT_MODEL
    msgs = _with_system(messages, system)
    _apply_cache_hints(msgs, tools)

    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            kwargs: dict[str, Any] = {
                "model": mdl,
                "messages": msgs,
                "temperature": temperature,
                "response_format": schema,
            }
            if tools:
                kwargs["tools"] = tools
            resp = _completion_with_metrics(**kwargs)
            content = resp.choices[0].message.content
            if isinstance(content, str):
                return schema.model_validate_json(content)
            return schema.model_validate(content)
        except Exception as exc:
            last_err = exc
            if attempt < _MAX_RETRIES:
                msgs = msgs + [
                    {"role": "assistant", "content": str(exc)},
                    {"role": "user", "content": "Your response was not valid JSON matching the schema. Please retry."},
                ]
    raise RuntimeError(f"LLM call failed after {_MAX_RETRIES} retries: {last_err}")


def call_tool_use(
    messages: list[dict],
    tools: list[dict],
    model: str | None = None,
    system: str | None = None,
    temperature: float = 0.0,
) -> dict:
    """
    Call LLM in native tool-use mode.
    Returns the raw choice message as a dict.
    """
    mdl = model or _DEFAULT_MODEL
    msgs = _with_system(messages, system)
    _apply_cache_hints(msgs, tools)

    resp = _completion_with_metrics(
        model=mdl,
        messages=msgs,
        tools=tools,
        temperature=temperature,
    )
    return resp.choices[0].message.model_dump(exclude_unset=True)


def _with_system(messages: list[dict], system: str | None) -> list[dict]:
    if not system:
        return messages
    if messages and messages[0].get("role") == "system":
        return messages
    return [{"role": "system", "content": system}] + messages


def _apply_cache_hints(messages: list[dict], tools: list[dict] | None) -> None:
    """Add anthropic prompt-caching hints to system message and last tool."""
    if messages and messages[0].get("role") == "system":
        messages[0]["cache_control"] = {"type": "ephemeral"}
    if tools:
        tools[-1].get("function", {})["cache_control"] = {"type": "ephemeral"}
