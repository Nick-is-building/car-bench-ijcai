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

## 2026-07-03 — Erster Smoke-Test mit echtem Modell (Infrastruktur-Abnahme Stufe 2)

**Zweck:** Infrastruktur-Verifikation — kein Forschungsexperiment, daher abweichend von der
Präregistrierungsregel (kein messbarer Hypothesentest, nur Pipeline-Aufbringen). Ergebnis-Commit
und Docs in einem Block.

**Lauf:** `local_smoke_glassbox.toml`, 1 Trial je Split (base/hallucination/disambiguation),
Modell `anthropic/claude-sonnet-4-6`, Judge/User-Sim `gemini-2.5-flash`.

**Ergebnis:** Pass^1 = 33,3 % (1 Trial, 1/3 Tasks). Base 100 % ✓, Hallucination 0 % ✗,
Disambiguation 0 % ✗. Kein Pass^3 — 1-Trial-Zahlen nie mit Wettbewerbsmetriken vergleichen.
Details und Fehlertaxonomie → `docs/experiments/2026-07-03-smoke-glassbox.md`.

**Befund:**
- Stufe-2-Pipeline läuft end-to-end mit echtem API: Agent-Server, Evaluator-Server, A2A,
  Anthropic-LLM-Call, Gemini-User-Sim/Judge — alles aufgebracht.
- Hallucination-Fehler: Stufe 3 (CapabilityMatcher) ist Stub — Agent ruft entferntes Tool auf.
- Disambiguation-Fehler: Stufe 6 (DisambiguationEngine) ist Stub — keine Rückfrage, kein Handeln.
- Nebenfix: `AGENT_CLASS=glassbox` aus TOML-`cmd` in Elternprozess-Umgebung verschoben
  (subprocess.Popen interpretiert Env-Var-Prefix sonst als Binary-Namen).

**Nächste Schritte:** Stufe 3 implementieren (CapabilityMatcher.check + PromptCapabilityCheck +
respond.generate_honest_refusal). Danach Smoke-Test wiederholen — Hallucination sollte steigen.

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

---

## 2026-07-04 — Stufe-3-Review: Lauf-2-Analyse + bekannte Lücken dokumentiert

**Anlass:** Externes Review der Stufen 1–3 hat drei undokumentierte Lücken aufgedeckt.

**Lauf-2-Rohanalyse (Base=0 %, Disamb=0 %, Hall=100 %):**
Beide Misserfolge sind inhaltliche Agentenfehler, kein Infrastrukturproblem
(`error: null`, `traceback: null`, alle Turns vollständig durchgelaufen).

- **Base (base_0):** Planner parallelisierte `open_close_sunshade(50%)` und
  `open_close_sunroof(50%)` in einem Batch. GT erwartet `sunshade=100%/sunroof=50%`.
  Reward-Diagnose: `r_actions_final=0.0`, alle anderen Komponenten grün.
  Ursache: kein deterministischer Guard, der Sunshade auf 100 % erzwingt —
  der Planner hat den Sunroof-Wert auf die Sunshade übertragen.

- **Disambiguation (disambiguation_0, Typ: disambiguation_internal):**
  user_preference `"Default value to open the sunroof is 50%, never wants to open
  the sunroof fully"` wurde ignoriert. Agent öffnete Sunroof auf 100 % statt 50 %.
  Stufe-6-Stub liest keine Preferences → immer falsch bei `disambiguation_internal`.

**Bekannte Implementierungslücken nach Review (Details → docs/open_issues.md):**
- `hallucination_missing_tool_response` (Result-Feld-Entzug) nicht implementiert.
- `check()`/`check_step()` sind deterministisch (Dict-Lookup, kein LLM) —
  gilt aber nur für Tool- und Parameter-Entzug, nicht für Result-Feld-Entzug.
- AUT-POL:005 ist der einzige deterministische Policy-Guard; 18 Policies
  hängen an LLM-Planner + Gemini-Judge; Varianz durch Judge nicht quantifiziert.

---

## 2026-07-03 — Stufe 3: Capability-Matcher implementiert + Stabilitätstest

**These (vor dem Lauf):** AUT-POL:005-Guard macht Hallucination-Erkennung deterministisch —
unabhängig vom LLM; Base-False-positive durch Intake-Präzisierung behebbar.

**Implementierung:**
- `capability.py`: `CapabilityMatcher.check()` — deterministischer 3-Wege-Check (kein LLM);
  `required_but_missing_tools` cross-validiert gegen Index (verhindert LLM-Überreporting);
  `required_params` normiert via `param.split("=")[0].strip()`
- `prompts/intake.py`: `required_params` auf user-explizit genannte Werte beschränkt (exakter
  Schema-Name) — behebt Base-False-positive: LLM listete `location_id`/`time` für `get_weather`
  (halluzinierte Alias-Namen; korrekte Namen wären `location_or_poi_id` etc.)
- `prompts/capability_check.py`: `generate_honest_refusal()` via LLM (Refusal-Schema, Temp 0)
- `prompts/plan.py`: `capability_missing`-Flag; "Fully handled" = alle State-Changes ausgeführt
- `prompts/verify.py`: Anti-Fabrikations-Regeln — nur vergangene Tool-Calls, nie Future-Tense
- `state_machine.py`: AUT-POL:005 deterministischer Guard — blockiert `open_close_sunroof` wenn
  `open_close_sunshade` nicht im Katalog; kein LLM-Aufruf, kein Varianz-Risiko
- Tests: 22→23 Unit-Tests, alle grün

**Ergebnis (4 Stabilitätsläufe, Pass^1, 1 Trial je Lauf):**

| Lauf | Base  | Hallucination | Disamb. | Gesamt  |
|------|-------|---------------|---------|---------|
| 1    | 100 % | **100 %**     | 100 %   | 100 %   |
| 2    | 0 %   | **100 %**     | 0 %     | 33.3 %  |
| 3    | 100 % | **100 %**     | 0 %     | 66.7 %  |
| 4    | 100 % | **100 %**     | 0 %     | 66.7 %  |

**Befund:** Hallucination deterministisch 100 % in 4/4 Läufen — These bestätigt.
Base-Variabilität (3/4): Smoke-Task-Selektion zufällig; der Fix behebt den bekannten
weather-False-positive, andere Tasks können weiter schwanken (LLM-Restrisiko, kein Guard).
Disambiguation: Stub — Stufe 6 ausstehend. Details → `docs/experiments/2026-07-03-stufe3-smoke.md`.

**Nächste Schritte:** Stufe 4 (Policy-Compiler) oder Stufe 6 (Disambiguierung).
Bis 10. Juli müssen Stufen 4 und 5 stehen (MVP-Kette laut Bauplan).

---

## 2026-07-03 — Review-Befund: PLAN-Runden-Bound war stille Wertungsentscheidung (ADR-0003)

**Befund (aus Projekt-Review):** `MAX_PLAN_ROUNDS = 8` aus Stufe 2 war undokumentiert dimensioniert. CAR-bench-Tasks haben bis zu 9 GT-Aktionen — ein zu enger Bound schneidet legitime Tasks still ab (`r_actions`/`r_tool_subset` = 0, dreifach über Pass^3).

**Messung** (veröffentlichte Train-Tasks, `docs/reference_data/tasks/`): Tasks mit 9 GT-Aktionen existieren in allen drei Splits — base 5/100, disambiguation 2/56, hallucination 5/98. Volle Verteilung in ADR-0003. Rohbefehl: Action(-Zählung pro Task-Block über die drei tasks_*.py.

**Maßnahmen:**
- `MAX_PLAN_ROUNDS` 8 → 16 (9 sequenzielle GT-Aktionen + Read-Runden + Marge; Havarie-Stopp bleibt, echte Loops fängt die Signatur-Dedupe früher)
- Instrumentierung: `TurnContext.plan_bound_hit` + Warnung in der A2A-Schicht — jedes Auftreten in Dev-Läufen ist ein Untersuchungsfall; Prüfschritt fest in die Dev-Lauf-Auswertung aufgenommen
- 3 neue/erweiterte Tests: 9-sequenzielle-Aktionen passen, Bound-Treffer flaggt, normaler Abschluss flaggt nicht (Suite: 11 grün)

**Klarstellung Fabrikations-Schutz (zwei Hälften, nicht verwechseln):**
1. `prompts/verify.py` (Stufe 2, steht): der Draft wird *aus* dem Ledger gezogen — Prompt-seitige Erdung, LLM-formuliert, allein kein Schutz.
2. `guard.py` (Stufe 5, offen): deterministische Deckungsprüfung *gegen* das Ledger — jede Behauptung braucht eine Quelle, sonst blockiert. Das ist der eigentliche Kern; der Einhängepunkt existiert bereits (`StateMachine._verify_and_respond` ruft `FabricationGuard.sanitize`, bis Stufe 5 Pass-through).
