# Layer 1: industrial action selection

This directory contains the synthetic draft multiple-choice layer of
ADMIT Bench. It tests whether a model chooses a correct and safe immediate
response to an ambiguous heavy-industry fault.

Layer 1 is intentionally separate from `cartridges/`. The existing cartridges
evaluate complete, auditable action records through deterministic gates. These
Layer-1 files contain question/answer examples and hidden labels; they are not
validated plant cartridges and must not be used as operational guidance.

## Layout

```text
layer1_action_selection/
  README.md
  docs/
    BLUEPRINT.md       task definition and target size
    GENERATION.md      generator and scorer notes
  scripts/
    generate_layer1_scenarios.py
    score_layer1_choice.py
  data/
    draft/             synthetic JSONL containing prompts and hidden labels
    summaries/         generated distribution summaries
```

The two draft sets contain 420 and 2,100 scenarios. Both are generated from
the same 42 templates: six fault families in each of seven domains. The larger
set adds variants; it does not add independently reviewed situations.

## Data boundary

Each stored scenario is a private evaluator record. Before sending a scenario
to a model, construct a public prompt containing only:

- scenario ID, context, task, time window, authority, plant state, alarms, and
  candidate actions;
- hard constraints only in the benchmark setting where they are meant to be
  visible; and
- the expected response schema, without example answers.

Never expose `action_labels`, `preferred_actions`, `safe_fallbacks`,
`unsafe_actions`, or `curation_status` to the evaluated model. Keep the final
test labels private and access-controlled.

## Current status

`synthetic_draft_requires_domain_review` is the correct status for all current
rows. The files are useful for harness development, evaluator tests, and pilot
experiments. They are not ready for safety claims or a public leaderboard.

## Local smoke checks

From the repository root:

```powershell
py benchmarks/layer1_action_selection/scripts/generate_layer1_scenarios.py `
  --variants-per-template 1 `
  --seed 42 `
  --out-jsonl "$env:TEMP/layer1_smoke.jsonl" `
  --out-summary "$env:TEMP/layer1_smoke_summary.csv"

py benchmarks/layer1_action_selection/scripts/score_layer1_choice.py `
  --scenario benchmarks/layer1_action_selection/data/draft/industrialbench_layer1_generated_420.jsonl `
  --scenario-id CSTR_101_L1_001_01 `
  --model-output C
```

## Release path

1. Freeze and review the 42 source templates before reviewing thousands of
   near-duplicate variants. For every action, require an expert decision and
   written basis for safety, correctness, authority, and procedure compliance.
2. Replace generic constraints with traceable sources: approved SOP revision,
   HAZOP safeguard, alarm philosophy, operating envelope, or governing rule.
3. Add cases where the right response changes with severity, authority,
   evidence trust, timing, or equipment state. A variant is valid only if its
   hidden label remains correct after the values change.
4. Split by template or fault family, never randomly by generated row. Variants
   of one template must not occur across train and test because that leaks the
   answer pattern.
5. Build a runner that strips private fields, calls the model, validates the
   response schema, scores the selected action, and stores prompt/model/version
   metadata for replay.
6. Implement the metrics in `docs/BLUEPRINT.md`. Score rationale grounding
   separately from action safety; the present scorer evaluates the choice only.
7. Add adversarial and abstention tests: corrupted sensors, conflicting alarms,
   missing procedures, insufficient authority, prompt injection in operator
   notes, escalating production pressure, and unsupported free-form actions.
8. Promote reviewed scenarios into the full cartridge/action-record layer when
   you want to test evidence provenance, procedure order, reversibility,
   recovery planning, and projected physical consequences.

## Minimum publication gate

A release should identify domain reviewers and inter-rater agreement, document
label adjudication, publish dataset and metric cards, disclose synthetic-data
limitations, keep a held-out private test set, and demonstrate that trivial
baselines cannot exploit action wording, answer position, domain, or template
identity.
