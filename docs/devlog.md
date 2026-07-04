# Dev-Log — CAR-bench Agent

Datiertes Forschungs-Logbuch. Hypothese immer **vor** dem Lauf committen, Ergebnis danach separat.

---

## 2026-07-04 — Auftrag A Phase 0+1: Infra-Fix, Seed, Mid-Turn-Check, Result-Feld-Entzug

**Hypothese vor dem Lauf (unit tests only, kein echtes Modell):**

- **A0.1 (OI-005):** `test_agent_scenario_directories_use_standard_matrix` schlägt fehl weil
  `local_smoke_glassbox.toml` nicht in der Exclusion-Liste steht. Fix: exclusion auf
  `{"a2a-scenario.toml", "local_smoke_glassbox.toml"}` erweitern. Upstream-Suite wird danach
  66/67 grün sein (die 2 echten Upstream-Failures bleiben).

- **A0.2 (Seed):** Task-Selektion im Smoke-Szenario ist faktisch bereits deterministisch
  (`shuffle=False, seed=10` hardcoded im Evaluator). Der Seed ist aber nicht aus der TOML
  konfigurierbar und daher nicht dokumentiert. Fix: `seed`-Param in `build_args_from_config`
  via `config.get("seed", 10)` exponieren, `local_smoke_glassbox.toml` bekommt `seed = 10`.

- **A1.1 (Mid-Conversation-Entziehung):** CapabilityIndex wird in `run_turn()` und `resume()`
  jeweils als lokale Variable neu gebaut (`CapabilityMatcher(ctx.tools)`). Zwischen User-Turns
  (neuer TurnContext) erhält `ctx.tools` automatisch den aktuellen Katalog. KEIN Freeze am
  StateMachine-Level. Innerhalb eines Turns (zwischen run_turn und resume) ist `ctx.tools`
  eingefroren — für die benchmark-seitig verwendeten Entzugstypen kein Problem
  (Tool/Param-Entzug passiert *vor* der User-Message). Erwartetes Testergebnis: Multi-Turn-Test
  grün, Turn 2 mit reduziertem Katalog → ehrliche Ablehnung.

- **A1.2 (Result-Feld-Entzug):** Tool-Schemas definieren KEIN responses/result-Schema
  (nur `parameters` = Input-Schema). `has_result_field()` kann daher NICHT auf Schema-Basis
  implementiert werden. Abdeckung erfolgt über Stufe-5-FabricationGuard (Auftrag C).
  OI-001 wird mit diesem Befund präzisiert. Fake-Test wird als @skip (OI-001) angelegt.

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

## 2026-07-04 — Auftrag A Phase 0+1: Ergebnis

**A0.1 (OI-005):** Exclusion-Set in `test_scenario_contract.py` Z.83 auf
`{"a2a-scenario.toml", "local_smoke_glassbox.toml"}` erweitert. OI-005 geschlossen.
`test_scenario_contract.py`: 11/11 grün (3 subtests).

**A0.2 (Seed):** `seed`-Parameter in `build_args_from_config` via
`config.get("seed", 10)` exponiert. `local_smoke_glassbox.toml` erhält `seed = 10`.
Task-Selektion war faktisch schon deterministisch (`shuffle=False`), ist jetzt auch im
TOML dokumentiert und überschreibbar.

**A1.1 (Mid-Conversation-Entziehung):**
- Befund: CapabilityIndex wird weder am StateMachine noch am CapabilityMatcher gecacht.
  `run_turn()` und `resume()` bauen jeweils lokal `CapabilityMatcher(ctx.tools)` — vollständig
  zustandslos. Zwischen User-Turns entsteht ein neuer `TurnContext` mit aktuellem Katalog.
  Innerhalb eines Turns kann der Aufrufer `ctx.tools` vor `resume()` aktualisieren,
  und die nächste Plan-Runde nutzt den neuen Index. Kein Code-Fix nötig — Architektur war korrekt.
- 2 neue Tests:
  - `test_capability_index_rebuilt_per_turn_not_cached_on_machine`: Turn-1 → Erfolg;
    Turn-2 (neue ctx, sunroof entfernt) → ehrliche Ablehnung. ✅
  - `test_resume_uses_ctx_tools_not_stale_first_turn_tools`: ctx.tools Update vor resume →
    Planner-Request auf entferntes Tool → Ablehnung in derselben Runde. ✅

**A1.2 (Result-Feld-Entzug):**
- Befund: Tool-Schemas enthalten NUR `parameters` (Input). Kein `responses`/`result`-Schema.
  `has_result_field()` nicht auf Schema-Basis implementierbar.
- OI-001 präzisiert: Abdeckung über Stufe-5-FabricationGuard (Auftrag C).
- 1 Stub-Test `ResultFieldEntzugTest` mit `@skip(OI-001)` angelegt — wird grün wenn Stufe 5 steht.

**Ergebnis:** 25 passed, 1 skipped (OI-001-Stub), 0 failed. Upstream-Suite: +1 Fix (OI-005).

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

---

## 2026-07-04 — Auftrag B: Policy-Compiler (Stufe 4) — Ergebnis

**B1 (Klassifikation):** Alle 19 Policies aus `wiki.md` wörtlich klassifiziert:
9× Klasse A (voll deterministisch: 005, 010, 011, 013, 014, 017, 019, 023, 024),
7× Klasse B (deterministischer Guard + semantischer Rest: 004, 007, 008, 009, 012,
016, 018), 3× Klasse C (inhärent semantisch: 002, 021, 022). Tabelle mit
Implementierungs-Status je Policy: `docs/decisions/0004-policy-compiler-regeltabelle.md`.

**B2 (Regel-Tabelle):** `policies.py` neu: EINE deklarative `RULES`-Liste mit 7
generischen Regeltypen (companion_available, value_bound, state_precondition,
prior_observation, state_companion, no_parallel, obligation_note).
`PolicyChecker.pre_flight()` iteriert generisch — Tool-Namen existieren NUR in den
Daten (Regel-Einträge, TOOL_EFFECTS, OBSERVATION_TOOLS), nie im Kontrollfluss.
Zustandsableitung ausschließlich aus dem Ledger (SUCCESS-Results); Null-FP-Disziplin:
unbekannter Zustand blockiert nie, höchstens Beobachtungs-Injektion mit
Schleifenschutz (max. 1 Observation pro Tool pro Turn).

**B3 (Generalisierungs-Beweis):** AUT-POL:005-Guard aus `state_machine.py` gelöscht,
ersetzt durch `CompanionAvailableRule`-Daten-Eintrag (+ neuer Wert-Aspekt: Sunshade
100 % via `inject_when_unknown`). Alle vorbestehenden Tests unverändert grün:
`test_glassbox_state_machine.py` 25 passed, 1 skipped (OI-001-Stub).
Zwei beim ersten Testlauf gefundene Bugs behoben: fehlendes `when`-Feld auf
ValueBound-/ObligationNote-Dataclasses; geblockte Calls werden jetzt aus `kept`
entfernt (kept = „besteht Pre-Flight").

**B4 (Klasse C markiert):** `SEMANTIC_POLICY_OBLIGATIONS`-Block (deutlich als
nicht-maschinell-geprüft markiert) in PLAN- und VERIFY-System-Prompts;
Pre-Flight-Notes werden als markierter Block in die User-Message beider Prompts
gereicht. Neuer LLM-Baustein `prompts/policy_check.py` (Policy-Block-Antwort);
`respond.generate_policy_block` delegiert dorthin.

**B5 (Tests pro Regeltyp):** `tests/test_glassbox_policies.py` neu, 28 Tests —
pro Regeltyp mindestens ein Verletzungsfall (Block/Refusal/Injektion/Defer) und
ein Nicht-Verletzungsfall (Durchlass unangetastet), plus Null-FP-Gesamttest
(harmloser Batch bleibt komplett unberührt, notes leer). Kein LLM, kein API-Key.

**Suite gesamt (Messart: pytest tests/, lokal, ein Lauf):** 95 passed, 1 skipped,
2 failed — beide Failures in `test_a2a_response_contract.py` auf sauberem HEAD
(3d13e1a) reproduziert, also vorbestehend und unabhängig von Auftrag B (→ OI-010).

**Open-Issues-Pflege:** OI-002 (weitgehend) und OI-003 geschlossen; neu:
OI-007 (Confirmation-Handshake 004/007/008), OI-008 (LLM-POL:012-Guard),
OI-009 (AUT-POL:016-Guard), OI-010 (vorbestehende a2a-Failures).

**Nächster Schritt:** B6-Abnahme-Lauf (5 Tasks × 3, Base-Split) — Hypothese-Eintrag
folgt separat VOR dem Lauf; Kostenschätzung geht zuerst an den User (Freigabe-Gate).

---

## 2026-07-04 — B6-Abnahme-Lauf Stufe 4: Hypothese (VOR dem Lauf)

**Setup:** 5 feste Base-Task-IDs × 3 Trials (= 15 Task-Läufe), Split train,
Agent anthropic/claude-sonnet-4-6, User-Sim + Policy-Judge gemini/gemini-2.5-flash,
seed 10, sequenziell. Szenario: `scenarios/track_1_agent_under_test/local_stufe4_abnahme.toml`.
Stand: Commit nach 9233489 (Stufe 4 komplett).

**Task-Auswahl (fest, regeltyp-getrieben — vor dem Lauf festgelegt):**
- `base_0` — sunroof/sunshade + weather: AUT-POL:005 (Verfügbarkeit + Wert), AUT-POL:009
- `base_10` — fog lights + low beams: AUT-POL:009, AUT-POL:013 (state_companion)
- `base_16` — defrost/fan/AC/window: AUT-POL:010, AUT-POL:011 (state_companion mehrfach)
- `base_20` — Kalender: AUT-POL:023 (value_bound); zugleich Null-FP-Kontrolle
  (fast reiner Read-Task — Pre-Flight darf hier nichts injizieren/blockieren)
- `base_56` — Navigation delete_waypoint: AUT-POL:017/018/019 (state_precondition, no_parallel)

**Hypothesen:**
1. **r_policy nirgends < 1.0** über alle 15 Task-Läufe — der deterministische
   Pre-Flight verhindert AUT-POL-Verletzungen strukturell (Injektion/Defer statt
   Verletzung; Null-FP-Disziplin verhindert falsche Blockaden).
2. **Base-Pass nicht schlechter** als vor Stufe 4 (Referenz: Stufe-3-Stabilitätstest,
   Base 3/4 Läufe Pass^1=100 %, anderer Task-Mix — nur grobe Richtgröße, kein 1:1-Vergleich).
3. `plan_bound_hit` feuert nie (injizierte Observations/Companions kosten Runden,
   Bound 16 hat Marge).
4. Kein Lauf endet in Refusal auf diesen 5 Tasks (alle benötigten Tools sind im
   Katalog vorhanden; missing_capability darf nicht triggern).

**Messart:** Pass^3 über 3 Trials pro Task + Pass^1 je Trial; r_policy aus den
Evaluator-Rewards. Kostenschätzung vorab: ~$1–2 Agent + <$0.10 Gemini (Basis:
Stufe-3-Smoke ~$0.06/Task-Lauf, Puffer ×2 für Pre-Flight-Overhead). User-Freigabe
liegt vor (2026-07-04).

**Lauf-Disziplin:** nohup > _local/runs/stufe4_abnahme.log, kein Live-Tail;
Auswertung einmalig nach Abschluss. Ergebnis-Eintrag folgt separat.

---

## 2026-07-04 — B6-Abnahme-Lauf Stufe 4: Ergebnis + Fehleranalyse

**Ergebnis (Messart: Pass^k über 3 Trials, n = 5 Base-Tasks × 3 = 15 Task-Läufe,
Agent claude-sonnet-4-6, User-Sim + Judge gemini-2.5-flash, seed 10):**

| Metrik | Wert |
|---|---|
| Pass^1 = Pass^2 = Pass^3 | **60.0 %** (base_0, base_16, base_20 je 3/3 ✓; base_10, base_56 je 0/3 ✗) |
| policy_aut_errors | **0 in 15/15** Läufen (deterministischer AUT-Teil hielt) |
| r_policy < 1.0 | **1/15** (base_10 T2: LLM-Judge, fehlende Confirmation, s. u.) |
| plan_bound_hit | 0 Treffer |
| Kosten / Dauer | $0.985 Agent / 660.8 s |

**Hypothesen-Status (ehrlich):**
1. r_policy nirgends < 1.0 — ❌ **widerlegt**: base_10 T2 r_policy=0.0. Ursache
   ist aber kein AUT-Fehler (policy_aut_errors=[] in allen 15), sondern der
   LLM-POL:008-Anteil von AUT-POL:009: Wetter „cloudy" ⇒ explizite
   User-Bestätigung vor set_fog_lights nötig; Agent setzte ohne Nachfrage.
   Das ist exakt die in ADR-0004 dokumentierte Klasse-B-Grenze → OI-007 bestätigt.
2. Base nicht schlechter — auf der überlappenden Menge (base_0, Stufe-3-Smoke)
   nicht schlechter (3/3). Gesamtbild wegen anderem Task-Mix nicht 1:1 vergleichbar.
3. plan_bound_hit nie — ✅ bestätigt (0 Treffer im Orchestrator-Log).
4. Kein Refusal — ❌ **widerlegt**: base_10 T0/T1 und base_56 T0/T1/T2 endeten
   mit falschem „nicht verfügbar"-Refusal (end_conversation_keyword=OUT_OF_SCOPE).

**Abnahme-Kriterium B6 (r_policy nie <1.0, Base nicht schlechter): NICHT bestanden.**

**Root-Cause-Analyse (deterministische Repro, kein Judge-Nachbau):**

1. **AUT-POL:019-False-Positive (base_56 T2) — Bug, behoben.**
   `_eval_state_precondition` prüfte das Prädikat auf dem projizierten Zustand
   INKLUSIVE des Effekts des Trigger-Calls selbst: `navigation_delete_waypoint`
   dekrementiert `nav_waypoint_count` 3→2, das 019-Prädikat (≥3) schlug fehl —
   der Delete hat sich selbst blockiert. Repro: echter Katalog + Ledger aus der
   T2-Trajektorie → `blocked=[AUT-POL:019]`. Fix: `projected_before(call)` —
   Preconditions werden auf dem Zustand VOR dem Call geprüft (Injections +
   vorangehende Batch-Calls). 2 Regressionstests (Pass mit Zwischenstopp;
   zweiter Delete im selben Batch weiterhin blockiert).
2. **Falsche Capability-Refusals (base_10 T0/T1; base_56 T0/T1 + T2-Ende) —
   LLM-Pfad, Guard nachgerüstet.** Pre-Flight per Repro entlastet
   (has_tool=True für alle GT-Tools mit echtem A2A-Katalog; Pre-Flight injiziert
   in der Repro korrekt get_weather + set_head_lights_low_beams). Per Ausschluss
   stammt das Refusal aus dem LLM-Pfad: Planner-`capability_missing`-Flag bzw.
   Intake-required_tools — beides wurde nie deterministisch gegen den Katalog
   verifiziert. Fix: Plan-Schema erhält `missing_tools` (exakte Namen); das Flag
   wird nur geehrt, wenn ein benanntes Tool wirklich nicht im Index ist; sonst
   PLAN-GUARD-Note + Re-Plan (max. 2, dann ehrliches VERIFY-Ende statt Refusal).
   Prompt verlangt Re-Scan der Schemas vor jedem Claim. 3 Tests (1 angepasst,
   2 neu). **Restrisiko** (nicht deterministisch schließbar): erfindet das LLM
   einen Tool-Namen, ist „Name nicht im Index" von „Capability fehlt wirklich"
   nicht unterscheidbar → OI-011.
3. **Fehlende Wetter-Confirmation (base_10 T2)** — bekanntes OI-007
   (Confirmation-Handshake über Turn-Grenzen), jetzt empirisch belegt. Kein
   Quick-Fix in dieser Runde; gehört zu Stufe-5/6-Arbeit.

**Lehren für die Lauf-Disziplin:** Agent-seitige Logs (state_trace, Refusal-Quelle)
wurden vom nohup-Orchestrator-Log nicht erfasst — die base_10/56-T0/T1-Zuordnung
Intake vs. Planner blieb deshalb Ausschluss-Diagnose. Vor dem nächsten Lauf
Agent-Server-Log in Datei umleiten (in OI-011 festgehalten).

**Konsequenz:** Beide deterministischen Fixes sind committet (Tests: 99 passed,
1 skipped, 2 vorbestehende a2a-Failures = OI-010). Wiederholung des
Abnahme-Laufs erst nach erneuter User-Freigabe (Kosten ~$1).
