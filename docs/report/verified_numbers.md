# Verified Numbers — CAR-bench Report

**Erstellt:** 2026-07-23 (Daten-Audit vor Report-Deadline 26. Juli)
**Methode:** Alle Zahlen gegen Primärquellen (Result-JSONs, Handover, Devlog) geprüft.
**Regel:** Nur Zahlen, die gegen die Primärquelle verifiziert wurden. Alles andere gilt als unverifiziert.

---

## 1. Opus-4.6-Baseline (PUBLIC LEADERBOARD)

| Metrik | Wert | Quelle | Status |
|--------|------|--------|--------|
| Base Pass^3 | **0.80** | CAR-bench Leaderboard; Handover §1 Zeile 55; devlog ~1861 | ✓ KORREKT |
| Hall Pass^3 | **0.48** | s.o. | ✓ KORREKT |
| Dis Pass^3 | **0.46** | s.o. | ✓ KORREKT |
| Overall Pass^3 | **0.58** | s.o. | ✓ KORREKT |

**WARNUNG:** In devlog Zeile ~340, ~2633 und paper/claims.md stand die falsche Rotation (Base=0.58, Hall=0.80, Dis=0.48, Overall=0.46). Korrigiert 2026-07-23 (siehe devlog Korrektur-Eintrag).

**Metrik-Split:** Die Baseline-Zahlen sind auf dem Hidden Test-Split. Unsere Zahlen unten sind auf dem Train-Split. Direkter Vergleich ist methodisch problematisch.

---

## 2. I3-Modellvergleich (Lauf 20260711-182802)

| Metrik | Wert | Quelle | Status |
|--------|------|--------|--------|
| Sonnet-4-6 Pass^3 | **9/18 = 50%** | JSON `20260711-182802...i3_opus_compare.json` (per-task aggregiert) | ✓ VERIFIZIERT |
| Opus-4-6 Pass^3 | **8/18 = 44.4%** | JSON `20260711-182802...i3_opus_compare.json` | ✓ VERIFIZIERT |
| Tasks BEIDE 3/3 | **base_10, base_32, dis_0, hall_10, hall_16** (5 Tasks) | JSONs 20260711-103751 (Sonnet) + 20260711-182802 (Opus) | ✓ VERIFIZIERT |
| Tasks BEIDE 0/3 | dis_22 | s.o. | ✓ VERIFIZIERT |

**Commit-Stand:** beide Läufe auf Commit 66b116f (H-Fixes, 249 Tests, nach I1-Fix-Commit f34cc73 für I2/I3).
**Lauf-IDs:** Sonnet = 20260711-103751 (H-Verify), Opus = 20260711-182802 (I3).

**Abweichung vom Devlog:** Devlog sagt "base_10, base_32, hall_10, hall_16, dis_0" ✓ (Reihenfolge anders, Inhalt identisch).

---

## 3. policy_aut_errors = 0

| Metrik | Wert | Quelle | Status |
|--------|------|--------|--------|
| Runs in docs/experiments/ (JSON-Archiv) | **894 Runs** | 25 JSON-Dateien in docs/experiments/, Python-Script gegen alle geprüft | ✓ VERIFIZIERT |
| policy_aut_errors in 894 Runs | **0** | alle 25 JSONs geprüft | ✓ VERIFIZIERT |
| Zusätzliche Runs in output/ (nicht archiviert) | ~480 (Subset-L 180 + Kaltlauf 180 + sonstige ~120) | output/ JSONs | ✓ ALLE 0 |
| Gesamtsumme kumuliert | **~1.374 Runs** | docs/experiments/ + output/ verifizierte Runs | ✓ VERIFIZIERT |

**Quell-Dateien (docs/experiments/), Run-Counts:**
2026-07-03-smoke (3), b6-abnahme (15), b6-wiederholung (15), lauf3-oi011 (15), lauf4-abnahme (15),
e1-judge (9), oi016-c1 (9), oi016-fixAB (9), e2-dev (180), f1b-rerun (9), f2f4 (36), g3 (30),
j3 (27), j6-mini (3), k1-verify (90), k10-mini (6), k7-mini (6), generalprobe-delta (207),
phase1-mini-v3 (30), phase1-mini (30), stufe5-abnahme (15), stufe6-abnahme (9), h-verify (54),
i2-verify (18), i3-opus (54) = **894 total**.

**Hinweis:** Der Devlog nennt ">450 Runs" im Delta-Ergebnis (2026-07-13). Dieser Stand war korrekt; seither kamen weitere Runs dazu.

---

## 4. Generalisierungs-Zahlen

| Metrik | Wert | Quelle | Lauf-ID | Datum | Commit | Status |
|--------|------|--------|---------|-------|--------|--------|
| Delta-Lauf Overall (69 neue Tasks) | **44.9% = 31/69** | JSON 2026-07-13-generalprobe-delta.json | 20260713-040017 | 2026-07-13 | 883db7b (vor K-Fixes) | ✓ VERIFIZIERT |
| Delta Base | **50.0% = 15/30** | s.o. | s.o. | s.o. | s.o. | ✓ VERIFIZIERT |
| Delta Hall | **53.6% = 15/28** | s.o. | s.o. | s.o. | s.o. | ✓ VERIFIZIERT |
| Delta Dis | **9.1% = 1/11** | s.o. | s.o. | s.o. | s.o. | ✓ VERIFIZIERT |
| "Bekannte Tasks" 80% / 48/60 | **NICHT DIREKT GEMESSEN** | Extrapolation in devlog 2026-07-13 (E2 + H/I/J/K-Fixes) | — | — | — | ⚠️ UNVERIFIZIERT |

**Zu "Bekannte Tasks 80%":** Die Zahl 48/60 in der Generalprobe-Kombinationstabelle (devlog ~310-313) ist eine SCHÄTZUNG, kein direktes Messergebnis. Es gibt keinen einzelnen Run, der alle 60 "bekannten" Tasks nach allen K-Fixes misst. Für das Paper: entweder als Schätzung kennzeichnen oder durch Subset-L/Kaltlauf-Daten ersetzen.

---

## 5. Subset-L Kernwerte

### 5a. Glassbox-Lauf 14. Juli (Fix-Paket L, Commit 883db7b)

| Metrik | Task-gewichtet | Makro-Avg | Quelle | Lauf-ID |
|--------|---------------|-----------|--------|---------|
| Overall | **45.0% = 27/60** | 44.2% | JSON 20260714-051201 | 20260714-051201 |
| Base | 56.5% = 13/23 | — | s.o. | s.o. |
| Hall | 35.0% = 7/20 | — | s.o. | s.o. |
| Dis | 41.2% = 7/17 | — | s.o. | s.o. |
| policy_aut_errors | 0 / 180 Trials | — | s.o. | s.o. |
| Kosten | $39.50 total / $0.219/Trial | — | JSON | s.o. |

**Hinweis Makro-Avg:** 44.2% = (56.5 + 35.0 + 41.2) / 3. Für den Wert "~44.2% / 27 von 60" stimmt nur das "27 von 60" — 27/60 = 45.0%, nicht 44.2%.
**Handover-Fehler:** Handover Zeile 40 nennt "59 Tasks × 3 = 177 Runs" — FALSCH. JSON: 60 Tasks (23+20+17) × 3 Trials = 180 Runs.

### 5b. Kaltlauf 21. Juli (Phase 1+2 aktiv, Commit ~60310a1)

| Metrik | Task-gewichtet | Makro-Avg | Quelle | Lauf-ID |
|--------|---------------|-----------|--------|---------|
| Overall | **51.7% = 31/60** | 51.3% | JSON 20260721-061226 | 20260721-061226 |
| Base | 60.9% = 14/23 | — | s.o. | s.o. |
| Hall | 40.0% = 8/20 | — | s.o. | s.o. |
| Dis | 52.9% = 9/17 | — | s.o. | s.o. |
| Pass^2 = Pass^3 (Base) | **JA** (0.609 = 0.609) | — | JSON | s.o. |
| Pass^2 = Pass^3 (Hall) | **JA** (0.400 = 0.400) | — | JSON | s.o. |
| policy_aut_errors | 0 / 180 Trials | — | JSON | s.o. |
| Kosten | $28.55 total / $0.158/Trial | — | JSON | s.o. |
| dis_55 A2A-Turns | **86** (35+27+24) | — | JSON | s.o. |
| dis_55 Gesamtkosten | **$3.098 ≈ $3.10** | — | JSON | s.o. |

**Hinweis Makro-Avg:** 51.3% = (60.9 + 40.0 + 52.9) / 3. Der kommunizierte Wert "51.3%" ist der Makro-Avg; task-gewichtet sind es 51.7% = 31/60.

---

## 6. Kosten / Effizienz

| Metrik | Wert | Quelle | Status |
|--------|------|--------|--------|
| Kaltlauf 21.07. Kosten/Trial | **$0.158/Trial** (gesamt) | JSON 20260721-061226 ($28.55 / 180) | ✓ VERIFIZIERT |
| Glassbox 14.07. Kosten/Trial | **$0.219/Trial** (gesamt) | JSON 20260714-051201 ($39.50 / 180) | ✓ VERIFIZIERT |
| E2 Dev-Lauf Kosten/Trial | **$0.128/Trial** | Devlog 2026-07-09 ($23.11 / 180) | ✓ aus Devlog |
| dis_55 Kaltlauf: 86 LLM-Calls | ✓ VERIFIZIERT | JSON (a2a_turns: 35+27+24=86) | ✓ VERIFIZIERT |
| dis_55 Kaltlauf: ~$3.09 | ✓ → $3.10 | JSON ($3.098) | ✓ VERIFIZIERT |
| LLM-Calls/Turn Glassbox (2–5) vs. Baseline (1) | **NICHT AUS JSONS VERIFIZIERBAR** | Architektur-Analyse | ⚠️ AUS LOGS BELEGEN |

**Zu "$0.135–0.218/Trial" (kommunizierte Range):** Die $0.218 entspricht Glassbox 14.07. (agent-only: $0.218/trial). Die $0.135 hat keine Entsprechung in den verfügbaren JSONs — könnte aus dem E2-Lauf (~$0.128) oder einer Schätzung stammen. **Nicht verwendbar ohne Quelle.**

---

## 7. HEAD-Stand / Architektur-Schichten

### Git-Stand (aktuell)

| Commit | Inhalt | Datum |
|--------|--------|-------|
| **60310a1** (HEAD) | fix(submission): AGENT_CLASS + temperature Docker-path fixes | 2026-07-15 |
| c9cbbb8 | feat(glassbox): Phase 2 — Few-Shot-Anker + Multi-Stop-Enforcer | 2026-07-15 |
| d0498cd | Merge phase-1-severity — Phase 1: Severity + RC-Injection | 2026-07-15 |
| 883db7b | fix(glassbox): Fix-Paket L — 8 deterministische Guards | 2026-07-14 |
| d179738 | docs+scenario: Partial-Probing Hypothese (vor Lauf) | 2026-07-14 |

### Im HEAD aktiv (einschließlich Phase 1 + Phase 2)

**AKTIV:**
- Phase 0: Rollback-Tag v-known-good-L, B1/B2-Ziehung (keine Code-Änderungen)
- Phase 1 §3.1: SOFT/HARD Guard-Severity, SOFT re-draft loop (max 2), detect_action_promises, detect_inability_contradictions
- Phase 1 §3.2: RC-Tool-Injection (additiv-only) für REQUIRES_CONFIRMATION-Tools
- Phase 2 §4.1: Few-Shot-Anker in prompts/plan.py (3 Beispiele am Ende von _PLAN_SYSTEM)
- Phase 2 §4.2: Multi-Stop-Message-Enforcer in guard.py (enforce_multi_stop_message)
- Submission-Fix: AGENT_CLASS aus TOML-cmd → Elternprozess-Umgebung; temperature-Fix in Docker-Pfad

**NICHT ENTHALTEN in Commit 60310a1 (submitted Image):**
- Phase 3 (RCL): Code war als uncommitted Working-Tree-Änderung vorhanden (guard.py + state_machine.py + prompts/verify.py + Tests), aber zum Submission-Zeitpunkt NICHT committed — kein Bestandteil des submitted Docker-Images. Erst nach dem Audit committed.
- Checkpoint1/Final-Verify-Läufe: Szenarien existieren (TOMLs), aber keine Ergebnisse

### Docker-Image Digest sha256:b890c7...

**STATUS: NICHT VERIFIZIERBAR aus Dokumenten.** Der Digest sha256:b890c7... ist in keiner Datei in docs/, _local/ oder paper/ dokumentiert. Muss aus dem Submission-Portal oder dem Docker-Build-Log entnommen werden. 

**Wahrscheinlichster Stand:** HEAD-Commit 60310a1 (da dieser der "fix(submission)" ist).

### Mini-Verify-Status der Phasen

| Phase | Mini-Verify | Ergebnis | Im Devlog |
|-------|-------------|---------- |-----------|
| Phase 1 | V1, V2, V3 | V3 BESTANDEN (base_2 3/3, Hijack weg) | ✓ JA |
| Phase 2 | Mini (20260715-042601) | **NICHT BESTANDEN** (0/3 Enforcer-Ziele; hall_18 0/3 Regression) | ✗ FEHLT — nachgetragen 2026-07-23 |

---

## Nicht verifizierbare Zahlen (ohne zusätzliche Quellen)

| Behauptung | Problem |
|------------|---------|
| "+15pp über Opus-Baseline Overall" | Doppelt falsch: (a) falsche Baseline, (b) Train/Test-Vergleich |
| "+8pp über Opus-Baseline in Base" | Falsch: Unsere Train-Base 0.66 < Opus-Base 0.80 = -14pp |
| "80% auf bekannten/optimierten Tasks" | Kein direkter Verifikationslauf; Extrapolation aus E2 + K-Fixes |
| "Glassbox ~$0.135/Trial" | Keine JSON-Quelle gefunden; $0.128 (E2) ist ähnlich, aber anderer Lauf |
| "LLM-Calls/Turn Glassbox 2–5 vs. 1" | Nicht aus Result-JSONs extrahierbar; braucht Agent-Log-Analyse |
| Docker Image Digest sha256:b890c7... | Nicht in docs/ dokumentiert |
