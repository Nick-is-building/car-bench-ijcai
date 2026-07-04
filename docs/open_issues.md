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

## OI-002 — Nur AUT-POL:005 ist deterministisch; 18 Policies LLM-abhängig
**Entdeckt:** 2026-07-04  **Stufe:** 3/4  **Priorität:** hoch (Stufe 4 behebt das)

Von den 19 AUT-POL-Policies ist nur AUT-POL:005 (sunroof/sunshade) als deterministischer
Guard in `state_machine.py` implementiert. Die restlichen 18 Policies werden nach wie vor
nur durch den LLM-Planner (Prompt-Härtung) und den Gemini-Policy-Judge beachtet.
Der Judge ist LLM-basiert und eine Varianzquelle außerhalb unserer Kontrolle.
Wie viel Varianz er in der aktuellen Konfiguration verursacht, wurde nicht gemessen.

**Nächster Schritt:** Stufe 4 (PolicyChecker) — 19 Policies als deterministische
Pre-Flight-Prädikate, sodass Verletzungen strukturell unmöglich werden.

---

## OI-003 — Lauf-2-Ausreißer: Sunshade-Prozentzahl vom Planner falsch geerbt
**Entdeckt:** 2026-07-04  **Stufe:** 2/3  **Priorität:** mittel

Im Stabilitätslauf 2 öffnete der Planner Sunshade und Sunroof parallel je auf 50 %.
GT erwartet Sunshade=100 %/Sunroof=50 %. Kein Guard erzwingt aktuell "Sunshade immer
auf 100 % vor Sunroof". AUT-POL:005 prüft nur Verfügbarkeit, nicht den Prozentwert.
Tritt nur bei diesem Base-Task auf; andere Base-Tasks sind unbekannt betroffen.

**Nächster Schritt:** Überprüfen, ob AUT-POL-Policy zu Sunshade-Stellung vor Sunroof
(z. B. AUT-POL:005 Erweiterung oder eigene Policy) deterministisch prüfbar ist.

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
