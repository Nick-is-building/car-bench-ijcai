# ADR-0002: Resumierbare Zustandsmaschine mit begrenzter PLAN→EXECUTE-Schleife

Datum: 2026-07-03   Status: akzeptiert

## Kontext

Der Bauplan (Stufe 2) beschreibt den Ablauf INTAKE → CAPABILITY_CHECK → (CLARIFY | PLAN) → POLICY_CHECK → EXECUTE → VERIFY → RESPOND als lineare Sequenz. Zwei Realitäten des Benchmarks erzwingen Anpassungen:

1. **Das A2A-Protokoll ist multi-turn:** Der Agent führt Tools nicht selbst aus. Er sendet Tool-Calls als Antwort-Nachricht; der Evaluator führt aus und liefert die Ergebnisse in einer *neuen* Nachricht. Ein synchroner `tool_executor`-Callback (Stufe-0-Skelett) ist damit nicht umsetzbar — die Maschine würde bei jedem Ergebnis-Empfang wieder bei INTAKE starten und INTAKE/PLAN mehrfach (nicht-deterministisch) durchlaufen.
2. **Tool-Argumente hängen von vorherigen Ergebnissen ab** (z. B. erst `get_weather`, dann abhängig davon `open_close_sunroof`). Ein einziger vollständiger Plan vor der Ausführung kann diese Werte nicht kennen.

## Entscheidung

1. **Resumierbare Maschine:** `run_turn(ctx)` startet einen User-Turn und gibt eine *Aktion* zurück (`EmitToolCalls` oder `EmitText`). Der `TurnContext` persistiert in der A2A-Schicht pro `context_id`. Nach Eintreffen der Tool-Ergebnisse (von der A2A-Schicht ins Ledger geschrieben) setzt `resume(ctx)` exakt am PLAN-Punkt fort — INTAKE und CAPABILITY_CHECK laufen pro Turn genau einmal.
2. **Begrenzte PLAN→POLICY_CHECK→EXECUTE-Schleife:** Der Planner liefert pro Runde nur die *sofort ausführbaren* Schritte (alle Argumentwerte bekannt); abhängige Schritte folgen in der nächsten Runde, nachdem die Ergebnisse im Ledger liegen. Leerer Plan = Turn abgeschlossen → VERIFY → RESPOND. Obergrenze `MAX_PLAN_ROUNDS = 8` stoppt Planner-Schleifen deterministisch.
3. **Idempotenz:** Deterministische Call-IDs (`call_t{turn}_r{runde}_s{index}` statt uuid4) und eine Signatur-Menge `(tool, kanonisches-JSON-der-Argumente)` pro Turn — ein identischer Call wird nie doppelt ausgeführt (vgl. „Sunroof zweimal geöffnet"-Beispiel im Benchmark-Repo). Liefert eine Planrunde nach Dedupe/Capability-Filter keine ausführbaren Calls, endet die Schleife (Planner-Loop-Erkennung).
4. **Stub-sichere Defaults:** Solange Stufe 3–7 Stubs sind, arbeitet die Maschine mit dokumentierten Pass-through-Defaults (Capability→covered, Policy→keine Verstöße, Guard→Durchreichen). Bei Ambiguität stellt sie die bereits in INTAKE formulierte Rückfrage (konservativer Default: ehrliches Zögern). Unbekannte Tools/Parameter im Plan werden deterministisch abgefangen → ehrliche Ablehnung, nie ein Call an den Evaluator, den es nicht gibt.
5. **RESPOND ohne zweiten LLM-Call:** Der VERIFY-Entwurf folgt bereits Persona/Format-Regeln; `finalize` ist deterministische Bereinigung (Markdown-Reste, Whitespace). Ein zweiter Umformulierungs-Call wäre nur zusätzliche Varianz und Kosten.

## Begründung

- Pass^3 verlangt identische Trajektorien; Resumierbarkeit + feste Rundenstruktur + deterministische IDs eliminieren die strukturellen Varianzquellen der A2A-Schleife.
- Die begrenzte Planschleife bleibt „kein frei laufendes LLM": jede Runde ist ein enger strukturierter Aufruf, die Übergänge sind Code.

## Alternativen

- *Ein Plan mit Platzhaltern (null-Werte), Maschine füllt nach:* verlagert Wertauflösung in fehleranfälligen deterministischen Code (Pfad-Ausdrücke in Tool-Results); die Rundenlösung nutzt das LLM genau dafür, strukturiert und pro Runde überprüfbar.
- *Native Tool-Use-Schleife (Baseline-Stil):* frei laufendes LLM, genau die Varianzquelle, die ADR-0001 ausschließt.

## Konsequenzen

- Die A2A-Schicht hält aktive `TurnContext`-Objekte pro Kontext und matcht Ergebnisse FIFO über Tool-Namen auf pending Calls.
- LLM-Aufrufe pro Turn: 1× INTAKE + (Runden+1)× PLAN + 1× VERIFY-Draft.
- Intent-Schema: `required_params` als Liste `{tool, params}` (statt Dict) für strikte JSON-Schema-Kompatibilität; Plan-Argumente als `arguments_json`-String (Provider-sicher, Validierung im Retry-Loop).
