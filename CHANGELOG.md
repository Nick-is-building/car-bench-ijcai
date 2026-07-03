# Changelog

All notable changes to the glassbox CAR-bench agent.

## [Unreleased]

## [0.3.0] — 2026-07-03

### Added
- Stufe 3 — Capability-Matcher vollständig (commit 589db23):
  - `capability.py`: `CapabilityMatcher.check()` — deterministisch, kein LLM;
    `required_but_missing_tools` cross-validiert gegen Tool-Index;
    param-Name-Normierung via `split("=")[0].strip()`
  - `prompts/intake.py`: `required_params` auf user-explizit genannte Werte beschränkt;
    behebt Base-False-positive (LLM listete halluzinierte Alias-Namen für `get_weather`)
  - `prompts/capability_check.py`: `generate_honest_refusal()` via LLM (Refusal-Schema, Temp 0)
  - `prompts/plan.py`: `capability_missing`-Flag + "Fully handled" = alle State-Changes ausgeführt
  - `prompts/verify.py`: Anti-Fabrikations-Regeln (nur vergangene Tool-Calls, nie Future-Tense)
  - `state_machine.py`: AUT-POL:005 deterministischer Guard — `open_close_sunroof` blockiert
    wenn `open_close_sunshade` nicht im Katalog; unabhängig von LLM-Zuverlässigkeit
  - `tests/test_glassbox_state_machine.py`: 22→23 Tests; `TOOLS_NO_SUNSHADE`-Fixture für Guard
  - `docs/experiments/2026-07-03-stufe3-smoke.md`: Stabilitätstest (4 Läufe) dokumentiert;
    Hallucination Pass^1 = 100 % in 4/4 Läufen (deterministisch bestätigt)
- `paper/` — IJCAI-ECAI 2026 Paper-Skelett:
  - `ijcai26.sty`, `named.bst` aus dem offiziellen Author-Kit
  - `main.tex`: 4-Seiten-Struktur vorgezeichnet (Abstract + 5 Abschnitte + Related Work + Fazit)
  - `references.bib`: CAR-bench-Pflichtzitat + tau-bench
  - `build.sh`: pdflatex-Kompilation + Seitenzahl-Check
  - `claims.md`: Claims-zu-Evidenz-Tabelle (Bauplan §8)
  - `figures/`: Platzhalter für skriptbasierte Ergebnisgrafiken

### Fixed
- `.gitignore`: LaTeX-Build-Artefakte (`*.aux`, `*.log`, `*.bbl` etc.) ausgeschlossen

## [0.2.1] — 2026-07-03

### Fixed
- `scenarios/track_1_agent_under_test/local_smoke_glassbox.toml`: removed `AGENT_CLASS=glassbox`
  from `cmd` field — `subprocess.Popen` without `shell=True` treats env-var prefixes as the
  executable name; variable is now passed via the parent process environment instead.

### Added
- `docs/experiments/2026-07-03-smoke-glassbox.md` — first successful end-to-end smoke run:
  Pass^1 33.3 % (base 100 %, hallucination 0 %, disambiguation 0 %); confirms Stufe-2 pipeline
  runs with real APIs; Stufe-3/6 stubs are next.

### Changed
- `MAX_PLAN_ROUNDS` 8 → 16 (ADR-0003): train tasks have up to 9 GT actions in
  all three splits; bound is a last-resort loop stop, not a task-size budget.
  New `TurnContext.plan_bound_hit` flag + agent-layer warning instrument any
  cut-off for dev runs.

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
