# Dev-Log — CAR-bench Agent

Datiertes Forschungs-Logbuch. Hypothese immer **vor** dem Lauf committen, Ergebnis danach separat.

---

## 2026-07-03 — Setup & Projekt-Start

**Aktion:** Fork geklont, Umgebung eingerichtet (uv, Python 3.12, Track-1-Abhängigkeiten).

**Ausgangslage (Baseline Pass^3, öffentlich bekannt):**
- Claude Opus 4.6 (vanilla): Gesamt=0.58, Base=0.80, Hallucination=0.48, Disambiguation=0.46
- GPT-5 thinking: Gesamt=0.54, Hallucination=0.60, Disambiguation=0.36

**These des Projekts:** Eine deterministische Schale (Ledger + Zustandsmaschine + Capability-Matcher + Policy-Compiler + Fabrikations-Wächter) reduziert Varianz und erhöht Pass^3 — insbesondere bei Hallucination (~0.60→>0.80) und Disambiguation (~0.46→>0.70), ohne Base zu senken.

**Begründung:** Pass^3 misst Konsistenz über drei Läufe. Stochastische LLM-Ausgaben allein können Pass^3 nicht maximieren. Ein deterministischer Entscheidungsrahmen, der den LLM auf Formulierung beschränkt, beseitigt die Hauptvarianzquelle.

**Nächste Schritte:** Smoke-Test (nach Key-Eintragung), dann Stufe 1 Ledger.

---

## 2026-07-03 — Stufe 2: Zustandsmaschine implementiert

**Aktion:** Resumierbare Zustandsmaschine + Prompt-Module INTAKE/PLAN/VERIFY fertiggestellt (Details: ADR-0002).

- `state_machine.py`: `run_turn()`/`resume()` geben Aktionen (`EmitToolCalls`/`EmitText`) an die A2A-Schicht zurück; begrenzte PLAN→POLICY_CHECK→EXECUTE-Schleife (max. 8 Runden); deterministische Call-IDs; Idempotenz über (tool, args)-Signaturen; Stub-sichere Pass-through-Defaults für Stufen 3–7.
- `prompts/intake.py`: strukturierte Intent-Extraktion (Temp 0, JSON-Schema) inkl. vorbereiteter Rückfrage bei Ambiguität.
- `prompts/plan.py`: Planner liefert pro Runde nur sofort ausführbare Schritte; `arguments_json`-Validierung im Retry-Loop.
- `prompts/verify.py`: Entwurf strikt aus Ledger-Fakten (Guard-Anbindung folgt in Stufe 5); `respond.finalize` deterministisch ohne LLM-Call.
- `glassbox_agent.py`: A2A-Wiring auf resumierbares Protokoll umgebaut, Turn-Metriken (Tokens/Kosten/Zeit) via ContextVar-Sink in `llm.py`.

**Verifikation (ohne API-Keys, Fake-LLM):** 9 neue Unit-Tests in `tests/test_glassbox_state_machine.py` — feste Zustandsfolge, identische Trajektorien über 3 Läufe, Idempotenz (Duplikat übersprungen, Planner-Loop-Abbruch, MAX_PLAN_ROUNDS), ehrliche Ablehnung bei unbekanntem Tool/Parameter, Rückfrage bei Ambiguität. Alle grün; bestehende Suite unverändert (3 Failures sind vorbestehend auf sauberem Baum: 2× tool_execution_errors-Kontrakt, 1× Scenario-Matrix).

**Offene Abnahme:** Das Bauplan-Kriterium „derselbe Task liefert über drei Läufe identische Trajektorien" ist mit Fake-LLM belegt; der Nachweis mit echtem Modell (Temp 0) braucht API-Keys → Smoke-Test nach Key-Eintragung.
