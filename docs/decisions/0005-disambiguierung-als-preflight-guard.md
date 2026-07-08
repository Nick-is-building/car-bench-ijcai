# ADR-0005 — Disambiguierung als Pre-Flight-Guard in der PLAN-Schleife

Datum: 2026-07-08 · Status: akzeptiert · Stufe: 6 (DisambiguationEngine)

## Kontext

Der CAR-bench-Split `disambiguation` misst zwei Untertypen:
`disambiguation_internal` (die Mehrdeutigkeit MUSS intern aufgelöst werden — NIE fragen)
und `disambiguation_user` (bleiben ≥2 gültige Kandidaten, MUSS EINE Rückfrage gestellt
werden). Die wiki.md-Disambiguierungs-Policy definiert eine feste Prioritätsreihenfolge:
0 Policy-Regeln · 1 expliziter User-Request · 2 gelernte Präferenzen
(`get_user_preferences`) · 3 heuristische Defaults · 4 Kontext (Fahrzeugzustand,
`get_*`-Tools) · 5 Rückfrage.

Der Stufe-6-Stub (`DisambiguationEngine.resolve()`) warf immer `NotImplementedError`,
die State Machine fiel auf die Intake-Rückfrage zurück → für `internal`-Tasks strukturell
falsch (OI-004).

## Entscheidung

Der Motor läuft **NICHT** als separater Pre-Plan-Schritt, sondern als **Pre-Flight-Guard
in der PLAN-Schleife** — analog zu PolicyChecker (Stufe 4, ADR-0004) und FabricationGuard
(Stufe 5). Ausschlaggebend: Präferenzen (Priorität 2) und Kontext (Priorität 4) liegen
erst **nach aktivem Abruf** im Ledger. Ein Pre-Plan-Schritt vor jeglichem Tool-Call hätte
sie nicht. Der Guard kann daher — wie AUT-POL:009 in Stufe 4 — einen
`get_user_preferences`-Call **injizieren und den state-changing Call zurückstellen**
(gather-then-resolve); im Folge-Round greift die Kaskade mit vorliegender Präferenz.

### Auflösungs-Kaskade (deterministisch, Code entscheidet — Lesson 1a)

Pro mehrdeutigem `(tool, argument)`-Slot, den Intake geflaggt hat (`value_ambiguities`,
`user_stated=false`):

1. **Priorität 1** (expliziter Wert): wird upstream behandelt — ein vom User genannter
   Wert bedeutet, der Slot ist nicht mehrdeutig und erreicht den Guard nie.
2. **Priorität 2** (Präferenz): enge LLM-Extraktion strukturiert den freitextlichen
   `get_user_preferences`-Eintrag zu `{default, prohibited}`. Ist ein `default` vorhanden
   und nicht prohibitiert → **still anwenden**.
3. **Priorität 3** (Heuristik): eindeutiger, in wiki.md benannter Default (Tabelle
   `_HEURISTIC_DEFAULTS`, z. B. Multi-Stop-Route = `fastest`) → still anwenden.
4. **Priorität 4** (Kontext): genau ein gültiger Kandidat aus dem Fahrzeugzustand →
   still anwenden.
5. **Priorität 5** (Rückfrage): state-changing UND ≥2 gültige Kandidaten (oder kein
   Resolver griff) → **EINE** gezielte Rückfrage (`_respond_disambiguation`).

Prohibitions (Priorität 0/2) eliminieren Kandidaten; es wird **nie** unter gültigen
Kandidaten gerankt (wiki.md: „do not perform a ranking of valid options").

### Rollen (Lesson 1a)

- **LLM (Kandidatengenerierung):** Intake flaggt mehrdeutige `(tool, argument)`-Slots
  (`ValueAmbiguity`); eine enge Extraktion (`prompts/clarify.extract_preference`)
  strukturiert die Präferenz-Freitexte. Beides ist Extraktion, keine Entscheidung.
- **Code (Entscheidung):** `resolve_slot()` wendet die Kaskade per Map-Lookup an und
  entscheidet still-anwenden vs. fragen.

### Value-Flow-Garantie

Der aufgelöste Wert wird vom Guard **direkt im Call-Argument überschrieben**
(`_coerce` typ-koerziert, z. B. „50%" → 50), nie dem Planner-LLM überlassen. Test:
geparste Präferenz 22 → Call-Arg exakt 22.

### Telemetrie (GuardResult, C1-Interface)

- `Disambiguation.gather` (UNCERTAIN) — Präferenz-Abruf injiziert, Call zurückgestellt.
- `Disambiguation.resolve` (PASS) — n Argumente still aufgelöst.
- `Disambiguation.clarify` (BLOCK) — ≥2 Kandidaten → Rückfrage.

## Konsequenzen

- **Positiv:** `disambiguation_internal` wird still aufgelöst (Null spurious Rückfragen);
  `disambiguation_user` fragt genau einmal. Ordnet sich in die bestehende
  Inject/Defer/Block-Architektur ein — keine neue State-Machine-Zustandslogik. Der
  CLARIFY-Zustand bleibt für echte Ziel-/Tool-Mehrdeutigkeit (`is_ambiguous`).
- **Kosten:** eine enge Extraktions-LLM-Anfrage pro mehrdeutigem Slot, nur wenn
  Präferenzen im Ledger liegen. Ein zusätzlicher gather-Round pro Turn mit
  Präferenz-Bedarf.
- **Grenze:** Kontext-Kandidaten (Priorität 4) werden vorerst nur best-effort abgeleitet;
  fehlen sie, fällt `disambiguation_user` auf die Intake-Rückfrage zurück (konservativ,
  korrekt). Erweiterung späterer Aufträge.

Behebt OI-004. Getestet: `tests/test_glassbox_disambiguation.py` (18 Tests, beide
Untertypen, Null-FP, Value-Flow, gather-Verdrahtung).
