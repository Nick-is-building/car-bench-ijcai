# Open Issues — CAR-bench Glassbox Agent

Offene Lücken und bekannte Schwächen, die vor der Finalwertung (19. Juli) adressiert
oder bewusst akzeptiert werden müssen. Eintrag sofort, wenn erkannt. Erledigtes abhaken.

---

## OI-001 — Result-Feld-Entzug nicht implementiert
**Entdeckt:** 2026-07-04  **Stufe:** 3 (Capability-Matcher)  **Priorität:** hoch

`hallucination_missing_tool_response` (dritter Hallucination-Typ): der Evaluator
entfernt ein Antwortfeld aus einem Tool-Schema. Weder `check()` noch `check_step()`
in `capability.py` prüfen, ob ein erwartetes Antwortfeld vorhanden ist — es gibt keine
`has_result_field()`-Funktion. Kein Test deckt diesen Fall ab.

**Risiko:** Wenn der Smoke- oder Kalibrierungslauf einen `hallucination_missing_tool_response`-
Task zieht, verhält sich der Agent wie bei einem Base-Task (kein Eingeständnis → `HALLUCINATION_ERROR`).

**Nächster Schritt:** In Stufe 3 nachimplementieren oder spätestens als Teil von Stufe 5
(FabricationGuard) über Ledger-Result-Prüfung abfangen.

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

## OI-005 — test_agent_scenario_directories_use_standard_matrix schlägt fehl
**Entdeckt:** 2026-07-03  **Stufe:** Infrastruktur  **Priorität:** niedrig

Der Upstream-Test erwartet exakt die 6 Standard-TOML-Dateien. Unsere
`local_smoke_glassbox.toml` ist nicht in `EXPECTED_SCENARIO_FILES` (test_scenario_contract.py Z.26–33).
Kein Logikfehler — der Test kennt unsere Datei schlicht nicht.
Trivial behebbar: `local_smoke_glassbox.toml` in der Exclusion-Liste neben
`a2a-scenario.toml` eintragen, oder Upstream-EXPECTED-Set erweitern.
Wird nicht behoben bis der Fix sich als nötig erweist.

---

## OI-006 — check() deterministisch nur für Tool/Parameter-Entzug
**Entdeckt:** 2026-07-04  **Stufe:** 3  **Priorität:** dokumentarisch

`check()` und `check_step()` sind vollständig deterministisch (reines Dict-Lookup,
kein LLM-Aufruf). Das gilt aber ausschließlich für die Typen `hallucination_missing_tool`
und `hallucination_missing_tool_parameter`. Für `hallucination_missing_tool_response`
gibt es keinen deterministischen Pfad (→ OI-001).
