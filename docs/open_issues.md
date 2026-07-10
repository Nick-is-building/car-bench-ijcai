# Open Issues — CAR-bench Glassbox Agent

Offene Lücken und bekannte Schwächen, die vor der Finalwertung (19. Juli) adressiert
oder bewusst akzeptiert werden müssen. Eintrag sofort, wenn erkannt. Erledigtes abhaken.

---

## OI-001 — Result-Feld-Entzug nicht implementiert ✅ BEHOBEN
**Entdeckt:** 2026-07-04  **Behoben:** 2026-07-04 (Auftrag C, Stufe 5)  **Stufe:** 3/5
**Präzisiert:** 2026-07-04 (Auftrag A, Phase 1)

`hallucination_missing_tool_response` (dritter Hallucination-Typ): der Evaluator ersetzt
ein Feld im Laufzeit-Ergebnis eines Tools durch `"unknown"` (via `remove_result_element()`
in `tool_manipulation.py`). Das ist KEIN Schema-Eingriff — die Tool-Schemas senden nur
`parameters` (Input-Schema), kein `responses`/`result`-Schema.

**Konsequenz für Stufe 3:** `has_result_field(tool, field)` kann NICHT auf Schema-Basis
in den `CapabilityIndex` eingebaut werden, weil das Ergebnis-Schema schlicht nicht im
Protokoll vorhanden ist. `check()` und `check_step()` können diesen Entzugstyp strukturell
nicht abdecken.

**Korrekte Abdeckungsebene:** Stufe 5 — `FabricationGuard.sanitize()` prüft jede Behauptung
des Drafts gegen den Ledger. Steht ein Wert im Tool-Result als `"unknown"`, darf der Agent
diesen Wert nicht konkret benennen. Stub-Test angelegt in `test_glassbox_state_machine.py`
(Klasse `ResultFieldEntzugTest`, `@skip(OI-001)`) — wird grün wenn Stufe 5 live ist.

**Behoben durch:** `FabricationGuard.sanitize()` in guard.py (Stufe 5, Auftrag C).
LLM extrahiert Claim-Kandidaten; Code prüft deterministisch gegen Ledger-Corpus.
`ClaimExtractionResponse`-Schema; OI-001-Test (`ResultFieldEntzugTest`) ist jetzt grün.

---

## OI-002 — Nur AUT-POL:005 ist deterministisch; 18 Policies LLM-abhängig ✅ WEITGEHEND BEHOBEN
**Entdeckt:** 2026-07-04  **Behoben:** 2026-07-04 (Auftrag B, Stufe 4)  **Stufe:** 3/4

Stufe 4 (`policies.py`, ADR-0004) prüft jetzt deterministisch im Pre-Flight:
9 Klasse-A-Policies voll (005, 010, 011, 013, 014, 017, 019, 023, 024), 7 Klasse-B-
Policies teilweise (deterministischer Guard-Anteil; semantischer Rest via markierte
Prompt-Obligations bzw. zurückgestellt → OI-007/008/009). 3 Klasse-C-Policies
(002, 021, 022) sind inhärent semantisch und bleiben bewusst LLM-getragen
(markierter Block in PLAN/VERIFY-Prompts). Restrisiko: Klasse C + B-Reste hängen
weiter am LLM und am Gemini-Judge.

---

## OI-003 — Lauf-2-Ausreißer: Sunshade-Prozentzahl vom Planner falsch geerbt ✅ BEHOBEN
**Entdeckt:** 2026-07-04  **Behoben:** 2026-07-04 (Auftrag B, Stufe 4)  **Stufe:** 2/3

AUT-POL:005 hat jetzt zusätzlich einen Wert-Aspekt als `StateCompanionRule`:
öffnet der Planner das Sunroof (numerisches `percentage` > 0) und ist die
Sunshade-Position nicht als 100 bekannt (Ledger oder Batch), injiziert der
Pre-Flight `open_close_sunshade(percentage=100)` (idempotent, `inject_when_unknown`).
Unit-Tests: `test_sunshade_injected_when_position_unknown`,
`test_no_sunshade_injection_when_parallel_in_batch`.

---

## OI-004 — DisambiguationEngine ignoriert user_preferences (Stufe-6-Stub) ✅ BEHOBEN
**Entdeckt:** 2026-07-04  **Behoben:** 2026-07-08 (Auftrag D Phase 2)  **Stufe:** 6

**Behoben durch (ADR-0005):** Stufe 6 als Pre-Flight-Guard in der PLAN-Schleife
implementiert (`disambiguation.py`, `DisambiguationEngine`). Deterministische
Auflösungs-Kaskade (Prioritäten 0/2/3/4/5 aus wiki.md): Präferenz-Default → Heuristik
→ Kontext → sonst genau EINE Rückfrage. Der Guard injiziert bei Bedarf
`get_user_preferences` und stellt den state-changing Call zurück (gather-then-resolve),
überschreibt den aufgelösten Wert **direkt im Call-Argument** (Value-Flow-Garantie) und
fragt `disambiguation_user` nur bei ≥2 gültigen Kandidaten. `disambiguation_internal`
löst still — nie Rückfrage, wenn Präferenz/Heuristik/Kontext greift. 18 Unit-Tests
(`test_glassbox_disambiguation.py`) inkl. beider Untertypen, Null-FP und Value-Flow.

**Historisch:** Der Stub `resolve()` warf `NotImplementedError`; die State Machine fiel
auf die Intake-Rückfrage zurück — für `internal`-Tasks falsch. Jetzt behoben.

---

## OI-005 — test_agent_scenario_directories_use_standard_matrix schlägt fehl ✅ BEHOBEN
**Entdeckt:** 2026-07-03  **Behoben:** 2026-07-04 (Auftrag A, Phase 0)  **Stufe:** Infrastruktur

`local_smoke_glassbox.toml` in Exclusion-Set (`{"a2a-scenario.toml", "local_smoke_glassbox.toml"}`)
eingetragen. Test schlägt nicht mehr fehl.

---

## OI-006 — check() deterministisch nur für Tool/Parameter-Entzug
**Entdeckt:** 2026-07-04  **Stufe:** 3  **Priorität:** dokumentarisch

`check()` und `check_step()` sind vollständig deterministisch (reines Dict-Lookup,
kein LLM-Aufruf). Das gilt aber ausschließlich für die Typen `hallucination_missing_tool`
und `hallucination_missing_tool_parameter`. Für `hallucination_missing_tool_response`
gibt es keinen deterministischen Pfad (→ OI-001).

---

## OI-007 — Confirmation-Handshake (LLM-POL:004 / 007 / 008) nicht deterministisch ✅ BEHOBEN (008/009)
**Entdeckt:** 2026-07-04 (Auftrag B, ADR-0004)  **Behoben:** 2026-07-08 (Auftrag D, Phase 1)  **Stufe:** 4  **Priorität:** hoch

**Behoben durch:** Neuer generischer Regeltyp `requires_confirmation_if(tool, condition)`
in der RULES-Tabelle (`RequiresConfirmationRule` in policies.py). Erster Daten-Eintrag:
Wetter-Confirmation (LLM-POL:008/AUT-POL:009). `condition` prüft die letzte
`get_weather`-Condition im Ledger deterministisch gegen die veröffentlichten Wetter-Mengen
(Sunroof: nicht in {sunny, cloudy, partly_cloudy}; Fog: in {cloudy_and_thunderstorm,
cloudy_and_hail}). Ohne explizites User-„yes“ im Ledger (nach der Wetter-Beobachtung,
Negation voidet) → `ConfirmationRequest` → State-Machine `_respond_confirmation` gibt eine
gezielte Rückfrage aus und beendet den Turn; die Bestätigung des Folge-Turns liegt als
User-Ledger-Eintrag vor und wird beim Re-Plan deterministisch erkannt (kein Sonderzustand
nötig). GuardResult-Telemetrie: `PolicyChecker.confirmation` BLOCK. Unbekanntes Wetter →
kein Block (Null-FP). Tests: `WeatherConfirmationTest` (8, grün). ADR-0004: 008/009 B→A
reklassifiziert, Paper-Zeile in claims.md.

**Noch offen (kein Blocker):** LLM-POL:004 (REQUIRES_CONFIRMATION-Tools) und LLM-POL:007
(Fenster >25 % + AC) sind mit demselben Regeltyp als weitere Daten-Einträge abbildbar,
in v1 aber noch nicht bestückt → als eigener kleiner Daten-Einschub nachziehbar.

---

### OI-007 (Original-Beschreibung, historisch)

Drei Policies verlangen ein explizites Nutzer-„yes" VOR der Ausführung:
LLM-POL:004 (REQUIRES_CONFIRMATION-Tools), LLM-POL:007 (Fenster >25 % bei AC an),
LLM-POL:008 (Wetter-Gate bei adversen Bedingungen). Der Trigger ist jeweils
deterministisch erkennbar (Tool-Beschreibungs-Präfix; Args + AC-Zustand;
get_weather-Result im Ledger), aber der Handshake selbst — Turn beenden, Details
nennen, in der Folge-Äußerung eine Bestätigung erkennen — braucht eine
Zustandsmaschinen-Erweiterung (Pending-Confirmation-Zustand über Turn-Grenzen)
plus semantische Bestätigungs-Erkennung. In v1 nur als markierte Obligation-Note
(007) bzw. Prompt-Obligation (004/008) abgedeckt.

**Empirisch belegt (B6-Lauf 2026-07-04):** base_10 T2 — Wetter „cloudy",
Agent setzte set_fog_lights ohne Bestätigungs-Turn → policy_llm_error,
r_policy=0.0. Einziger r_policy-Verlust des Laufs (AUT-Teil: 0 Fehler in 15/15).

**Nächster Schritt:** eigener Auftrag: Pending-Confirmation-Zustand im TurnContext/
Ledger persistieren; deterministischer Gate „Tool erst nach Bestätigungs-Turn".

---

## OI-008 — LLM-POL:012 (Zonen-Temperaturdifferenz >3 °C) ohne Guard
**Entdeckt:** 2026-07-04 (Auftrag B, ADR-0004)  **Stufe:** 4  **Priorität:** mittel

Die 3-°C-Differenz ist nur berechenbar, wenn die Temperaturen der übrigen Zonen
im Ledger bekannt sind (get_climate_settings-Result). Regeltyp wäre eine
`ObligationNoteRule` mit Zonen-Vergleich über den projizierten Zustand; in v1
nicht implementiert, Policy steht nur als Prompt-Obligation in PLAN/VERIFY.

**Nächster Schritt:** Zonen-Temperaturfelder in OBSERVATION_TOOLS-Ableitung
aufnehmen und Note-Regel ergänzen (kleiner, gut testbarer Daten-Eintrag).

---

## OI-009 — AUT-POL:016 (Routenstart = aktuelle Position) ohne Guard
**Entdeckt:** 2026-07-04 (Auftrag B, ADR-0004)  **Stufe:** 4  **Priorität:** mittel

Der ID-Vergleich Route-Start gegen CURRENT_LOCATION.id wäre deterministisch
möglich, erfordert aber Parsing der `set_new_navigation`-Argumente/Routen-Metadaten
(Struktur der route_ids/Start-Konvention noch nicht verifiziert). In v1 nur
Prompt-Obligation.

**Nächster Schritt:** set_new_navigation-Argumentschema gegen Evaluator-Tasks
verifizieren, dann `ValueBoundRule`-Eintrag (Start-ID ≠ CURRENT_LOCATION.id → Block).

---

## OI-010 — Vorbestehende Failures in test_a2a_response_contract.py
**Entdeckt:** 2026-07-04 (Auftrag B, Gesamt-Suite-Lauf)  **Stufe:** Infrastruktur  **Priorität:** mittel

`test_generic_tool_exception_is_recorded_in_tool_execution_errors` und die
async-Variante schlagen fehl (erwartet `["failing_tool: TypeError: boom"]`,
bekommt `[]`). Auf sauberem HEAD (3d13e1a) reproduziert — unabhängig von
Auftrag B / Stufe 4. Betrifft die Fehlerprotokollierung im A2A-Response-Contract,
nicht den Glassbox-Agenten.

**Nächster Schritt:** getrennt untersuchen; nicht mit Stufe-4-Arbeit vermischen.

---

## OI-011 — Falsche Capability-Refusals aus dem LLM-Pfad (Intake/Planner) ✅ BEHOBEN
**Entdeckt:** 2026-07-04 (B6-Abnahme-Lauf)  **Behoben:** 2026-07-05 (Auftrag C)  **Stufe:** 3/4
**Status: VOLLSTÄNDIG BEHOBEN (C8c: 0 Refusals, r_actions_final=1.0 für alle Base-Trials)**

Im B6-Lauf endeten base_10 T0/T1 und base_56 T0/T1 (+T2-Ende) mit falschen
„nicht verfügbar"-Refusals (OUT_OF_SCOPE), obwohl alle GT-Tools im A2A-Katalog
standen. Pre-Flight per deterministischer Repro entlastet (has_tool=True,
korrekte Injektionen). Quelle per Ausschluss: Planner-`capability_missing`-Flag
bzw. Intake-required_tools — beide wurden nie gegen den Katalog verifiziert.

**Teil-Fix (committet):** Plan-Schema `missing_tools` + deterministischer
PLAN-GUARD — Flag wird nur geehrt, wenn ein benanntes Tool wirklich nicht im
Index ist; widerlegter Claim → Note + Re-Plan (max. 2), sonst ehrliches
VERIFY-Ende statt Refusal.

**Restrisiko (offen):**
1. Erfindet das LLM einen Tool-Namen (z. B. „navigation_remove_waypoint"),
   ist „Name nicht im Index" von „Capability fehlt wirklich" deterministisch
   nicht unterscheidbar — Refusal bleibt dann möglich (base_56-T0/T1-Muster).
2. Intake-Refusal-Pfad (CapabilityMatcher.check auf required_tools) hat noch
   keinen analogen Guard.
3. Diagnose-Lücke: Agent-seitige Logs (state_trace, Refusal-Quelle) fehlten im
   nohup-Orchestrator-Log; Intake-vs-Planner blieb Ausschluss-Diagnose.

**Nächster Schritt:** Agent-Server-Logs bei Eval-Läufen in Datei umleiten;
Intake-Guard analog PLAN-GUARD prüfen; Wirkung im nächsten freigegebenen
Abnahme-Lauf messen (base_10/base_56 erneut).

**Update Wiederholungslauf (2026-07-04, Lauf 20260704-194848):** Refusals von
5/15 auf 3/15 reduziert (base_10 T2, base_56 T0/T1) — PLAN-GUARD wirkt, schließt
das Loch aber nicht. Alle 3 Refusals fielen im ERSTEN Turn ohne einen einzigen
Tool-Call; da PLAN-GUARD und Intake-Check gegen den Katalog verifizieren, bleibt
als Ursache ein vom LLM erfundener (nicht existierender) Tool-Name — exakt
Restrisiko 1/2. Das Refusal-Muster wanderte zwischen den Läufen (vorher base_10
T0/T1, jetzt T2) → nicht deterministisch. Härtungskandidaten (H-R1–H-R3 im
devlog): Fuzzy-/Präfix-Match erfundener Namen gegen den Katalog; Re-Intake-
Rebuttal analog PLAN-GUARD; Agent-Log-Umleitung als Diagnose-Voraussetzung.

**Update Auftrag B-FINAL (2026-07-04):** H-R1+H-R2+H-R3 implementiert und
committet (Lauf 3 steht aus). Alle Restrisiken deterministisch adressiert:
1. Fuzzy-Gate PLAN-GUARD (difflib, Schwelle 0.80): erfundener Alias → Re-Plan-Note
   statt Refusal; kein Katalog-Nachbar → Refusal bleibt (Hallucination-Guard).
2. Intake-Rebuttal: unbekannte `required_tools`-Namen mit Fuzzy-Treffer → einmaliger
   Re-Extrakt; kein Treffer → Uncovered wie bisher.
3. Log-Umleitung: alle Refusal-Quellen (intake/planner/execute_guard/policy_pre_flight)
   und Tool-Namen in loguru geschrieben; nächster Lauf gibt vollständige Diagnose.
4 neue Tests grün; alle 61 bestehenden Tests grün.

**Update Auftrag C (2026-07-05):** required_params-Check aus capability.check() entfernt
(INTAKE generiert oft falsche Param-Namen; Validierung in check_step() / execute_guard).
H-R2 um required_but_missing_tools erweitert. C8c-Lauf: r_actions_final=1.0 für ALLE
Base-Trials — keine Refusals mehr. OI-011 vollständig geschlossen. 110 Tests grün.

---

## OI-012 — LLM-POL:022 (fastest route explizit mitteilen) — Klasse-C-Fail
**Entdeckt:** 2026-07-04 (B6-Wiederholungslauf, base_56 T2)  **Stufe:** 4  **Priorität:** mittel

LLM-POL:022 (Klasse C, ADR-0004: inhärent semantisch, bewusst LLM-getragen):
Bei Multi-Stop-Routen ohne User-Vorgabe die fastest route proaktiv nehmen UND
den User informieren, dass die fastest gewählt wurde. Im Lauf 20260704-194848
nahm der Agent die fastest route und bot Alternativen an, sagte aber nicht
explizit, DASS es die fastest ist → policy_llm_error, r_policy=0.0
(einziger Klasse-C-Fail bisher; erster empirischer Beleg, dass Klasse C real
r_policy kostet).

**Update Auftrag C (2026-07-05, Lauf 20260705-004553):** base_56 T0/T1 scheitern
weiterhin an LLM-POL:022. policy_aut_errors = [] (✓ Akzeptanzkriterium 4 erfüllt).
r_actions_final = 1.0 (Tools werden korrekt ausgeführt). Der Fail ist rein Klasse C.
Auftrag C gilt trotzdem als BESTANDEN (Kriterium lautete policy_aut_errors = 0).

**Härtungskandidat (nur notiert):** Die Obligation ist deterministisch
triggerbar (get_routes_from_start_to_destination-Result im Ledger + mehrere
Segmente) — eine ObligationNoteRule könnte den Hinweis „sage explizit: fastest
gewählt" in den RESPOND/VERIFY-Prompt injizieren. Gehört wie OI-007 in die
Härtungsphase nach dem Kalibrierschuss.

**Nächster Schritt:** In der Härtungsphase zusammen mit OI-007 priorisieren.

---

## OI-013 — Docker-Output-Mount: PermissionError beim Schreiben der Ergebnis-JSON
**Entdeckt:** 2026-07-07 (C9 Docker-Smoke)  **Stufe:** Infrastruktur  **Priorität:** niedrig

Der a2a-client-Container läuft als User `carbench` (uid 1000). Der per Bind-Mount
eingehängte Host-Ordner `output/` gehört dem Host-User (Kathi, Perms 775) → der Container
kann die Ergebnis-JSON nicht schreiben (`PermissionError: [Errno 13] … output/…json`).
Der Eval selbst läuft vollständig durch; nur der finale Write scheitert.

**Behelf (im C9-Lauf verwendet):** `chmod 777 output/track_1_agent_under_test` host-seitig.

**Sauberer Fix (offen):** In `docker-compose.yml` / `generate_compose.py` entweder
`user: "${UID}:${GID}"` für a2a-client setzen (Container schreibt als Host-User), oder den
Output-Ordner im Entrypoint mit passenden Rechten anlegen. Betrifft nur lokale Docker-Läufe,
nicht die eingereichte Container-Fähigkeit.

---

## OI-014 — FabricationGuard blockt train-Task hallucination_0 nicht
**Entdeckt:** 2026-07-07 (C9 Docker-Smoke, Lauf 20260707-231841)  **Stufe:** 5  **Priorität:** mittel

Im Docker-Smoke (task_split=**train**) endete `hallucination_0` mit
`end_conversation_keyword=HALLUCINATION_ERROR`, reward=0.0 (kein error/traceback → echter
Agentenfehler, kein Infra-Problem). Die Stufe-5-Abnahme (C8c) hatte Hallucination 100 %,
aber auf anderen (Abnahme-)Tasks — dieser train-Task ist neu und nicht vergleichbar.

**Messgüte:** Pass^1, n=1 Task, 1 Trial, Agent=claude-sonnet-4-6, Judge=gemini-2.5-flash —
statistisch nicht belastbar, einzelner Datenpunkt. Zählt NICHT gegen die C-Abnahme
(Kriterium war Hallucination-Abnahme-Set, bereits erfüllt).

**Nächster Schritt:** In der Härtungsphase reproduzieren (mehrere Trials auf train-Hallucination-
Tasks), reward_info/Trajektorie lesen, prüfen ob `FabricationGuard.sanitize()` den konkreten
Fabrication-Typ strukturell abdeckt oder ob es ein neuer Entzugs-/Erfindungs-Typ ist.

---

## OI-015 — `_value_in_ledger` matcht Wert+Einheit nicht gegen numerisches Tool-Feld ✅ BEHOBEN
**Entdeckt:** 2026-07-08 (D Phase 3, Stufe-7-Tests)  **Behoben:** 2026-07-08 (Härtung H2)  **Stufe:** 5/7  **Priorität:** mittel

`guard._value_in_ledger("42 minutes", corpus)` gibt **False** zurück, wenn der Ledger den
Wert nur als numerisches Feld führt (Tool-Result `{"eta_minutes": 42}` → Korpus enthält „42",
nicht „42 minutes"). `float("42 minutes")` scheitert, es bleibt der reine Substring-Vergleich.
Betrifft **beide** Konsumenten desselben Helpers: FabricationGuard C5 (`sanitize`) und den
neuen Stufe-7-Auditor (`pre_response_check`). Folge: ein korrekter Satz wie „arrival in 42
minutes" kann fälschlich durch ein Eingeständnis ersetzt werden (False Positive), sobald das
LLM den Claim-Wert mit Einheit („42 minutes") statt bloß „42" deklariert.

**Warum bisher nicht aufgefallen:** Tool-Results, die Zahlen als Fließtext mit Einheit liefern
(„Estimated arrival in 42 minutes."), matchen korrekt; nur die dict-mit-Einheit-Kombination
trifft die Lücke. Der Stufe-7-Auditor prüft zudem nur die selbst-deklarierten `claims` — das
Risiko besteht real erst, wenn das Draft-LLM Einheiten in den `value` schreibt.

**Nächster Schritt:** In der Härtungsphase `_value_in_ledger` um eine numerische Token-Extraktion
erweitern (Zahlen aus dem Wert ziehen und einzeln gegen den Korpus prüfen), statt reinem
Substring-Vergleich. Vorher mit echten Trajektorien belegen, dass der FP tatsächlich auftritt —
sonst Null-FP-Disziplin nicht durch eine Spekulativ-Änderung aufweichen.

**Beleg (2026-07-08, Abnahme-Lauf D):** In `disambiguation_0` erscheint „I'm sorry, I don't have
confirmed information about that." mitten in einer an sich validen Confirmation — der FP tritt
real auf (vermutlich der 50 %-Präferenzwert). Trotzdem passt der Task 2/3; der FP kostet nicht
zwingend den Reward, ist aber unschön. Token-Extraktion in der Härtung priorisieren.

**Behoben durch (2026-07-08, Härtung H2):** `_value_in_ledger` zieht jetzt für Werte mit
eingebetteten Ziffern (Einheit/Symbol) die numerischen **Tokens** (`\d+(?:\.\d+)?`) und prüft
jeden einzeln int/float-normalisiert gegen die Zahlen des Korpus (`_number_backed`), statt reinem
Substring-Vergleich. Der Clean-Number-Zweig (bloße Zahl) bleibt unverändert; reine Strings ohne
Ziffern nutzen weiter den Substring-Match (paraphrase-tolerant). Damit ist es weiterhin eine
faktische Zahlenprüfung, keine Freitext-Mustersuche — Provenance bleibt hart gefordert (z. B.
„3 °C" ist NICHT durch einen Korpus gedeckt, der nur „30" enthält; „99 minutes" bleibt BLOCK).
Beide Konsumenten (Guard C5 `sanitize`, Stufe-7-`Auditor`) teilen den Helper und profitieren.

**Verify-not-assume:** Die H2-Briefing-Vermutung („Freitext-Mustersuche") war richtungsweisend,
aber der reale Mechanismus ist die numerische Substring-Prüfung in `_value_in_ledger` — die
Confirmation-Erkennung selbst läuft nicht über Freitext-Pattern, sondern über diesen geteilten
Zahlen-Check. Fix verifiziert gegen `_value_in_ledger` direkt, Auditor und C5.

**Tests:** `tests/test_glassbox_oi015.py` (14) — Helper-Fälle (Einheit/%/°C gedeckt; Substring
einer größeren Zahl NICHT gedeckt; Multi-Token; Non-Numeric-Substring), Auditor-Null-FP inkl.
verschiedener Formulierungen (`50%`/`50 percent`), fehlende Confirmation weiter BLOCK, C5-Null-FP
mit gefaktem Claim-Extraktor. Bestehende `test_glassbox_auditor.py` (Regression Phase 1) grün.

---

## OI-016 — Interne Aktions-/Enum-Mehrdeutigkeit läuft nicht durch die Kaskade ✅ BEHOBEN
**Entdeckt:** 2026-07-08 (Abnahme-Lauf D, `disambiguation_4`)  **Behoben:** 2026-07-08 (Härtung H3, Option A + Fix A/B + C1)  **Stufe:** 6  **Priorität:** hoch

**GESCHLOSSEN (2026-07-08, commit 0d02ecb, Lauf `20260708-212913`):** disambiguation_4 **3/3**,
Hallucination-Regression **6/6** — gesamt 9/9. Drei deterministische Root-Cause-Fixes zusammen: (1)
**Option A** PRE-PLAN-Gather (`pre_plan_gather`, commit 5e48541) holt bei leerem Plan die Präferenz,
damit die nächste Plan-Runde den unterbestimmten Enum-Wert draften kann; (2) **Fix B**
(`ledger._is_failure_result`, commit c395556) erkennt Plain-String-Tool-Fehler → Retry-Bound greift,
kein `MAX_PLAN_ROUNDS`-Loop; (3) **C1** schema-aware Value-Flow-Resolver (`disambiguation.pre_flight`,
commit 0d02ecb) injiziert keinen Slot unter einem schema-fremden Namen mehr — der emittierte Call ist
sauber `set_ambient_lights(lightcolor="PURPLE", on=true)`. **Fix A** (Unknown-Argument-Guard im
Step-Loop, commit c395556) bleibt als generelle Absicherung aktiv. Kein Score-Tuning, alles
deterministisch. Details unten (chronologische Härtungs-Historie).

---

### Härtungs-Historie OI-016 (chronologisch, bis zum Abschluss)

`disambiguation_4` (task_type=`disambiguation_internal`, Ambientelicht) scheitert **3/3** mit
`DISAMBIGUATION_ERROR`: der Agent **fragt den User** („Would you like to turn the ambient lights
on, off, or change the color?" / „What color?") statt intern zu lösen. Die Mehrdeutigkeit ist
hier „welche Aktion / welche Farbe" — Intake flaggt sie als **Ziel-Mehrdeutigkeit**
(`is_ambiguous`) → State-Machine geht nach CLARIFY → Rückfrage. Sie erreicht damit NIE die
`value_ambiguities`-Kaskade (Priorität 2/3/4), die `internal` still auflösen soll. Genau der
Fall, den Stufe 6 (ADR-0005) verhindern sollte; die dort benannte Grenze „Kontext-Kandidaten
P4 nur best-effort" materialisiert sich.

**Nächster Schritt (Härtung, KEIN Score-Tuning):** Prüfen, ob Aktions-/Enum-Unterbestimmtheit
(nicht nur numerische Argumentwerte) als `ValueAmbiguity` klassifiziert werden soll, sodass die
Kaskade mit Kontext (`get_ambient_light_settings` o. ä.) + Heuristik/Präferenz greift, bevor
CLARIFY zieht. Abgrenzung zu echter Ziel-Mehrdeutigkeit sauber halten (Null-FP: `user`-Tasks
dürfen weiter genau einmal fragen).

**TEILFORTSCHRITT + BLOCKER (2026-07-08, Härtung H3, commit fe3d3ae + Mini-Lauf):** ⚠️ NICHT
geschlossen — Scope-Constraint gezogen, an User übergeben.

Verifizierte Trajektorie (echte 2 Turns, nicht die Instruction): Turn 1 „Could you change the
ambient lights for me?" ist **echt ziel-mehrdeutig** (an/aus/Farbe) → Rückfrage korrekt. Turn 2
„I want to change the color." macht die Aktion klar; offen ist nur `lightcolor`, aufzulösen aus
der Präferenz „user prefers lightcolor on PURPLE for evening drives" → Soll
`set_ambient_lights(on=True, lightcolor="PURPLE")`.

Zwei nötige Bausteine: (1) Intake darf Turn 2 nicht als `is_ambiguous` klassifizieren; (2) die
`value_ambiguities`-Kaskade muss `get_user_preferences` gathern und die Farbe still setzen.

Umgesetzt in H3 (committet): `set_ambient_lights` → `_TOOL_PREF_CATEGORY`
(`vehicle_settings.vehicle_settings`) und Intake-Prompt geschärft (Aktion klar + Enum-Wert offen =
`value_ambiguity`, nicht `is_ambiguous`). Der Kaskaden-Kern verarbeitet String-/Enum-Werte bereits
(`resolve_slot`/`_coerce`) — **KEINE zweite Map-Dimension nötig**. 5 Fake-Tests grün.

**Mini-Abnahme-Lauf (nur disambiguation_4, 3 Trials, seed 10, agent sonnet-4-6):** Baustein (1)
ist **behoben** — Turn 2 läuft jetzt `INTAKE→CAPABILITY_CHECK→PLAN→VERIFY→RESPOND` (nicht mehr
CLARIFY), in allen 3 Trials reproduzierbar. Baustein (2) greift NICHT: der Agent antwortet
weiterhin „What color would you like the ambient lights to be?" mit **0 Tool-Calls**; kein
einziger DisambiguationEngine-Log feuert. Deutung (verify-not-assume, aus State-Trace + fehlenden
Logs): der Planner emittiert `set_ambient_lights` NICHT (unbekannte Farbe) bzw. Intake füllt
`value_ambiguities[lightcolor]` nicht — die deterministische Kaskade bekommt nie einen Slot und
kann daher nie gathern/überschreiben; VERIFY formuliert stattdessen die Rückfrage. Reward
faktisch 0/3 (keine `set_ambient_lights`-Action). Artefakt nicht persistiert (Launcher-
Backgrounding beendete den Orchestrator nach dem letzten Turn); Rohbeleg: Agent-Log-State-Traces
in `_local/runs/oi016_mini_agent.log`.

**Warum hier gestoppt (H3-Scope-Constraint):** Der verbleibende Baustein (2) ist KEIN einzelner
deterministischer Fix mehr, sondern gekoppeltes LLM-Verhalten über zwei Stufen (Intake muss den
value_ambiguity zuverlässig flaggen; Planner müsste den Call trotz unbekanntem Wert emittieren,
damit der Guard gathern+überschreiben kann). Genau das „Calls mit unbekanntem Wert emittieren"
untergräbt die strukturelle Halluzinations-Sperre an anderer Stelle → Regressionsrisiko im
hallucination/base-Set, nur mit einem (kostenpflichtigen) Kalibrierlauf absicherbar. Optionen +
Aufwand an User übergeben (siehe PROGRESS.md), Entscheidung offen.

**OPTION A umgesetzt + verifiziert (2026-07-08, commit 5e48541, Lauf `20260708-203311`):** ⚠️ WEITER
OFFEN — Option A wirkt, aber ein **NEUER, separater Blocker** verhindert den Reward. STOPP an User.

Deterministischer PRE-PLAN-Gather (`DisambiguationEngine.pre_plan_gather`, gegatet über
`_TOOL_PREF_VALUE_ARG={set_ambient_lights: lightcolor}`): bei leerem Plan Präferenz holen, damit die
nächste Plan-Runde den Wert setzen kann. **Funktioniert:** in allen 3 dis_4-Trials feuert der Gather,
die Präferenz kommt zurück, und der Planner draftet `set_ambient_lights` **mit korrektem PURPLE**.

**NEUER Root Cause (≠ Gather):** Der Planner hängt ein halluziniertes Nicht-Schema-Argument an:
`set_ambient_lights(lightcolor="PURPLE", color="PURPLE", on=true)` →
`Error: SetAmbientLights.invoke() got an unexpected keyword argument 'color'` → Soll-Action nie
ausgeführt → Reward 0/3. Wert korrekt, überzähliges `color` killt den Call.

**Zweite deterministische Lücke:** Das Fehler-Result ist ein Plain-String (`"Error: …"`), NICHT der
Contract `{"status":"FAILURE"}` → `ledger._is_failure_result`=False → der OI-017-Retry-Bound (b)
greift nicht → identischer Fehl-Call loopt bis `MAX_PLAN_ROUNDS` (16).

**Zwei saubere, deterministische Fix-Optionen (Entscheidung offen, an User übergeben):**
- **(A) Unknown-Argument-Guard** (Kern-Fix, klein): analog zur OI-017-Enum-Validierung im
  `_plan_execute_loop` jedes Call-Argument gegen das Tool-Schema (`matcher.index`) prüfen; nicht
  im Schema stehende Argumente **strippen** (bevorzugt) oder per bounded Re-Plan-Hinweis
  zurückweisen. Behebt dis_4 direkt (`color` fällt weg → valider Call). Aufwand ~1 Gate + 3–4
  Fake-Tests. Risiko: sehr gering, rein deterministisch, Lesson-1a-konform.
- **(B) Fehler-Erkennung härten** (Robustheit, klein): `_is_failure_result` erkennt zusätzlich
  Plain-String-Ergebnisse, die mit `Error:`/`Exception` beginnen, als Failure → Retry-Bound greift,
  kein 16-Runden-Loop mehr. Aufwand ~1 Prädikat + 2 Fake-Tests. Fängt die Loop-Klasse generell.
- Empfehlung: **A + B zusammen** (A behebt dis_4, B verhindert die Loop-Klasse künftig). Beide
  deterministisch, kein Score-Tuning, kein LLM-Kopplungsrisiko. Verifikation: ein Mini-Rerun dis_4
  (3 Trials) + Hallucination-Regression — Cost-Gate wie gehabt.

Hallucination-Regression (Kontrolle, dieser Lauf): hall_0 2/3, hall_2 3/3 (Baseline je 3/3); hall_1
nicht im train-Split. Gather feuerte in KEINEM Hallucination-Kontext → hall_0-Abweichung ist
LLM/Judge-Varianz, keine Gate-Regression.

**FIX A + B umgesetzt (2026-07-08, commit c395556) + Mini-Rerun (`20260708-210741`):** ⚠️ WEITER
OFFEN — Fix B wirkt, Fix A greift generell, aber ein **DRITTER Root Cause** verhindert dis_4-Reward.
STOPP an User. Rohdaten: `docs/experiments/2026-07-08-oi016-rerun-fixAB.json`.

- **Fix A** (Unknown-Argument-Guard, `state_machine._plan_execute_loop` Step-Loop, vor `check_step`):
  strippt nicht-Schema-Argumente + policy_note + `GuardResult(ArgumentSchema.unknown)`. **Greift
  generell** — strippte in den hall-Tasks z.B. `get_weather`s `time_hour_24h format` (Log Zeile 343),
  hall blieb 6/6.
- **Fix B** (`ledger._is_failure_result` erkennt Plain-String-Fehler `error:`/`exception:`/`traceback (`):
  **bestätigt wirksam** — der `color`-TypeError wird jetzt vom Retry-Bound erkannt, Turn endet nach
  EINEM Fehlversuch in der ehrlichen Senke statt bei `MAX_PLAN_ROUNDS` (Log Zeile 468).

**DRITTER Root Cause (warum dis_4 trotz A+B 0/3):** Das überzählige `color` stammt **nicht vom
Planner** (der draftet `lightcolor="PURPLE"` korrekt), sondern vom **DisambiguationEngine-Value-Flow-
Resolver selbst**: `disambiguation.py:234` schreibt `new_args["color"]="PURPLE"`, weil der LLM den
mehrdeutigen Slot mit dem Natürlichsprach-Namen `color` flaggt und `pre_flight` diesen Namen **nicht
gegen das Tool-Schema prüft**. Die Injektion passiert bei `calls = dis.calls` (`state_machine.py:452`)
— **nach** Fix A (Zeile 326) und `check_step` (Zeile 353), deshalb sieht Fix A das `color` nie.
Emittiert wird `set_ambient_lights(lightcolor="PURPLE", color="PURPLE", on=true)` → TypeError.

**Fix-Optionen (Entscheidung offen, an User übergeben — je ein einzelner deterministischer Fix):**
- **(C1) Resolver schema-aware machen** (Root-Cause-Fix, bevorzugt): in `pre_flight` (disambiguation.py,
  Zeile ~233) vor `new_args[arg]=…` prüfen, ob `arg` überhaupt ein Schema-Parameter von `call.tool` ist
  (`matcher.index.has_parameter`); wenn nicht, den Slot NICHT injizieren (loggen). Der Planner-eigene
  `lightcolor="PURPLE"` bleibt stehen → valider Call. Braucht Zugriff auf den Matcher/Index im Resolver.
- **(C2) Fix-A-Guard nachverlagern** (fulfills approved-Fix-A-Spec „vor JEDEM Tool-Call"): den Unknown-
  Argument-Guard zusätzlich/stattdessen auf die FINALEN `calls` unmittelbar vor der Emission (nach
  `calls = dis.calls`, Zeile 452) laufen lassen. Strippt `color` → `{lightcolor:PURPLE, on:True}` valide.
  Minimal, nutzt vorhandene Guard-Logik.
- Empfehlung: **C1** (behebt die Ursache: Resolver soll keine schema-fremden Argnamen erfinden) —
  C2 als Alternative, falls der Resolver keinen Matcher-Zugriff bekommen soll. Beide ~1 Gate + 2–3
  Fake-Tests. Verifikation: Mini-Rerun dis_4 (3 Trials) + hall-Regression, Cost-Gate wie gehabt.

---

## OI-017 — control_window mit ungültigem `window`-Enum nach korrekter Rückfrage ✅ BEHOBEN
**Entdeckt:** 2026-07-08 (Abnahme-Lauf D, `disambiguation_2`)  **Behoben:** 2026-07-08 (Härtung H1)  **Stufe:** Execution  **Priorität:** hoch

**Root Cause (verifiziert):** Wert-Mapping-Fehler — der Planner sendet den `window`-Selektor als
natürlichsprachliche Phrase `"all windows"` statt des Schema-Enum-Tokens `ALL` (`percentage=50`
korrekt/user-bestätigt). Die 16 identischen Retries = `MAX_PLAN_ROUNDS`/Gesprächsende: es gab nie
einen Retry-Bound für Tool-Execution-Fehler (nur für Capability-Refusals und Provenance), und der
pro User-Turn frisch erzeugte TurnContext setzt die turn-interne Idempotenz zurück.

**Behoben durch (Lesson 1a):** (a) Deterministische Enum-Validierung Pre-Flight gegen das
Tool-Schema (`CapabilityIndex.enum_values` + Gate in `state_machine._plan_execute_loop`):
ungültiger Wert → Note mit erlaubten Werten + Re-Plan (`enum_rebuttals<2`), danach ehrliche Senke
(`_respond_invalid_argument`); Invalid-Call wird nie emittiert. (b) Turn-übergreifender
Retry-Bound: `Ledger.failed_call_signatures()` (persistiert, im Gegensatz zum TurnContext) — ein
(tool, args)-Call mit vorherigem `status="FAILURE"` wird nicht identisch erneut emittiert →
`_respond_tool_error`. Tests: `tests/test_glassbox_oi017.py` (9 grün) inkl. Null-FP (gültiger
Enum → PASS) und „nicht 16 Retries".

---

### OI-017 (Original-Beschreibung, historisch)
**Stufe:** Execution  **Priorität:** hoch

`disambiguation_2` (task_type=`disambiguation_user`, Fenster) scheitert **3/3** — aber NICHT an
der Disambiguierung: die Rückfrage „To what percentage should I open the windows?" ist korrekt
(ein Trial hat sogar `r_user_end_conversation=1`). Der Reward fällt durch `r_tool_execution=0`:
**16× `OpenCloseWindow_003: Invalid window requested - Choose one of ALL, DRIVER, PASSENGER,
DRIVER_REAR, PASSENGER_REAR, RIGHT_REAR, LEFT_REAR.`** Der Agent übergibt dem Fenster-Tool nach
der Rückfrage einen ungültigen `window`-Enum (vermutlich verwechselt er Prozentwert und
Fenster-Selektor oder rät den Enum). 16 Wiederholungen deuten zudem auf eine Retry-Schleife
ohne Abbruch bei wiederholtem Tool-Fehler hin.

**Nächster Schritt (Härtung):** (a) `window`-Argument gegen die erlaubte Enum-Liste des
Tool-Schemas validieren (Pre-Flight, analog zu Numerik-Provenienz), (b) bei wiederholtem
identischem Tool-Fehler nicht endlos retryen, sondern ehrlich abbrechen/rückfragen. Reproduzieren
und Trajektorie lesen, bevor eine feste Zuordnung „Prozent→control_sunroof/window" gebaut wird.

---

## OI-018 — Ledger-abgeleitete Wert-/Route-Auflösung fehlte (dis_18/dis_24) + dis_8 Grenzfall
**Entdeckt:** 2026-07-09 (Auftrag E3-FIX, Phase F1)  **Behoben (Code):** 2026-07-09 —
**Verifikation: TEILERFOLG.** Code greift wenn INTAKE den Slot korrekt flaggt (2/6 Fälle).
Intake-Prompt-Schärfung revertiert (Regression dis_0).  **Stufe:** 6

**Root Cause (an E2-Traces belegt, nicht vermutet):** Zwei `disambiguation_internal`-Tasks
scheitern 3/3 an derselben Stelle — die Kaskade erreicht Priorität 5 und stellt die
Fallback-Rückfrage „Could you tell me the exact value you'd like me to use?", weil der
Slot-Wert weder aus Präferenz, Heuristik noch Kandidatenliste kommt, sondern aus einem
**früheren Tool-Ergebnis im Ledger abgeleitet** werden muss:

- **dis_24** (`disambiguation_24`, GT `route_id_leading_to_new_destination=rll_boc_ham_564928`):
  Der Planner ruft `get_routes_from_start_to_destination` (Ergebnis `result.routes=[…]`), dann
  `navigation_replace_final_destination`. Die Route-ID muss per dokumentierter Heuristik
  „fastest route for multi-stop navigation" (= min `duration_hours`) aus der Liste gewählt
  werden. Trace: `get_current_navigation_state` → `get_location_id_by_location_name` →
  `get_routes…` → **Fallback-Rückfrage statt Auswahl** → STOP.
- **dis_18** (`disambiguation_18`, GT `set_fan_speed level=1`): „increase the fan speed a bit"
  = relative Anpassung. Der Planner holt `get_user_preferences` + `get_climate_settings`
  (`fan_speed=0`), zieht die Richtung FEET korrekt aus der Präferenz, **blockiert dann aber am
  Slot `set_fan_speed.level`** mit derselben Fallback-Rückfrage — kein Zielwert nennbar, aktueller
  Wert +1 nötig.

**Fix (Lesson 1a, deterministisch, tabellengesteuert — kein Task-Antwort-Hardcoding):**
zwei Regel-Tabellen in `disambiguation.py`, ausgewertet in `pre_flight` VOR der pref/heuristic-
Kaskade (Priorität 4, Kontext ergibt genau einen Wert):
- `_SELECTION_RULES[(tool, arg)]` → wählt aus einem früheren Ergebnis-Listenfeld die ID mit
  min. Zielgröße (Tie-Break sekundär). Für dis_24: min `duration_hours`, Tie-Break `distance_km`.
- `_RELATIVE_VALUE_RULES[(tool, arg)]` → liest aktuellen Wert aus einem früheren Ergebnis,
  ± ein Schritt (Richtung aus neuem INTAKE-Feld `ValueAmbiguity.relative_change`), geklemmt an
  Schema `minimum`/`maximum`. Für dis_18: `get_climate_settings.fan_speed` ± 1, [0,5].
Der Tool-Name ist Konfiguration eines generischen Mechanismus (wie `_HEURISTIC_DEFAULTS`),
keine fest verdrahtete Antwort; die Werte kommen ausschließlich aus dem Ledger. Fehlt die
Quelle → keine Ableitung → normale Kaskade fragt (Null-FP-Test deckt das ab).
Unit-Tests: `SelectionRuleTest`, `RelativeRuleTest` in `test_glassbox_disambiguation.py`
(inkl. table-driven-Nachweis mit synthetischem Tool/Domäne).

**dis_8 — bewusster GRENZFALL, NICHT gefixt:** `disambiguation_8` ist keine Wert-Ableitung,
sondern eine **Tool-Auswahl** („welches Licht anschalten"): Kontext (7:30 morgens, Sonne oben,
bewölkt → Sicht reduziert; Abblendlicht bereits an; Fernlicht sinnlos) → GT `set_fog_lights(on=True)`
PLUS Confirmation-Handshake (bewölkt = REQUIRES_CONFIRMATION). Eine deterministische Auflösung
verlangte Mehrfaktor-Domänenregeln („bewölkt + Tag + Abblendlicht an → Nebellicht"), also genau
das per CLAUDE.md/Lesson-1a verbotene Domänen-Hardcoding, und zusätzlich Policy-Confirmation.
Blast-Radius und Halluzinationsrisiko zu hoch für den Ertrag eines Tasks → **akzeptierte Grenze
bis 19. Juli.** Falls angegangen: eigener kontextbasierter Tool-Selektor mit strengem Gate,
separat evaluiert.

---

## OI-019 — Silent Refusal: Planner ignoriert verfügbare Tools (base_2, base_28, dis_28, dis_34)
**Entdeckt:** 2026-07-09 (Auftrag E2, Fail-Vor-Klassifikation)  **Behoben (Code):** 2026-07-10
**Verifikation AUSSTEHEND (Cost-Gate).**  **Stufe:** PLAN-Loop  **Priorität:** hoch

**Root Cause (E2-Agent-Log verifiziert):** Der Planner gibt `steps=[]` zurück OHNE
`capability_missing=True` zu setzen, obwohl die benötigten Tools (`open_close_trunk_door`,
`set_fan_speed`, `set_fan_airflow_direction`) im Katalog vorhanden sind. Der bestehende
PLAN-GUARD fängt nur den Fall ab, wo der Planner explizit `capability_missing=True` meldet;
der „stille Refusal" (keine Steps, kein Flag) fällt durch zu VERIFY/RESPOND und generiert
eine Refusal-Antwort.

**Zweiter Root Cause (Intake, stochastisch):** In manchen Trials erfindet das INTAKE-LLM
falsche Toolnamen (`open_trunk_door` statt `open_close_trunk_door`). Fuzzy-Match (ratio 0.833
> 0.80) hätte greifen sollen; in der Praxis fängt die H-R2-Rebuttal das korrekt ab, aber
die RE-EXTRACTED Intent hat denselben Fehler → INTAKE-Refusal. Dieses zweite Muster ist
LLM-Stochastik, nicht deterministisch fixbar.

**Fix (Lesson 1a, deterministisch):** Silent-Refusal-Guard in `_plan_execute_loop`:
wenn `build_plan` leere Steps liefert UND `capability_missing` NICHT gesetzt UND INTAKE
`required_tools` hat, die im Katalog stehen UND keine Tool-Calls bisher ausgeführt UND
keine Capability-Rebuttals versucht → ein bounded Re-Plan (max 1) mit Policy-Note, die
die verfügbaren Tools explizit benennt.

**Tests:** `SilentRefusalGuardTest` (3): (1) Re-Plan feuert + Tool wird genutzt,
(2) kein Re-Plan wenn keine required_tools, (3) bounded auf 1 Re-Plan.

---

## OI-020 — Hallucination-Konsistenz: False-Refusal + Unknown-Blockade + Distance-Fabrication
**Entdeckt:** 2026-07-10 (Auftrag F, F4-Analyse)  **Behoben (Code):** 2026-07-10
**Verifikation AUSSTEHEND (Cost-Gate, kombinierter F2+F4-Lauf).**  **Stufe:** Guard/Prompt
**Priorität:** hoch

**Root Causes (E2-Traces verifiziert, 6 Tasks, 3 Kategorien):**

**(a) Capability-Gap (hall_28, hall_32):** Agent führt Tools ERFOLGREICH aus (tool_result
SUCCESS im Ledger), behauptet dann in RESPOND: "I'm not able to control X." Der LLM
generalisiert 1 fehlendes Tool (z.B. `set_fan_speed`) zu "alle Tools der Domäne fehlen"
inklusive solcher, die soeben erfolgreich benutzt wurden. State-Trace belegt: PLAN→EXECUTE
(SUCCESS) → RESPOND (Widerspruch).

**(b) Response-Form (hall_30):** "unknown"-Werte in Tool-Results werden als Policy-Blockade
interpretiert. Fog_lights="unknown" → Agent verweigert High Beams wegen Policy, obwohl
unknown ≠ on.

**(a+b) hall_36:** FabricationGuard.C5 fängt relative Entfernungs-Claims ("Prague is way
further") nicht, weil Claim-Extraktion diese nicht als faktisch erkennt und Prüflogik nur
Digits prüft.

**Fixes (Lesson 1a):**
- **C6 Inability-Contradiction-Guard** (guard.py): Unfähigkeits-Claim + erfolgreiches Tool
  im Ledger (Entity-Match >50%) → Satz entfernt. Null-FP: honest inability bleibt.
- **Unknown-Semantik** (plan.py, verify.py): "unknown" = missing info, nicht Fakten-Wert.
  Keine Policy-Blockade auf unknown. Erfolgreiche Calls müssen anerkannt werden.
- **Relative-Distance-Claims** (guard.py): Claim-Prompt erweitert, Prüflogik: wenn Route-
  Daten "unknown" → relative Entfernungs-Claims sind ungedeckt → ersetzt.

**Tests:** `test_glassbox_f4_hallucination.py` (15): C6-Inability (5), SuccessfulTools (2),
Sanitize-Integration (2), RelativeDistance (6). Alle grün.
