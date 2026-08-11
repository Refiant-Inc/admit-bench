# Contributing to ADMIT Bench

Thanks for your interest in ADMIT Bench. This guide covers how to set up, what
the invariants are, and how to contribute code, cartridges, or model results.

By contributing you agree that your contributions are licensed under the
project's [Apache License 2.0](LICENSE).

## Setup

```bash
git clone https://github.com/Refiant-Inc/admit-bench
cd admit-bench
pip install -e ".[dev]"
admitbench doctor          # offline self-check: litmus pair, answer-key seal, physics
```

Everything runs fully offline with the stub provider (`--provider stub --model
oracle`); no API key is needed to develop or test.

## Running the tests

```bash
python -m pytest              # everything (should be all green before you push)
python -m pytest tests/base   # invariants every episode must satisfy
python -m pytest tests/stress # metamorphic and gaming tests
python -m pytest tests/unit   # per-module unit tests
```

CI runs the same suite plus a wheel-install smoke test across Linux, macOS, and
Windows on Python 3.10–3.13.

## The one rule that cannot break

ADMIT is admissibility-first. Two invariants are non-negotiable, and the doctor
checks them on every run:

1. **The litmus pair never inverts.** A correct answer reached by skipping a
   required step must **fail**; a justified escalation on corrupted evidence must
   **pass**. If a change flips either verdict, it is wrong.
2. **The answer key stays sealed.** No oracle action, acceptable-action set,
   reference diagnosis, load-bearing label, timing, or gate outcome may appear in
   a model's prompt. `tests/base/test_sealing.py` mutates the sealed fields and
   requires the rendered prompt to stay byte-identical.

A hard-gate failure must always yield `aggregate = None` — never a low score.

## The studio / benchmark boundary

- The **studio** (cartridge authoring: `builder.py`, `ingest`, `hypotheses.py`)
  may use models and heuristics freely.
- The **benchmark** (everything from the compiled four files onward: loader,
  gates, physics, scoring) must be **deterministic and replayable**. Do not
  introduce nondeterminism, network calls, or model dependence into that path.

## Contributing a cartridge

Cartridges are four files (`manifest.yaml`, `system_graph.jsonl`,
`safety_case_graph.jsonl`, `procedures_cases.jsonl`). See
[docs/CARTRIDGE_AUTHORING.md](docs/CARTRIDGE_AUTHORING.md).

- New cause→effect knowledge enters as `review_status: candidate`. Only
  `reviewed` or `validated` entries may power hard gates. Do not ship a cartridge
  that claims `validated` without domain review.
- Validate before opening a PR: `admitbench validate <path>` must pass, and
  `admitbench doctor` must stay green.
- Open a **New cartridge** issue first so a maintainer can pair on review scope.

## Submitting model results

Use the **Results submission** issue template. Include: exact model identifier
and provider, decoding parameters, the code git-rev and cartridge content-hashes
(from `scripts/build_artifact.py`), and the redacted trace bundle. Results
without a reproducible manifest cannot be listed.

## Pull requests

1. Branch from `main`; keep changes focused.
2. Match the surrounding style; new code should read like the code around it.
3. `python -m pytest` and `admitbench doctor` must pass.
4. Update `CHANGELOG.md` under `[Unreleased]` for user-visible changes.
5. Describe **what** and **why**; link the issue.

Security issues: do **not** open a public issue — see [SECURITY.md](SECURITY.md).
Questions: **team@refiant.ai**.
