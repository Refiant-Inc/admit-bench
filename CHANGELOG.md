# Changelog

All notable changes to ADMIT Bench are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Paper reference: the framework is described in arXiv:2608.03866. Linked from
  the README (badge + header) and added to `CITATION.cff` as the preferred
  citation, so GitHub's "Cite this repository" resolves to the paper.
- **`admit_full_check` MCP tool** — preferred integrator path: builds
  ActionRecord + EvidenceLog + DecisionContext (with existing
  authority/mode/evidence hardening) and calls `run_gates` so T0–T4 land in
  one response (`admissible`, `hard_failed`, `violations`, optional
  `trajectories`). Split tools remain for debugging. Partner bootstrap via
  `plugin/scripts/bootstrap_venv.sh` and `plugin/INSTALL.md`.
- **CI / security parity:** `requirements.lock` + `pip-audit` workflow;
  concurrency on CodeQL/gitleaks; PR template security checklist; SECURITY.md
  documents required branch-protection checks (tests, gitleaks, codeql,
  pip-audit).
- **Agent Plugins bridge** (`plugin/`). Packages the gates to the Agent Plugins
  1.0.0 standard so an agent can check a proposed action before executing it,
  not only after. An MCP server (`admit_describe_contract`,
  `admit_check_record`, `admit_verify_consequence`, `admit_explain`) and a
  cartridge-authoring skill. Standard library only — no new dependency, and
  nothing in `admitbench/` imports it, so the deterministic path keeps zero
  network surface. `admit_verify_consequence` mirrors the T4 conditions in
  `gates.py` so the gate and the bench cannot drift. Answer-key sealing and the
  litmus pair are asserted through the MCP surface in
  `tests/unit/test_plugin_bridge.py`.
- Custom cartridges in the plugin: set `ADMITBENCH_CARTRIDGE_PATHS` to gate
  against your own plants. Opt-in, so the default server can only read what
  ships with the package. Names are resolved as bare basenames inside the
  declared roots, and a symlink pointing out of a root is refused.
  `admit_list_cartridges` reports what resolved and what failed to compile.
- **`admitbench verifier-sweep`** (`verifier_sweep.py`). Re-judges recorded
  actions with the T4 envelope scaled 0.8x to 1.5x and the horizon varied
  600-2400s, separating decisive rejections from knife-edge ones. Answers the
  reviewer question the supplement raises but had no script for: how much of
  the verdict is the physics and how much is the threshold. Read-only — it
  measures the gates without touching them.

### Changed

- Public snapshot withholds `results/*/traces.tar.gz` (raw model outputs)
  pending provider terms-of-service review; `REPRODUCE.md` in each results
  bundle documents how to request them. Aggregates in `results.json` and
  `episode_ledger.csv` still ship.
- `NOTICE` acknowledges the Layer-1 action-selection contribution from
  Javal Vyas (Imperial College London).

## [0.1.0] — 2026-07-21

Initial public release.

### Added

- **Admissibility kernel.** The action record (`record.py`), the rulebook
  (`rulebook.py`), and a single deterministic checker (`checker.py`) evaluated as
  an ordered gate chain T0–T6 (`gates.py`) with lexicographic scoring
  (`scoring.py`): any hard-gate failure yields `aggregate = None`, so unsafe
  behavior is ineligible for ranking rather than ranked low.
- **Consequence verifier.** Deterministic coupled mass/energy ODE models with
  fixed-step fourth-order Runge–Kutta (`physics.py`) for a jacket-cooled CSTR and
  a steam-reboiled distillation column.
- **Four-file cartridge format** and loader (`cartridge.py`): `manifest.yaml`,
  `system_graph.jsonl`, `safety_case_graph.jsonl`, `procedures_cases.jsonl`, with
  a `candidate → reviewed → validated` knowledge ladder.
- **Two reference cartridges:** `cstr` (15 cases) and `distillation` (10 cases),
  compiled from a 37-family literature-anchored cause→effect library.
- **Robustness layer.** The Caution Monotonicity Test (`ablation.py`,
  `monotonicity.py`): degraded evidence may never justify a bolder, more
  confident, or less recoverable action.
- **Harness.** Providers, parser, prompts, runner, and three-table reports; a
  self-diagnosing doctor and violation-code explainer (`doctor.py`); and a full
  CLI (`admitbench run | call | dashboard | paired | cmt | sweep | doctor | …`).
- **Interactive dashboards.** Self-contained HTML with a run-composition donut,
  per-gate leakage, an admissibility-vs-caution exhibit, a specimen viewer, and a
  Layer-1 view; confidence tooling in `sweep.py`.
- **Reproducibility artifact** (`scripts/build_artifact.py`): manifest, episode
  ledger, redacted trace bundle, and a `REPRODUCE.md`.
- **First evaluation** (`results/eval_2026-07-21/`): twelve frontier and
  open-weight models, the gate benchmark run twice plus a Layer-1 floor benchmark.
- Project documentation (`docs/`), `SECURITY.md`, `CITATION.cff`, and `NOTICE`.

### Changed

- Licensed under the **Apache License 2.0** (see `LICENSE` and `NOTICE`).

### Non-claims

- ADMIT Bench is an evaluation standard, not a functional-safety certification.
  It produces reproducible, auditable evidence about advisory action behavior; it
  does not authorize deployment and does not replace HAZOP, LOPA, or SIL
  verification.

[Unreleased]: https://github.com/Refiant-Inc/admit-bench/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Refiant-Inc/admit-bench/releases/tag/v0.1.0
