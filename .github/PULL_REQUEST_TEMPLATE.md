<!-- Thanks for contributing to ADMIT Bench. -->

## What and why

Describe the change and the motivation. Link any related issue.

## Type

- [ ] Bug fix
- [ ] New cartridge / cartridge content
- [ ] Kernel / gates / scoring (deterministic core)
- [ ] Harness / dashboard / docs
- [ ] Other

## Checklist

- [ ] `python -m pytest` is green
- [ ] `admitbench doctor` passes (litmus pair + answer-key seal intact)
- [ ] No nondeterminism, network calls, or model dependence added to the
      deterministic benchmark path (loader → gates → physics → scoring)
- [ ] Any new `validated` cause→effect knowledge has domain review noted
- [ ] `CHANGELOG.md` updated under `[Unreleased]` (for user-visible changes)
- [ ] I agree my contribution is licensed under Apache-2.0

## Security checklist

- [ ] Secret scan clean (no new credentials, tokens, or `.env` contents;
      gitleaks CI expected green)
- [ ] Plugin seal intact: no tool or docs path exposes case `oracle`,
      `diagnosis_accept`, or `acceptable_actions` to callers
- [ ] No oracle leak in logs, fixtures, or PR description (paste only
      redacted records / AAS codes)
