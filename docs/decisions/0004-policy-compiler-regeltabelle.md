# ADR-0004 — Policy-Compiler: deklarative Regel-Tabelle statt Policy-Prädikate im Kontrollfluss

Datum: 2026-07-04 · Status: akzeptiert · Stufe: 4 (PolicyChecker)

## Kontext

Die 19 veröffentlichten Policies (wiki.md, IDs LLM-POL/AUT-POL 002–024) wurden bisher
nur durch Prompt-Härtung plus einen einzelnen hartkodierten Guard (AUT-POL:005 in
`state_machine.py`) beachtet. Stufe 4 soll Policy-Verletzungen strukturell unmöglich
machen. Voraussetzung ist eine ehrliche Klassifikation: Welche Policies sind überhaupt
deterministisch prüfbar?

Prüfgrundlage ist ausschließlich: (1) der wörtliche Policy-Text aus wiki.md,
(2) die Ledger-Herkunft von Fakten, (3) die Tool-Schemas. Evaluator-Subscores werden
nicht nachgebildet (Compliance-Grenze).

## Klassifikation der 19 Policies (Forschungsergebnis)

Klassen: **(A)** voll deterministisch prüfbar · **(B)** teilweise — deterministischer
Vorbedingungs-Guard + semantischer Rest · **(C)** inhärent semantisch.

| ID | Kurzinhalt | Klasse | Deterministischer Guard (v1 implementiert?) | Semantischer Rest |
|----|-----------|:------:|---------------------------------------------|-------------------|
| LLM-POL:002 | Metrisches System, 24h-Format in Antworten | C | — | Ausgabeformat ist Text-Eigenschaft → VERIFY-Prompt |
| LLM-POL:004 | REQUIRES_CONFIRMATION-Tools: erst explizites „yes“ | B | Flag deterministisch aus Tool-Beschreibungs-Präfix ableitbar; Confirmation-Handshake-Guard entworfen, **v1 nicht implementiert** (→ OI-007) | Erkennung, ob die Nutzeräußerung eine Bestätigung ist |
| AUT-POL:005 | Sunroof nur wenn Sunshade voll offen oder parallel geöffnet | A | ✅ `companion_available` (Verfügbarkeit) + `state_companion` (Sunshade=100 erzwingen/injizieren) | — |
| LLM-POL:007 | Fenster >25 % bei AC an → Warnung + Confirmation | B | ✅ Trigger-Erkennung (Args + bekannter AC-Zustand) → Obligation-Note an VERIFY; Handshake wie 004 → OI-007 | Warntext, Bestätigungsdialog |
| LLM-POL:008 | Confirmation bei widrigem Wetter (Paar mit 009) | ~~B~~ **A** | ✅ `requires_confirmation` (OI-007, Auftrag D): letzte `get_weather`-Condition im Ledger deterministisch gegen die veröffentlichten Wetter-Mengen geprüft (Sunroof: nicht in {sunny, cloudy, partly_cloudy}; Fog: in {cloudy_and_thunderstorm, cloudy_and_hail}) → ohne User-„yes“ im Ledger BLOCK → Rückfrage | — (Reklassifiziert B→A, siehe unten) |
| AUT-POL:009 | Wetter muss vor Sunroof-Öffnen / Fog-Lights manuell geprüft sein | ~~B~~ **A** | ✅ `prior_observation`: `get_weather`-Result muss im Ledger stehen, sonst Injektion des Reads (Args deterministisch aus CURRENT_LOCATION/DATETIME). Der frühere semantische Rest („Bewertung widrig → Confirmation“) ist jetzt via 008 deterministisch. | — |
| AUT-POL:010 | Defrost front/all → Fan≥2, Richtung WINDSHIELD, AC an | A | ✅ `state_companion`: Zustand beobachten (get_climate_settings), fehlende Begleit-Calls injizieren | — |
| AUT-POL:011 | AC an → Fenster >20 % schließen, Fan 1 falls 0 | A | ✅ `state_companion` (get_vehicle_window_positions + get_climate_settings) | — |
| LLM-POL:012 | Einzelzonen-Temperatur mit >3 °C Differenz → informieren | B | Differenz nur berechenbar, wenn beide Zonentemperaturen im Ledger bekannt; **v1 nicht implementiert** (→ OI-008) | Inform-Text |
| AUT-POL:013 | Fog an → Low Beams an, High Beams aus | A | ✅ `state_companion` (get_exterior_lights_status) | — |
| AUT-POL:014 | High Beams verboten, wenn Fog an | A | ✅ `state_precondition`: fog_lights muss bekannt False sein; unbekannt → Read injizieren; True → Block | — |
| AUT-POL:016 | Routenstart = aktuelle Fahrzeugposition | B | ID-Vergleich Route-Start gegen CURRENT_LOCATION.id wäre möglich, erfordert Parsing der Routen-Metadaten aus Ledger-Results; **v1 nicht implementiert** (→ OI-009) | Routenwahl selbst |
| AUT-POL:017 | Waypoint-Edit-Tools nur bei aktiver Navigation | A | ✅ `state_precondition`: navigation_active bekannt True; unbekannt → get_current_navigation_state injizieren; False → Block | — |
| AUT-POL:018 | Bei aktiver Nav Edit-Tools statt Neuanlage; Edits nie parallel | B | ✅ `no_parallel` (max. 1 Edit-Call pro Batch) + `state_precondition` (set_new_navigation nur bei navigation_active False) | Wahl des fachlich richtigen Edit-Tools |
| AUT-POL:019 | Ziel nicht löschbar ohne Zwischenstopp (Route ≥ Start+Ziel) | A | ✅ `state_precondition` über bekannte Waypoint-Anzahl (≥3 für delete) | — |
| LLM-POL:021 | Detail-Route mit Maut → Nutzer informieren | C | — | Inhaltspflicht in freiem Text → VERIFY-Prompt |
| LLM-POL:022 | Multi-Stop ohne Auswahl → fastest je Segment + informieren | C | — | Heuristische Wahl + Inhaltspflicht → PLAN/VERIFY-Prompt |
| AUT-POL:023 | Kalender nur für den aktuellen Tag | A | ✅ `value_bound`: month/day == DATETIME aus Task-Kontext | — |
| AUT-POL:024 | Wetter nur für den aktuellen Tag (mit Uhrzeit) | A | ✅ `value_bound`: month/day == DATETIME (Uhrzeit-Pflicht deckt das Schema/check_step) | — |

**Bilanz (Auftrag B):** 9× A, 7× B, 3× C.

**Reklassifizierung Auftrag D (OI-007):** LLM-POL:008 und AUT-POL:009 (das Wetter-Paar)
wandern von **B → A**. Begründung: Mit dem neuen generischen Regeltyp
`requires_confirmation` ist der Confirmation-Trigger *und* die Bestätigungs-Erkennung
jetzt vollständig deterministisch gegen den Ledger prüfbar (letzte `get_weather`-Condition
gegen die veröffentlichten Wetter-Mengen; explizites User-„yes“ als Ledger-Eintrag nach
der Wetter-Beobachtung). Es bleibt kein semantischer Rest, der an LLM/Judge hängt.
**Neue Bilanz: 11× A, 5× B, 3× C.** Verbleibende B: 004, 007, 012, 016, 018.

## Entscheidung

**Eine deklarative Regel-Tabelle** (`policies.py: RULES`) mit generischen Regeltypen.
`PolicyChecker.pre_flight()` iteriert generisch über die Tabelle gegen Ledger +
geplanten Batch. **Kein Tool-Name im Kontrollfluss — Tool-Namen existieren nur in
den Daten** (Regel-Einträge, Effekt-Tabelle, Parser-Tabelle).

### Generische Regeltypen

| Typ | Semantik | Korrektur bei Verletzung |
|-----|----------|--------------------------|
| `companion_available` | Trigger-Tool erfordert, dass Companion-Tool im Katalog ODER im Batch ODER diese Turn bereits ausgeführt ist | ehrliche Ablehnung (capability_missing) |
| `state_companion` | Trigger erfordert Zustandsbedingungen; Zustand unbekannt → Beobachtungs-Call injizieren, Trigger verschieben; Bedingung verletzt → Begleit-Call injizieren | Injektion (korrigierende Aktion) |
| `state_precondition` | Trigger nur zulässig, wenn Prädikat über bekanntem Zustand wahr; unbekannt → Beobachtung injizieren | Block → Policy-Ablehnung, wenn bekannt falsch |
| `prior_observation` | Info-Tool-Result muss im Ledger stehen; Args des Reads deterministisch konstruierbar | Injektion des Reads, Trigger verschieben |
| `value_bound` | Parameter muss dynamischer Schranke genügen (z. B. == heutiges Datum) | Block → Policy-Ablehnung |
| `no_parallel` | Max. 1 Call einer Gruppe pro Batch | erster Call bleibt, Rest wird auf Folgerunde verschoben |
| `obligation_note` | Trigger erzeugt markierte Pflicht-Notiz an PLAN/VERIFY (semantischer Rest der B-Policies) | keine Blockade |
| `requires_confirmation` | `requires_confirmation_if(tool, condition)`: hält `condition` (deterministisch gegen Ledger) und liegt keine explizite User-Bestätigung im Ledger, wird der Trigger zurückgehalten | BLOCK → gezielte Rückfrage (kein Refusal); nächster Turn erkennt die Bestätigung und lässt den Call durch |

### Zustandsableitung (deterministisch, nur aus dem Ledger)

`derive_known_state(ledger)` faltet chronologisch: (a) geparste Results der
Beobachtungs-Tools (Parser-Tabelle `OBSERVATION_PARSERS`), (b) Effekte erfolgreicher
zustandsändernder Calls (Effekt-Tabelle `TOOL_EFFECTS`, Status SUCCESS im Result).
Nicht Ableitbares bleibt **unbekannt** — Regeln behandeln „unbekannt“ nie wie einen
Wert (Null-FP-Disziplin): unbekannt führt höchstens zur Injektion eines Reads, nie
zu Block oder Ablehnung.

### Schleifenschutz

Wurde die Beobachtung dieser Turn bereits ausgeführt und der Zustand ist trotzdem
unbekannt (z. B. FAILURE-Result), wird der Trigger **nicht erneut** verschoben,
sondern mit Notiz durchgelassen — kein Injektions-Loop, MAX_PLAN_ROUNDS bleibt
letzte Sicherung (ADR-0003).

## Bewusste Grenzen (Klasse C + zurückgestellte B-Guards)

- Klasse C (002, 021, 022) und die semantischen Reste der B-Policies werden als
  klar markierter Block („SEMANTIC POLICY OBLIGATIONS — not machine-checked“) in die
  PLAN- und VERIFY-System-Prompts aufgenommen. Das ist eine bewusste Grenze des
  Compilers: Inhaltspflichten in freiem Text sind nicht deterministisch erzwingbar,
  ohne die Antwortgenerierung selbst zu deterministisieren.
- Confirmation-Handshake: **008/009 in Auftrag D deterministisch gelöst** (`requires_confirmation`,
  siehe Reklassifizierung oben) — der Handshake läuft über den Ledger und die
  Zustandsmaschine (`_respond_confirmation`), ohne Zustandsmaschinen-Sonderzustand: die
  Rückfrage beendet den Turn, die Bestätigung des Folge-Turns liegt als User-Ledger-Eintrag
  vor und wird beim Re-Plan deterministisch erkannt. Die Bestätigungs-Erkennung ist ein
  konservativer Schlüsselwort-Gate (Affirmative-Menge, Negation voidet — lieber erneut
  fragen als eine unsichere Aktion auslösen). Für **004** (REQUIRES_CONFIRMATION-Tools) und
  **007** (Fenster >25 % + AC) ist derselbe Regeltyp direkt anwendbar (weitere Daten-Einträge),
  in v1 aber noch nicht bestückt.
- Trigger-Asymmetrie AUT-POL:005: Die Wert-Durchsetzung (Sunshade=100) greift nur,
  wenn der geplante Call das echte Schema (`percentage`) verwendet; die
  Verfügbarkeitsprüfung greift bei jedem Sunroof-Call. Grund: Die Regeln sind auf
  die realen Tool-Schemas geschrieben; Alt-Tests mit vereinfachten Fakes bleiben
  dadurch beweiskräftig für die unveränderte Refusal-Semantik.

## Konsequenzen

- AUT-POL:005 verschwindet als Sonderfall aus `state_machine.py` (Z. 203–212 alt)
  und wird ein Daten-Eintrag; alle Stufe-3-Tests bleiben unverändert grün — das
  belegt die Generalisierung.
- `pre_flight()` kann Batches **verändern** (Injektion, Verschiebung), nicht nur
  blockieren. Die Zustandsmaschine übernimmt den korrigierten Batch; verschobene
  Calls plant der Planner in der Folgerunde mit dann vorhandener Information neu
  (Notizen aus dem Pre-Flight gehen als markierter Block in den PLAN-Prompt).
- Injizierte Reads kosten je eine Plan-Runde; das ist durch MAX_PLAN_ROUNDS=16
  gedeckt (ADR-0003, ≤3 Injektionsrunden realistisch pro Task).
