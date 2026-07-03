# Changelog

All notable changes to the glassbox CAR-bench agent.

## [Unreleased]

### Added
- Stufe 2 — Zustandsmaschine vollständig (ADR-0002):
  - `state_machine.py`: resumable `run_turn()`/`resume()` returning actions
    (`EmitToolCalls`/`EmitText`), bounded PLAN→POLICY_CHECK→EXECUTE loop,
    deterministic call ids, per-turn idempotency signatures, stub-safe
    pass-through defaults for Stufen 3–7
  - `prompts/intake.py`, `prompts/plan.py`, `prompts/verify.py` implemented
    (Temp 0, JSON-Schema); `prompts/respond.finalize` as deterministic cleanup
  - `prompts/common.py`: deterministic transcript/tool-catalog rendering
  - `glassbox_agent.py`: rewired to the resumable protocol, turn metrics via
    ContextVar sink in `llm.py`
  - `tests/test_glassbox_state_machine.py`: 9 deterministic unit tests
    (fake LLM, no API keys needed)
- `src/track_1_agent_under_test/glassbox/` — deterministic shell package skeleton
  - `ledger.py` — Provenienz-Ledger (Stufe 1, fully implemented)
  - `state_machine.py` — Zustandsmaschine INTAKE→RESPOND (Stufe 2, stub)
  - `capability.py` — Capability-Matcher + CapabilityIndex (Stufe 3, stub)
  - `policies.py` — 19 Policies as deterministic predicates (Stufe 4, stub)
  - `guard.py` — Fabrikations-Waechter (Stufe 5, stub)
  - `disambiguation.py` — Disambiguierungs-Motor (Stufe 6, stub)
  - `auditor.py` — Auditor at two checkpoints (Stufe 7, stub)
  - `llm.py` — shared LiteLLM wrapper, Temp 0, JSON schema, prompt caching
  - `prompts/` — one module per state machine state
- `src/track_1_agent_under_test/glassbox_agent.py` — A2A executor wrapping glassbox
- `server.py` updated: `AGENT_CLASS=glassbox` env var activates glassbox executor
- `scenarios/track_1_agent_under_test/local_smoke_glassbox.toml` — glassbox smoke test
- `docs/devlog.md`, `docs/decisions/0001-deterministische-schale.md`, `docs/references.md`
- `.gitignore`: added `_local/`

## [0.0.1] — 2026-07-03

- Fork of `CAR-bench/car-bench-ijcai` (Nick-is-building fork)
- Environment setup: uv, Python 3.12, track-1-agent + car-bench-evaluator extras
- third_party/car-bench cloned via setup script
