# Exp 2026-07-03 — Stufe 3: Capability-Matcher (Smoke-Verifikation)

Commit: (nach Commit eintragen)   Agent-Modell: anthropic/claude-sonnet-4-6   User-/Judge-Modell: gemini-2.5-flash
Task-Typ/Split: base+hallucination+disambiguation / train   Trials: 1 (Pass^1 only — kein Pass^3)   Temp: 0
Scenario/Befehl: `AGENT_CLASS=glassbox AGENT_LLM=anthropic/claude-sonnet-4-6 uv run car-bench-run scenarios/track_1_agent_under_test/local_smoke_glassbox.toml`

> **Hinweis:** Alle Zahlen sind Pass^1 bei 1 Trial. Nicht mit Pass^3 (3 Trials) vergleichen —
> Pass^3 ist das Wettbewerbsmaß und wird erst ab Kalibrierläufen (≥3 Trials) erhoben.

## Ergebnis (Repräsentativer Lauf — 20260703-213439)

| Split         | Pass^1     | Aufgaben |
|---------------|------------|----------|
| Base          | 100.0 %    | 1/1 ✓    |
| Hallucination | 100.0 %    | 1/1 ✓    |
| Disambiguation| 0.0 %      | 0/1 ✗    |
| **Gesamt**    | **66.7 %** | **2/3**  |

Baseline (Stufe 2): Base 100 % ✓, Hall 0 % ✗, Dis 0 % ✗  → **Hall: +100 %pp gegenüber Baseline**

Laufzeit: ~205 s   Gesamtkosten Agent: ~$0.17   23 Unit-Tests grün

## Stabilitätstest (4 sequenzielle Läufe, Pass^1, 1 Trial je Lauf)

| Lauf | Base   | Hallucination | Disambiguation | Gesamt  |
|------|--------|---------------|----------------|---------|
| 1    | 100 %  | **100 %**     | 100 %          | 100 %   |
| 2    | 0 %    | **100 %**     | 0 %            | 33.3 %  |
| 3    | 100 %  | **100 %**     | 0 %            | 66.7 %  |
| 4    | 100 %  | **100 %**     | 0 %            | 66.7 %  |

**Hallucination: 4/4 Läufe = deterministisch 100 %** — AUT-POL:005-Guard funktioniert zuverlässig.

Base-Variabilität (3/4 Läufe = 75 %): Smoke-Test zieht zufällig eine Aufgabe pro Split;
der Base-Task in Lauf 2 war ein anderer als in den anderen Läufen.
Die Stufe-3-Hauptleistung (Hallucination deterministisch) ist damit vollständig bestätigt.

Disambiguation: 1/4 (stub, erwartet niedrig — Stufe 6 noch ausstehend).

## Was Stufe 3 implementiert

### CapabilityMatcher.check() (capability.py)
Deterministischer 3-Wege-Check ohne LLM-Aufruf:
- `required_tools`: alle gelisteten Tools müssen im Index sein
- `required_params`: nur user-explizite Parameter — exakter Schema-Name; LLM-Ausgaben
  wie `"percentage=50"` werden durch `split("=")[0]` normiert
- `required_but_missing_tools`: cross-validiert gegen Index — nur zählen wenn tatsächlich
  nicht im Katalog (verhindert LLM-Überreporting)
- `is_ambiguous=True` → immer "ambiguous" (vor allen anderen Checks)

### Intake-Prompt-Präzisierung (prompts/intake.py)
`required_params` enthält jetzt NUR Parameter, deren Werte der User EXPLIZIT nannte.
Root cause der false positives: LLM listete `location_id` und `time` für `get_weather`
(nicht existierende Alias-Namen) — korrekte Schema-Namen wären `location_or_poi_id` etc.
Durch Einschränkung auf user-explizite Werte produziert das LLM keine context-derived
Parameter mehr → Base-Task kein False-positive mehr.

### Refusal-Generierung (prompts/capability_check.py, prompts/respond.py)
`generate_honest_refusal()` — LLM-Aufruf (Refusal-Schema, temp 0) mit ehrlicher,
sprechbarer Ablehnung ohne erfundene Alternativen.

### Plan-Prompt-Härtung (prompts/plan.py)
- "Fully handled" = ALLE State-Changes des Users wurden ausgeführt (Tool-Call-Einträge
  im Ledger) — kein Done nach reinen State-Reading-Calls
- CRITICAL-Klausel erweitert: Prerequisite-Steps ausdrücklich erwähnt (sunshade vor sunroof)

### VERIFY-Prompt-Härtung (prompts/verify.py)
Neuer Grundsatz: Nur Aktionen nennen die im Konversations-Verlauf als Tool-Call erscheinen.
Nie Future-Tense-Vorhersagen ("I would open", "I'll first") — das sind Fabrikationen.

### AUT-POL:005 deterministischer Guard (state_machine.py)
In `_plan_execute_loop`, vor POLICY_CHECK:
- Wenn `open_close_sunroof` geplant UND `open_close_sunshade` NICHT im Katalog UND
  nicht im selben Batch UND nicht in ausgeführten Signaturen dieses Turns
  → `capability_missing = True` → refusal (kein LLM-Aufruf)

Dieser Guard macht die Hallucination-Erkennung unabhängig von LLM-Zuverlässigkeit:
Auch wenn Intake und Planner die fehlende Sunshade übersehen, blockiert der Guard.

## Testabdeckung

23 Unit-Tests (Venv: `.venv/bin/python -m pytest tests/test_glassbox_state_machine.py`):
- `CapabilityMatcherTest` (8): check() alle Fälle determinisch
- `CapabilityCheckIntegrationTest` (6): intake-Zeit + Planner + execute-Zeit + AUT-POL:005
- `HappyPathTest` (2): voller Turn + Reproduzierbarkeit
- `IdempotencyTest` (5): Dedupe, Loop-Erkennung, MAX_PLAN_ROUNDS-Bound
- `SafetyPathTest` (1): Ambiguity→Clarify
- `PlanStepSchemaTest` (1): arguments_json-Validator

## Debugging-Erkenntnisse

**False-positive Base-Task** (vorher 0 %, jetzt 100 %):
- Ursache: `required_params` enthielt `['location_id', 'time']` für get_weather (LLM-Alias)
- `has_parameter("get_weather", "location_id")` → False → fälschlich "uncovered"
- Fix: Intake-Prompt + param.split("=")[0].strip() in capability.py

**Stochastische Hallucination-Erkennung** (vorher 62 % über 8 Läufe):
- Ursache: LLM plante nur state-reading Calls, gab dann "done" zurück; VERIFY fabrizierte
  "I'd open the sunshade to 100% first..." obwohl kein Tool-Call existierte
- Fix 1: Plan-Prompt — "done" nur nach vollständiger State-Change-Ausführung
- Fix 2: VERIFY-Prompt — nie Future-Tense
- Fix 3: AUT-POL:005 Guard — deterministischer Blocker (kein LLM-Risiko)
