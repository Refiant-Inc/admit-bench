# ADMIT Bench — evaluation summary (2026-07-21) (12 models, dual-run)

Run: `runs/eval_2026-07-21/gate_A`, `runs/eval_2026-07-21/gate_B` (gate, identical config, run twice),
`runs/eval_2026-07-21/layer1` (Layer-1, once). Temperature 0. OpenRouter spend **$10.43** of a $18 cap;
protea free (self-hosted). 0 run errors.

## Models (12)
- **OpenRouter (9):** anthropic/claude-haiku-4.5, anthropic/claude-sonnet-5, openai/gpt-5.5,
  openai/gpt-5.4-mini, google/gemini-2.5-flash, meta-llama/llama-3.3-70b-instruct,
  mistralai/mistral-small-3.2-24b-instruct, qwen/qwen3-30b-a3b-instruct-2507, deepseek/deepseek-v3.2
- **Refiant (3):** protea-1, protea-5, protea-10

## Gate benchmark — admissibility (base cases, 25/model)

| Model | Adm. run A | Adm. run B | A↔B flips | first-fail T2 / T4 |
|---|---|---|---|---|
| google/gemini-2.5-flash | 80% | 80% | 0 | 1 / 4 |
| anthropic/claude-haiku-4.5 | 76% | 76% | 0 | 3 / 3 |
| anthropic/claude-sonnet-5 | 76% | 80% | 1 | 1 / 4 |
| qwen/qwen3-30b | 72% | 64% | 4 | 5 / 2 |
| openai/gpt-5.4-mini | 68% | 60% | 2 | 7 / 1 |
| openai/gpt-5.5 | 64% | 68% | 1 | 2 / 6 |
| refiant/protea-10 | 64% | 64% | 0 | 4 / 5 |
| refiant/protea-5 | 64% | 64% | 0 | 4 / 5 |
| refiant/protea-1 | 60% | 64% | 3 | 4 / 6 |
| meta-llama/llama-3.3-70b | 60% | 52% | 4 | 8 / 1 |
| deepseek/deepseek-v3.2 | 48% | 56% | 2 | 3 / 6 |
| mistralai/mistral-small-3.2 | 44% | 56% | 5 | 9 / 4 |

Admissibility spans **44%–80%**. The wider low end vs. the v4 run (60–76%) is the added
open-weight models; frontier models cluster 64–80%.

## The central finding reproduces and sharpens
Pooled over all 12 models, hazard-active, diagnosis-correct base outputs:

- **P(inadmissible | diagnosis correct) = 33%** — run A 49/148, run B 49/150. Identical to 2 sig figs across both runs.
- **~96% of those failures land at the consequence gate T4** (run A 47/49, run B 48/49).

This corroborates the v4 result (31%, 100% at T4): models diagnose the hazard and then, about a
third of the time, propose an action the physics rejects — magnitude too weak or deferral too slow.

## Run-to-run sanity (the "run twice" check)
- **7.3% per-verdict flip rate** — 22 of 300 shared base cells changed admissible↔inadmissible
  between two identical temperature-0 runs. Source is provider-side nondeterminism (OpenRouter
  can route the same slug to different upstream providers/quantizations; some kernels are
  non-deterministic).
- **Aggregate metrics are stable**: the pooled paired stat is 33%/33%; per-model admissibility moves
  by 0–12 points, most ≤4.
- **Determinism varies by model**: protea-5, protea-10, haiku-4.5, gemini-2.5-flash showed **0 flips**;
  mistral (5), llama (4), qwen (4) were noisiest. Self-hosted protea is the most reproducible.

## Layer-1 (multiple-choice floor, 420/model, 5,040 total)
- **11 of 12 models never select the explicitly unsafe option** (safe ≈ 100%, as in v4).
- **meta-llama/llama-3.3-70b is the sole floor-breaker**: all 5 unsafe selections in the whole run are
  its (1.19%), plus ~6.7% invalid/unsupported picks → 92.1% safe vs ≈100% for everyone else.
- Layer-1 scores 0.90 (llama) to 0.99; the format still hides the parameter/timing failures the gate
  benchmark exposes.

## Reproducibility recommendations
1. **Report aggregates, not single-run per-case verdicts.** With a 7.3% per-verdict flip rate, any
   claim about a specific case's verdict needs repetition. Headline metrics (admissibility, the paired
   gap) are stable and should be reported with an observed run-to-run band (≥2 runs).
2. **Pin the upstream provider, not just the slug.** OpenRouter routes a slug across providers and
   quantizations; capture the served provider (returned per response) and pin via routing preferences.
   This is the largest single reproducibility gap.
3. **consistency@k for per-episode claims.** For any case cited individually, sample k≥3–5 and report
   the modal verdict + agreement rate.
4. **Extend the manifest.** `scripts/build_artifact.py` already captures model IDs, dates, git-rev,
   cartridge content-hashes, decoding (temp/max_tokens), ledger, and traces. Add **top_p, seed, and the
   OpenRouter-served upstream provider** per call.
5. **Prefer pinned/self-hosted endpoints for the reference numbers.** Protea (self-hosted) showed 0
   flips; it and pinned frontier snapshots give the cleanest reproducibility.
6. **Layer-1 once is enough.** It is near-deterministic and saturated (0.10% unsafe overall); the gate
   benchmark is where run-to-run variance lives and where the double-run matters.

## Caution Monotonicity Test (CMT)
24 evidence-degradation pairs per model, both runs (remove / stale / quarantine / untrust). Computed
with the repo's own `compare_caution`, so the protective-move and decorative-ablation exemptions are
honored.

- **Pooled run A: 83.7% pass, 16.3% violation** — 47 violations over 288 comparable pairs. Models
  slide the wrong way on ~1 in 6 degradations. Failure modes: **became bolder** under less evidence
  (26), **inflated confidence** under less evidence (17), **dropped the recovery plan** while staying
  bold (13).
- **The caution ranking is not the admissibility ranking** — the central cross-finding.

| Model | Base admissibility | CMT pass (run A) | Main CMT failure |
|---|---|---|---|
| claude-haiku-4.5 | 76% | **96%** | 1 confidence-inflation |
| claude-sonnet-5 | 76% | 96% | isolated |
| protea-1 / 5 / 10 | 60–64% | 96–100% | isolated |
| qwen3-30b | 72% | 79% | bolder |
| deepseek-v3.2 | 48% | 79% | bolder |
| **gemini-2.5-flash** | **80% (top)** | **71%** | recovery-weakened ×6, bolder ×5 |
| gpt-5.5 | 64% | 67% | recovery-weakened ×6 |
| mistral-small-3.2 | 44% | 67% | confidence-inflation |
| gpt-5.4-mini | 68% | **62% (worst)** | bolder ×8 |

- **gemini-2.5-flash tops base admissibility (80%) but is among the weakest on caution (71%)**;
  claude-haiku is the reverse (76% admissible, 96% caution-monotone). This reproduces the
  "base competence and caution measure different properties" result (the AAAI paper's RQ3) on an
  independent model set.
- **CMT validity:** it is metamorphic — the base-vs-degraded comparison is the test, needing no answer
  key and no simulator — and it is immune to prompt caching (base and degraded are different inputs).
  It shares the run-to-run nondeterminism caveat: violation counts move between runs for stochastic
  models (claude-sonnet 1→5, gpt-5.5 8→4, llama 1→4), stable for others (gemini 7→7, haiku 1→1).
