# ADR-0006 — Schlanker Auditor: erzwungener Self-Check statt separatem Audit-LLM

Datum: 2026-07-08 · Status: akzeptiert · Stufe: 7 (Auditor)

## Kontext

Stufe 7 soll eine gezielte Selbstprüfung an zwei Stellen leisten:
1. **VOR** zustandsändernden Tool-Calls (handeln nur mit Deckung), und
2. **VOR** der finalen Antwort (behaupten nur mit Deckung).

Die naive Umsetzung wäre ein eigener Audit-LLM-Aufruf, der Plan bzw. Antwortentwurf
noch einmal gegen Wahrheit + Ledger prüft. Das kostet pro Turn einen zusätzlichen
Roundtrip und ist redundant: die beiden Checkpunkte sind bereits weitgehend abgedeckt.

## Entscheidung

Der Auditor bleibt **bewusst schlank** und macht **keinen eigenen LLM-Aufruf**.

### Checkpunkt 1 (vor state-changing Calls) — bereits realisiert

Deckung wird schon in jeder PLAN-Runde vor EXECUTE erzwungen durch:
- **Stufe 4** PolicyChecker Pre-Flight (ADR-0004) — blockt/injiziert/stellt zurück,
- **Stufe 5** FabricationGuard C2 (`check_tool_arguments` + Provenance) — jedes
  Call-Argument braucht Ledger-Herkunft.

Ein zusätzlicher Auditor-Schritt hier wäre reine Dopplung. **Kein eigener Code nötig.**

### Checkpunkt 2 (vor der finalen Antwort) — erzwungener Self-Check

Statt eines zweiten Extraktions-LLM-Aufrufs wird die Selbstprüfung **in den bestehenden
VERIFY-Draft-Call gefaltet**: das Draft-Prompt verlangt ZUERST eine Enumeration jeder
faktischen Behauptung mit ihrer verbatim Ledger-Quelle (`claims: list[ClaimCheck]`),
DANN die Antwort. Der `Auditor.pre_response_check` **parst diese Selbstprüfung
deterministisch** (Lesson 1a: LLM liefert Kandidaten, Code entscheidet):

- Pro Claim: nur **numerische** Werte sind hier deterministisch falsifizierbar
  (`re.search(r"\d", value)`); String-Only-Claims bleiben unangetastet (mögliche gültige
  Paraphrasen von Tool-Ergebnissen → Null-FP-Disziplin).
- Wert wird gegen den Ledger-Korpus geprüft (`_value_in_ledger`); eine deklarierte
  Quelle (≠ leer/"inferred"/"context"/"none") muss im Korpus vorkommen.
- Ungedeckte numerische Behauptung → der **ganze Satz** wird durch ein ehrliches
  Eingeständnis ersetzt (`_HONEST_ADMISSION`), nicht getilgt.

Der so bereinigte Text (`audit.safe_text`) geht anschließend in
`FabricationGuard.sanitize` (Stufe 5, C5) — Auditor und Guard sind komplementär:
Auditor prüft die **selbst-deklarierten** Claims deterministisch, der Guard extrahiert
zusätzlich per LLM latente Claims aus dem Fließtext.

### Konservativer Default

Im Zweifel wird die ungedeckte Behauptung durch ein Eingeständnis ersetzt
(handeln/behaupten nur mit Deckung). Es wird nie gegen nachgebildete Evaluator-Subscores
geprüft und nie iterativ gegen die Wertung repariert — ausschließlich gegen Wahrheit +
Ledger.

### Telemetrie (GuardResult, C1-Interface)

- `Auditor.pre_response` (PASS) — alle deklarierten Claims gedeckt.
- `Auditor.pre_response` (BLOCK) — ≥1 numerische Behauptung ohne Deckung ersetzt.

## Konsequenzen

- **Positiv:** kein zusätzlicher LLM-Roundtrip pro Turn (Kosten/Latenz konstant); die
  Selbstprüfung ist deterministisch reproduzierbar und testbar; ordnet sich in das
  bestehende GuardResult-Telemetrie-Interface ein.
- **Kosten:** das VERIFY-Draft-Prompt wird länger (Self-Check-Sektion) und der Draft-Call
  liefert ein strukturiertes `Draft`-Objekt statt eines Strings — `_verify_and_respond`
  wurde entsprechend angepasst.
- **Grenze:** nur numerische Behauptungen werden deterministisch geprüft; qualitative
  Fabrikationen fängt weiterhin der FabricationGuard (LLM-Extraktion) ab. Bewusste
  Abgrenzung zugunsten Null-FP.

Getestet: `tests/test_glassbox_auditor.py` (deterministischer pre_response_check:
Wert im Ledger → PASS; numerischer Wert nicht im Ledger → Satz ersetzt; nicht-numerisch
→ ignoriert (Null-FP); leere Claims → PASS; mehrere Claims, einer ungedeckt).
