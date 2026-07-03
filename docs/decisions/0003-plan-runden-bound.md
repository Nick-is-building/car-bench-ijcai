# ADR-0003: PLAN-Runden-Bound — Dimensionierung gegen Task-Abschneiden

Datum: 2026-07-03   Status: akzeptiert (präzisiert ADR-0002)

## Kontext

ADR-0002 führte `MAX_PLAN_ROUNDS = 8` als Schutz gegen Planner-Endlosschleifen ein — als stille Konstante ohne Dimensionierungs-Begründung. Das ist eine Design-Entscheidung mit direkter Wertungsfolge: Schneidet der Bound einen legitimen Task ab, fehlen Ground-Truth-Aktionen → `r_actions`/`r_tool_subset` = 0, und über Pass^3 wirkt jeder Ausfall dreifach.

**Messung an den veröffentlichten Train-Tasks** (`third_party/car-bench/docs/reference_data/tasks/`, Verteilung der GT-Aktionen pro Task):

| Split | Tasks | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | max |
|---|---|---|---|---|---|---|---|---|---|---|---|
| base | 100 | 21 | 12 | 21 | 19 | 12 | 5 | 3 | 2 | 5 | 9 |
| disambiguation | 56 | 11 | 6 | 13 | 12 | 3 | 4 | 3 | 2 | 2 | 9 |
| hallucination | 98 | 20 | 11 | 21 | 19 | 13 | 4 | 3 | 2 | 5 | 9 |

Tasks mit 9 GT-Aktionen existieren in **allen drei Splits** (z. B. base_64, base_74, disambiguation_47, hallucination_62).

**Semantik des Bounds:** Er begrenzt PLAN-Runden *pro User-Turn*, nicht Aktionen pro Task — eine Runde kann mehrere unabhängige Calls batchen, und Tasks verteilen sich über mehrere User-Turns. Aber der Worst Case ist real: 9 strikt sequenziell abhängige Aktionen in einem Turn brauchen 9 Runden, und Read-Calls (`get_weather`, `get_*_state`), die den GT-Aktionen vorausgehen, verbrauchen *zusätzliche* Runden. Bei Bound 8 wäre ein solcher Task still abgeschnitten worden.

## Entscheidung

1. `MAX_PLAN_ROUNDS = 16`: maximale GT-Aktionszahl (9, voll sequenziell) + Read-Runden + Marge. Der Bound bleibt reiner Havarie-Stopp; echte Planner-Schleifen fängt die Duplikat-Signatur-Erkennung (ADR-0002) deutlich früher.
2. **Instrumentierung:** `TurnContext.plan_bound_hit` wird gesetzt, wenn der Bound den Turn beendet, bevor der Planner Abschluss (leerer Plan) gemeldet hat; die A2A-Schicht loggt dann eine Warnung. Erwartung: **feuert auf wohlgeformten Tasks nie** — jedes Auftreten in Dev-Läufen ist ein zu untersuchender Befund.
3. **Abnahme-Ergänzung:** In jedem vollen Dev-Lauf wird geprüft, dass `plan_bound_hit` nicht auftrat (Log-Grep), bevor Ergebnisse gewertet werden.

## Begründung

- Kosten eines zu großen Bounds: nur im pathologischen Fall ein paar zusätzliche LLM-Runden. Kosten eines zu kleinen Bounds: stiller Totalausfall einzelner Tasks, dreifach über Pass^3. Asymmetrie klar zugunsten großzügiger Dimensionierung.
- Unit-Test abgesichert: 9 sequenzielle Aktionen + Abschlussrunde passen unter den Bound (`test_bound_allows_nine_sequential_actions`); Bound-Treffer setzt das Flag (`test_max_plan_rounds_bound`).

## Compliance

Die Messung nutzt ausschließlich die **veröffentlichten Referenzdaten** (Train-Task-Definitionen im Starter-Kit) — keine Hidden-Set-Sondierung, keine Nachbildung von Evaluator-Subscores. Die Aktionszahl-Verteilung dimensioniert eine allgemeine Schutzkonstante, kein Task-spezifisches Verhalten.

## Konsequenzen

- Dev-Lauf-Auswertung erhält einen festen Prüfschritt: `plan_bound_hit`-Warnungen zählen.
- Sollte ein Task mit legitimen >16 Runden auftauchen (nicht erwartet), wird der Bound mit neuem ADR erneut angehoben — nie still.
