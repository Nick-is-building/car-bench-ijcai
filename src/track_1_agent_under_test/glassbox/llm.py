"""
LiteLLM-Wrapper fuer alle Glassbox-Module.

Einheitlicher Aufruf mit:
- Temperatur 0 (deterministisch)
- JSON-Schema-Output (strukturierter Output)
- Retry bei Fehlformat (max 2)
- Prompt-Caching (anthropic ephemeral, nur bei anthropic/-Prefix)
- Transient-Retry mit exponentiellem Backoff (2s/4s/8s) vor Eskalation
"""
from __future__ import annotations

import os
import time
from contextvars import ContextVar
from typing import Any, Type

import litellm.exceptions as _llm_exc
from litellm import completion
from pydantic import BaseModel


_DEFAULT_MODEL = os.getenv("AGENT_LLM", "anthropic/claude-sonnet-4-6")
_MAX_RETRIES = 2

# Fixed backoff delays (seconds) for transient errors: attempt 1→2s, 2→4s, 3→8s.
_TRANSIENT_BACKOFF_S = (2, 4, 8)
_TRANSIENT_MAX_ATTEMPTS = len(_TRANSIENT_BACKOFF_S) + 1

_TRANSIENT_EXCEPTIONS = (
    _llm_exc.Timeout,
    _llm_exc.RateLimitError,
    _llm_exc.ServiceUnavailableError,
    _llm_exc.InternalServerError,
    _llm_exc.APIConnectionError,
    _llm_exc.BadGatewayError,
)

# Per-turn usage accumulator (set by the A2A layer, async-safe via ContextVar).
# Keys: prompt_tokens, completion_tokens, thinking_tokens, cost, num_llm_calls,
# total_llm_time_ms.
_metrics_sink: ContextVar[dict | None] = ContextVar("glassbox_metrics_sink", default=None)


def set_metrics_sink(sink: dict | None) -> None:
    _metrics_sink.set(sink)


def _is_anthropic(model: str) -> bool:
    """True iff the model string uses the direct Anthropic provider."""
    return model.startswith("anthropic/")


def _raw_completion(**kwargs) -> Any:
    """Single completion call with transient-error retry + exponential backoff."""
    last_exc: Exception | None = None
    for attempt in range(_TRANSIENT_MAX_ATTEMPTS):
        try:
            return completion(**kwargs)
        except _TRANSIENT_EXCEPTIONS as exc:
            last_exc = exc
            if attempt < len(_TRANSIENT_BACKOFF_S):
                time.sleep(_TRANSIENT_BACKOFF_S[attempt])
            else:
                raise
    raise last_exc  # unreachable but satisfies type checker


def _completion_with_metrics(**kwargs) -> Any:
    start = time.perf_counter()
    resp = _raw_completion(**kwargs)
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
    """Call LLM and parse response into a Pydantic schema.
    Retries up to _MAX_RETRIES times on parse failure.
    Transient network/rate errors are retried with fixed backoff before escalating.
    """
    mdl = model or _DEFAULT_MODEL
    msgs = _with_system(messages, system)
    _apply_cache_hints(msgs, tools, mdl)

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
    """Call LLM in native tool-use mode.
    Returns the raw choice message as a dict.
    """
    mdl = model or _DEFAULT_MODEL
    msgs = _with_system(messages, system)
    _apply_cache_hints(msgs, tools, mdl)

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


def _apply_cache_hints(messages: list[dict], tools: list[dict] | None, model: str) -> None:
    """Add Anthropic prompt-caching hints. Skipped for non-anthropic/ providers."""
    if not _is_anthropic(model):
        return
    if messages and messages[0].get("role") == "system":
        messages[0]["cache_control"] = {"type": "ephemeral"}
    if tools:
        tools[-1].get("function", {})["cache_control"] = {"type": "ephemeral"}
