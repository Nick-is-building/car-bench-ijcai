# Experiment-Index — CAR-bench Agent

Jeder Lauf mit Datum, Zweck, Commit-Stand (soweit bekannt), Result-Pfad und Gesamt-Ergebnis.
Erstellt 2026-07-23 (Audit). Nichts gelöscht, nur geordnet und indexiert.

## Konvention
- **Archiviert** = JSON liegt in `docs/experiments/` (im Repo)
- **Output-only** = JSON liegt nur in `output/track_1_agent_under_test/` (gitignored, VM-lokal)
- Commit = Glassbox-Agent-Commit zum Zeitpunkt des Laufs (sofern aus Devlog/Handover rekonstruierbar)

---

## Stufe-3 Smoke (2026-07-03)

| Datei | Runs | Ergebnis | Commit | Zweck |
|-------|------|---------- |--------|-------|
| `2026-07-03-smoke-glassbox.md` (Doku) | — | — | ~589db23 | Hallucinierung-Smoke-Protokoll |
| `2026-07-03-smoke-glassbox-raw.json` | 3 | Base+Hall+Dis je 1 Trial, Pass^1 100% Hall | ~589db23 | Stufe-3 Glassbox-Smoke |
| `2026-07-03-stufe3-smoke.md` (Doku) | — | — | ~589db23 | Analyse-Protokoll |

---

## Stufe-4 Abnahme-Serie (2026-07-04–05)

| Datei | Runs | Ergebnis | Commit | Zweck |
|-------|------|---------- |--------|-------|
| `2026-07-04-b6-abnahme-raw.json` | 15 | — | ~ba1503a (C) | Stufe-4 Abnahme Lauf 1 |
| `2026-07-04-b6-wiederholung-raw.json` | 15 | — | ~ba1503a | Stufe-4 Wiederholung |
| `2026-07-04-lauf3-oi011.json` | 15 | — | ~ba1503a | OI-011 Verifikation |
| `2026-07-04-lauf4-abnahme.json` | 15 | — | ba1503a | Stufe-4 Abnahme FINAL |
| `20260705-004553__...-stufe5_abnahme__.json` | 15 | Base 77.8%/Hall 100%/Overall 83.3% Pass^3 | ba1503a | **Auftrag C BESTANDEN** |

---

## Härtungs-/OI-Serie (2026-07-07–08)

| Datei | Runs | Ergebnis | Commit | Zweck |
|-------|------|---------- |--------|-------|
| `2026-07-08-e1-judge-variance.json` | 9 | 9/9 Pass^1=100% | ~H1/H2 | E1 Judge-Varianz |
| `20260708-020751__...-stufe6_abnahme__.json` | 9 | Dis 2/9=22.2% | ~H1/H2 | Auftrag D — Disambiguation-Abnahme |
| `2026-07-08-oi016-verify-optionA.json` | 9 | dis_4 0/3, hall 6/6 | 5e48541 | H3 OI-016 Option-A verify |
| `2026-07-08-oi016-rerun-fixAB.json` | 9 | dis_4 0/3, hall 6/6 | c395556 | H3 OI-016 Fix-A+B |
| `2026-07-08-oi016-c1-verify.json` | 9 | dis_4 3/3✓, hall 6/6 | 0d02ecb | H3 OI-016 C1 → OI-016 GESCHLOSSEN |

---

## Dev-Lauf E2 und F-Fixes (2026-07-09–10)

| Datei | Runs | Ergebnis | Commit | Zweck |
|-------|------|---------- |--------|-------|
| `2026-07-09-e2-dev-lauf.json` | 180 | **Overall 58.3%, Base 75%, Hall 70%, Dis 30%** | nach 0d02ecb | **Hauptmessperiode** |
| `2026-07-09-f1b-rerun-v2.json` | 9 | dis_0 regression revertiert | 6f54525 | F1b Intake-Rollback |
| `2026-07-10-f2f4-verify.json` | 36 | +9 Rewards, base_28/hall_30/hall_36 3/3 | nach 4af7fa7 | F2+F4 Hallucination-Hardening |
| `2026-07-10-g3-verify.json` | 30 | hall_16 3/3✓, hall_32 2/3✓ | a7191d8+G5 | G1+G2 Verifikation |

---

## H-Fixes und Modellvergleich (2026-07-11)

| Datei | Runs | Ergebnis | Commit | Zweck |
|-------|------|---------- |--------|-------|
| `20260711-103751__...-h_verify__.json` | 54 | **9/18 Pass^3 = 50%** (von 3/18 auf 9/18, +6 Tasks) | 66b116f | H-Verifikation (Sonnet-4-6) |
| `20260711-162509__...-i2_verify__.json` | 18 | 4/6 Pass^3, dis_26 0/3 | f34cc73 | I2 Wert-Durchfluss |
| `20260711-182802__...-i3_opus_compare__.json` | 54 | **Opus 8/18, Sonnet 9/18** — Sonnet schlägt Opus | 66b116f+f34cc73 | I3 Modellvergleich |

---

## J-Serie Disambiguation (2026-07-11–12)

| Datei | Runs | Ergebnis | Commit | Zweck |
|-------|------|---------- |--------|-------|
| `2026-07-11-j3-verify.json` | 27 | dis_16/18/24/28 3/3✓ | 858d1b6 | J-Verifikation |
| `2026-07-12-j6-mini.json` | 3 | dis_22 2/3 (J6 wirkt) | 9df4025 | J6 Mini-Verify |

---

## K-Serie Generalprobe-Vorbereitung (2026-07-12)

| Datei | Runs | Ergebnis | Commit | Zweck |
|-------|------|---------- |--------|-------|
| `2026-07-12-k1-verify.json` | 90 | 17/30 Pass^3 = 56.7% (Baseline vor K-Fixes) | vor K-Fixes | K1 Baseline |
| `2026-07-12-k7-mini.json` | 6 | Fix 1a/b/c dis_38: Wirkung teilweise | K7-Commit | K7 Mini |
| `2026-07-12-k10-mini.json` | 6 | base_10 3/3✓, dis_38 3/3✓ | 085b8e4 | K10 Mini — Fix 4 abgeschlossen |

---

## Generalprobe Delta (2026-07-13)

| Datei | Runs | Ergebnis | Commit | Zweck |
|-------|------|---------- |--------|-------|
| `2026-07-13-generalprobe-delta.json` | 207 | **31/69 = 44.9% Overall** (Base 50%, Hall 53.6%, Dis 9.1%) | vor 883db7b | 69 Delta-Tasks (zweite Hälfte Train) |

policy_aut_errors: 0/207. Lauf-ID: 20260713-040017.

---

## Subset-L und Finalplan-Phasen (2026-07-14–15)

Diese JSONs liegen noch in `output/` (Output-only). Noch nicht nach `docs/experiments/` kopiert.

| Datei (output/) | Runs | Ergebnis | Commit | Zweck |
|-------|------|---------- |--------|-------|
| `20260714-051201__...-local_subset_L__.json` | 180 | **27/60 = 45.0% Overall** (Base 56.5%, Hall 35%, Dis 41.2%) | 883db7b | Glassbox Subset-L |
| `20260714-224133__...-local_partial_probing__.json` | 60 | — | 883db7b | Partial-Probing 12 Tasks × 5 Trials |
| `20260715-012332__...-local_phase1_mini__.json` | 30 | Phase-1 Mini-Verify V1 | vor Phase-1 | Phase 1 Mini-V1 |
| `20260715-025035__...-local_phase1_mini__.json` | 30 | Phase-1 Mini-Verify V2 | — | Phase 1 Mini-V2 |
| `20260715-034458__...-local_phase1_mini__.json` | 30 | Phase-1 Mini-Verify V3 (BESTANDEN) | d0498cd | Phase 1 Mini-V3 |
| `20260715-042601__...-local_phase2_mini__.json` | 15 | **Phase-2 Mini NICHT BESTANDEN** (0/3 Enforcer, hall_18 0/3) | c9cbbb8 | Phase 2 Mini-Verify |
| `20260715-221629__...-ghcr_smoke__.json` | 3 | — | ~60310a1 | GHCR Docker-Smoke 1 |
| `20260715-222746__...-ghcr_smoke__.json` | 3 | — | ~60310a1 | GHCR Docker-Smoke 2 |

**Auch in docs/experiments/ vorhanden (archiviert):**
| Datei | Runs | Ergebnis | Commit | Zweck |
|-------|------|---------- |--------|-------|
| `2026-07-15-phase1-mini.json` | 30 | Phase-1 Mini-Verify (V2) | d0498cd | Phase 1 Mini (archiviert) |
| `2026-07-15-phase1-mini-v3.json` | 30 | Phase-1 Mini-Verify V3 | d0498cd | Phase 1 Mini-V3 (archiviert) |

---

## Kaltlauf 21. Juli (Output-only, noch nicht archiviert)

| Datei (output/) | Runs | Ergebnis | Commit | Zweck |
|-------|------|---------- |--------|-------|
| `20260721-031515__...-local_baseline_smoke__.json` | 3 | — | ~60310a1 | Baseline-Smoke vor Kaltlauf |
| `20260721-061226__...-local_subset_L__.json` | 180 | **31/60 = 51.7% Overall** (Base 60.9%, Hall 40%, Dis 52.9%) | 60310a1 | **Kaltlauf Subset-L** |

policy_aut_errors: 0/180. dis_55: 86 A2A-Turns, $3.10. Kosten: $28.55 gesamt / $0.158/Trial.

---

## Offene Punkte (Noch nicht nach docs/experiments/ kopiert)

Die folgenden JSONs sollten für die Archiv-Vollständigkeit nach `docs/experiments/` kopiert werden:
1. `20260714-051201__...-local_subset_L__.json` — **wichtigstes Ergebnis (Fix-Paket L)**
2. `20260715-042601__...-local_phase2_mini__.json` — Phase-2-Mini-Ergebnis
3. `20260721-061226__...-local_subset_L__.json` — Kaltlauf-Ergebnis

Befehl (nach User-GO):
```
cp output/track_1_agent_under_test/20260714-051201__* docs/experiments/
cp output/track_1_agent_under_test/20260715-042601__* docs/experiments/
cp output/track_1_agent_under_test/20260721-061226__* docs/experiments/
```
