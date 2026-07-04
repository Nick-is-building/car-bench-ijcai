# Open Issues — CAR-bench Glassbox Agent

Offene Lücken und bekannte Schwächen, die vor der Finalwertung (19. Juli) adressiert
oder bewusst akzeptiert werden müssen. Eintrag sofort, wenn erkannt. Erledigtes abhaken.

---

## OI-001 — Result-Feld-Entzug nicht implementiert
**Entdeckt:** 2026-07-04  **Stufe:** 3/5  **Priorität:** hoch
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

**Risiko bis Stufe 5:** Zieht Smoke-/Kalibrierungslauf einen `hallucination_missing_tool_response`-
Task, gibt der Agent eine fabrizierte konkrete Antwort → `HALLUCINATION_ERROR`.

**Nächster Schritt:** Stufe 5 (FabricationGuard) implementieren und Test un-skippen.

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

## OI-004 — DisambiguationEngine ignoriert user_preferences (Stufe-6-Stub)
**Entdeckt:** 2026-07-04  **Stufe:** 6  **Priorität:** hoch (56 Disambiguation-Tasks)

Alle `disambiguation_internal`-Tasks scheitern strukturell, weil der Stub
`DisambiguationEngine.resolve()` immer `NotImplementedError` wirft. Die State Machine
fällt dann auf die Intake-Rückfrage zurück — das ist für `internal`-Tasks falsch
(`r_user_end_conversation` kann 0 werden, `r_actions_final` immer 0).

**Nächster Schritt:** Stufe 6 implementieren; Priorität nach Stufe 4+5.

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

## OI-007 — Confirmation-Handshake (LLM-POL:004 / 007 / 008) nicht deterministisch
**Entdeckt:** 2026-07-04 (Auftrag B, ADR-0004)  **Stufe:** 4  **Priorität:** hoch

Drei Policies verlangen ein explizites Nutzer-„yes" VOR der Ausführung:
LLM-POL:004 (REQUIRES_CONFIRMATION-Tools), LLM-POL:007 (Fenster >25 % bei AC an),
LLM-POL:008 (Wetter-Gate bei adversen Bedingungen). Der Trigger ist jeweils
deterministisch erkennbar (Tool-Beschreibungs-Präfix; Args + AC-Zustand;
get_weather-Result im Ledger), aber der Handshake selbst — Turn beenden, Details
nennen, in der Folge-Äußerung eine Bestätigung erkennen — braucht eine
Zustandsmaschinen-Erweiterung (Pending-Confirmation-Zustand über Turn-Grenzen)
plus semantische Bestätigungs-Erkennung. In v1 nur als markierte Obligation-Note
(007) bzw. Prompt-Obligation (004/008) abgedeckt.

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
