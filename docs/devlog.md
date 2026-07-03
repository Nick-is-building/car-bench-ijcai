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
