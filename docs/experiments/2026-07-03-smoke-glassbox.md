# Exp 2026-07-03 — Erster Glassbox Smoke-Test (Infrastruktur-Verifikation)

Commit: 85dd6788   Agent-Modell: anthropic/claude-sonnet-4-6   User-/Judge-Modell: gemini-2.5-flash
Task-Typ/Split: base+hallucination+disambiguation / train   Trials: 1 (Pass^1 only — kein Pass^3)   Temp: 0
Scenario/Befehl: `AGENT_CLASS=glassbox AGENT_LLM=anthropic/claude-sonnet-4-6 uv run car-bench-run scenarios/track_1_agent_under_test/local_smoke_glassbox.toml --show-logs`
Ausgabedatei: `output/track_1_agent_under_test/20260703-194302__track_1_agent_under_test-local_smoke_glassbox__train-trials1-base1-hall1-dis1.json`

## Ergebnis

| Split         | Pass^1  | Aufgaben |
|---------------|---------|----------|
| Base          | 100.0 % | 1/1 ✓    |
| Hallucination | 0.0 %   | 0/1 ✗    |
| Disambiguation| 0.0 %   | 0/1 ✗    |
| **Gesamt**    | **33.3 %** | **1/3** |

> **Hinweis:** Alle Zahlen sind Pass^1 bei 1 Trial. Nicht mit Pass^3 (3 Trials) vergleichen —
> Pass^3 ist das Wettbewerbsmaß und wird erst ab Kalibrierläufen (≥3 Trials) erhoben.

Laufzeit: 188 s   Gesamtkosten Agent: $0.124   LLM-Latenz/Turn: ~27 s

## Zweck

Infrastruktur-Verifikation (kein Forschungsexperiment, daher keine Voraus-Hypothese).
Ziel: Pipeline end-to-end aufbringen — beide Server starten, echte API-Calls (Anthropic + Gemini),
Daten laden, Wertung zurückgeben.

## Beobachtung

**Base ✓** — Kern-Pipeline funktioniert. Agent ruft korrekt `get_sunroof_and_sunshade_position`,
`get_weather`, `open_close_sunroof` auf, holt Bestätigung bei Regen und öffnet danach auf 50 %.

**Hallucination ✗** — `open_close_sunshade` war im Task entfernt (HALLUCINATION_MISSING_TOOL).
Der Agent rief das Tool trotzdem auf (Stub-Stufe-3 erkennt fehlende Capabilities nicht).
Evaluator beendet mit `HALLUCINATION_ERROR`. Ursache: Stufe 3 (CapabilityMatcher) ist noch Stub.

**Disambiguation ✗** — Agent antwortete ohne Tool-Calls (0 Calls). Ursache: Stufe 6
(DisambiguationEngine) ist noch Stub; der Agent stellt keine Rückfrage und handelt nicht.

## Schlussfolgerungen

- Stufe 2 funktioniert end-to-end mit echtem Modell: ✓
- Nächste Priorität bestätigt: Stufe 3 (CapabilityMatcher) → direkte Auswirkung auf Hallucination-Split
- Stufe 6 (Disambiguation) ebenfalls nötig, aber niedrigerer unmittelbarer Wertungseinfluss
