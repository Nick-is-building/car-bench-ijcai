# Changelog

All notable changes to the glassbox CAR-bench agent.

## [Unreleased]

### Fixed
- B6-Abnahme-Lauf (Pass^3 60.0 %, n=5 Base-Tasks × 3 Trials) deckte zwei Fehler auf
  (Analyse in `docs/devlog.md`, Restrisiken in OI-011):
  - `policies.py`: AUT-POL:019-False-Positive — `_eval_state_precondition` prüfte
    das Prädikat auf dem projizierten Zustand INKLUSIVE des Trigger-Call-Effekts
    (`navigation_delete_waypoint` blockierte sich selbst: count 3→2 < 3). Neu:
    `projected_before(call)` — Preconditions gelten für den Zustand VOR dem Call.
    2 Regressionstests (deterministische Repro aus base_56 T2)
  - `prompts/plan.py` + `state_machine.py`: falsche Capability-Refusals — das
    Planner-`capability_missing`-Flag wurde nie gegen den Katalog verifiziert.
    Neu: `missing_tools`-Feld im Plan-Schema; PLAN-GUARD ehrt das Flag nur, wenn
    ein benanntes Tool wirklich nicht im Index ist, sonst Note + Re-Plan (max. 2),
    danach ehrliches VERIFY-Ende statt Refusal. 3 Tests (1 angepasst, 2 neu)

### Added
- B6-Wiederholungslauf (Lauf 20260704-194848, identische Konfiguration): Pass^3
  weiter 60.0 % — Hypothese (80 %) nicht bestätigt, Abnahme-Kriterium nicht
  erfüllt. Beide Fixes wirken nachweislich (kein AUT-POL:019-FP mehr, Refusals
  5/15 → 3/15, policy_aut_errors 0 in 15/15 = kumuliert 0/30), aber: OI-007
  traf base_10 2×, drei First-Turn-Refusals ohne Tool-Call (OI-011-Restrisiko,
  erfundene Tool-Namen), neuer Klasse-C-Fail LLM-POL:022 (→ OI-012).
  Analyse in `docs/devlog.md`; Rohdaten beider B6-Läufe unter
  `docs/experiments/2026-07-04-b6-*.json`
- `docs/open_issues.md`: OI-012 (LLM-POL:022, erster Klasse-C-Fail);
  OI-011-Update (Refusal-Muster wandert, erfundene Tool-Namen als Restursache)
- `docs/devlog.md`: Lessons aus B6 (deterministisches Gate für jedes
  LLM-Entscheidungsfeld — verbindlich für Auftrag C; projected_before-Regel;
  Fail-Landkarte) + Hypothesen-Eintrag für den Wiederholungslauf
- `docs/devlog.md`: B6-Ergebniseintrag — Hypothesen H1/H4 widerlegt, H3 bestätigt;
  policy_aut_errors=0 in 15/15 (deterministischer AUT-Teil hielt); Abnahme nicht
  bestanden, Wiederholungslauf erst nach erneuter Freigabe
- `docs/open_issues.md`: OI-011 (LLM-Pfad-Refusals, Intake-Guard + Agent-Logs offen);
  OI-007 empirisch belegt (base_10 T2, fehlende Wetter-Confirmation)

## [0.4.0] — 2026-07-04

### Added
- Stufe 4 — Policy-Compiler (Auftrag B, ADR-0004):
  - `docs/decisions/0004-policy-compiler-regeltabelle.md`: ehrliche Klassifikation
    aller 19 Policies (9× A voll deterministisch, 7× B teilweise, 3× C inhärent
    semantisch) inkl. Implementierungs-Status und bewusster Grenzen
  - `policies.py`: EINE deklarative `RULES`-Tabelle mit 7 generischen Regeltypen
    (companion_available, value_bound, state_precondition, prior_observation,
    state_companion, no_parallel, obligation_note); `PolicyChecker.pre_flight()`
    iteriert generisch — Tool-Namen nur in Daten, nie im Kontrollfluss;
    Zustandsableitung ausschließlich aus dem Ledger (`derive_known_state`,
    `TOOL_EFFECTS`, `OBSERVATION_TOOLS`); Null-FP-Disziplin: unbekannter Zustand
    blockiert nie (höchstens Observation-Injektion mit Schleifenschutz)
  - `prompts/common.py`: `SEMANTIC_POLICY_OBLIGATIONS` (Klasse C + B-Reste, klar
    als nicht-maschinell-geprüft markiert) + `render_policy_notes()`;
    in PLAN- und VERIFY-Prompts verdrahtet
  - `prompts/policy_check.py`: `generate_policy_block()` — natürliche, ehrliche
    Policy-Block-Antwort (LLM, Temp 0); `respond.py` delegiert dorthin
  - `tests/test_glassbox_policies.py`: 28 Tests — pro Regeltyp Verletzungs- und
    Nicht-Verletzungsfall plus Null-FP-Gesamttest; kein LLM, kein API-Key

### Changed
- `state_machine.py`: hartcodierter AUT-POL:005-Guard gelöscht und durch den
  generischen Pre-Flight ersetzt (Refusal / Block / Injektion / Defer / Notes);
  `TurnContext.policy_notes` neu. Alle vorbestehenden Tests unverändert grün
  (Generalisierungs-Beweis B3). AUT-POL:005 erzwingt jetzt zusätzlich den
  Wert-Aspekt (Sunshade 100 % vor Sunroof-Öffnung, behebt OI-003)

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
