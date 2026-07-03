# Claims-to-Evidence Table

Every quantitative claim in main.tex must have an entry here before it goes in the paper.
No number in the paper without a timestamped artifact.

| Claim | Belegendes Artefakt (Commit / Datei / Lauf) | Ort im Paper (`\label`) |
|-------|---------------------------------------------|-------------------------|
| Baseline: Claude Opus 4.6 Pass^3 = 0.58/0.80/0.48/0.46 | Public knowledge (CAR-bench leaderboard) | `tab:results` |
| Hallucination 4/4 Stabilitätsläufe Pass^1 = 100 % | `docs/experiments/2026-07-03-stufe3-smoke.md`, commit 589db23 | (Stufe-3 Evidenz) |
| *(Kalibrierungslauf 10. Juli)* | TODO nach Lauf | `tab:results` |
| *(Finalwertung 19. Juli)* | TODO nach Lauf | `tab:results` |
