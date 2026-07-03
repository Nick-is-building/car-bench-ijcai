# Referenzen & Methoden-Provenienz

## Primärquellen

- **CAR-bench Paper:** Kirmayr, Stappen, André (2026). "CAR-bench: Evaluating the Consistency and Limit-Awareness of LLM Agents under Real-World Uncertainty." arXiv:2601.22027
  - Übernommen: Reward-Struktur (UND-Gatter, Pass^3), Task-Typen, Baseline-Zahlen
- **tau-bench:** Yao et al. (2024). Vorgängerbenchmark für Tool-Agent-User-Interaktion.
  - Übernommen: Konzept des realistischen User-Simulators; in CAR-bench weiterentwickelt

## Methoden

- **Deterministische Schale / Provenienz-Ledger:** eigene Entwicklung; Kernidee aus formaler Verifikation (jede Behauptung braucht eine nachweisbare Quelle im Dialog)
- **State-Machine für LLM-Agenten:** Pattern aus reaktiven Agenten-Architekturen; hier auf Benchmarkstruktur zugeschnitten

## BibTeX

```bibtex
@misc{kirmayr2026carbench,
  title={CAR-bench: Evaluating the Consistency and Limit-Awareness of
         LLM Agents under Real-World Uncertainty},
  author={Kirmayr, Johannes and Stappen, Lukas and Andr{\'e}, Elisabeth},
  year={2026}, eprint={2601.22027}, archivePrefix={arXiv}, primaryClass={cs.AI}
}

@misc{yao2024taubench,
  title={tau-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains},
  author={Yao, Shunyu and others},
  year={2024},
  archivePrefix={arXiv}
}
```
