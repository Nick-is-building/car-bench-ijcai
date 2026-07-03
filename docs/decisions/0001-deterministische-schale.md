# ADR-0001: Deterministische Schale um LLM-Kern

Datum: 2026-07-03   Status: akzeptiert

## Kontext

CAR-bench bewertet mit Pass^3 (Task zählt nur bei dreimal 1.0). Stochastische LLM-Ausgaben produzieren Varianz, die Pass^3 systematisch unter das latente Können (Pass@3) drückt. Der Benchmark hat drei kritische Task-Typen: Base, Hallucination, Disambiguation.

## Entscheidung

Der Agent wird als deterministische Schale gebaut, die den LLM auf Formulierung beschränkt. Alle Entscheidungen (Capability-Check, Policy-Prüfung, Provenienz-Verifikation) laufen deterministisch; das LLM generiert nur Text innerhalb enger Prompts mit Temperatur 0.

## Begründung

- Pass^3 belohnt Konsistenz mehr als Spitzenleistung
- LLM-Judge (Gemini) ist Varianzquelle außerhalb unserer Kontrolle → Marge einbauen
- Deterministischer Entscheidungsrahmen macht falsche Antworten strukturell unmöglich statt unwahrscheinlich

## Alternativen verworfen

- Reines LLM-Prompting (wie Baseline-Agenten): zu viel Varianz für Pass^3
- Reasoning/Thinking-Modus: erhöht Latenz und Kosten, hilft nicht gegen Hallucination/Disambiguation-Struktur

## Konsequenzen

Höherer Implementierungsaufwand (7 Stufen). Risiko: deterministischer Check blockiert korrekte Antworten (Null-FP-Disziplin nötig, Base-Regression nach jedem Change).
