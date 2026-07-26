# CAR-bench IJCAI 2026 — Glassbox Agent

**Competition:** CAR-bench Challenge, IJCAI-ECAI 2026  
**Track:** Open Track 1  
**Participant:** Nick Wagner (Independent Researcher)  
**Submitted:** July 2026

## What this is

A deterministic glass-box shell around Claude Sonnet 4.6 for the
CAR-bench automotive voice assistant benchmark. Core thesis: under
Pass³ (all three trials must succeed), determinism is metric-optimal.

The agent wraps the LLM in a 7-stage pipeline with a provenance
ledger. All capability checks, policy enforcement, and claim
verification run as deterministic predicates over the ledger.
The LLM is responsible only for text generation.

This is the second iteration of a research line on structural
verification — see also [ast-guard](https://github.com/Nick-is-building/ast-guard).

## Key Results

| Configuration | Overall Pass³ | Base | Hall. | Dis. |
|---|---|---|---|---|
| Baseline (naked Sonnet) | 51.7% | 60.9% | 40.0% | 52.9% |
| Glassbox pre-fix | 45.0% | 56.5% | 35.0% | 41.2% |
| Submitted agent | *pending — official scores July 29* | | | |

Model ablation (18 tasks): Sonnet 9/18 vs. Opus 8/18 under identical
shell — architecture, not model scale, determines the outcome.
Policy violations: 0 across 894 runs.

## Repository Structure

- `src/track_1_agent_under_test/` — Agent source code
- `docs/` — Architecture Decision Records, experiment logs, devlog
- `docs/report/verified_numbers.md` — Verified experiment data
- `scenarios/` — Evaluation scenario configs
- `paper/` — Technical report (PDF added after July 29)

## Research Line

1. [ast-guard](https://github.com/Nick-is-building/ast-guard) —
   Deterministic AST gate against reward hacking in RL training
2. This repository — Structural verification at agent runtime

## Technical Report

4-page IJCAI-format report submitted to CAR-bench.
Will be added here after official results release (July 29, 2026).
