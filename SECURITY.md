# Security Policy

## Reporting a vulnerability

If you discover a security issue in ADMIT Bench, please report it privately to
**team@refiant.ai** rather than opening a public issue. Include steps to
reproduce and, where relevant, the affected version or commit.

Alternatively, use GitHub's private vulnerability reporting / security advisory
flow for this repository (Security → Advisories / "Report a vulnerability") if
you prefer not to email.

We aim to acknowledge reports within **3 business days** and will coordinate a
fix and disclosure timeline with you.

## Supported versions

Security fixes are applied to the tip of `main` and the current `0.1.x` line.
Older releases are not patched separately; upgrade to a supported revision.

## Scope and intended use

ADMIT Bench is an **evaluation** framework. It is not a functional-safety
system and must not be placed in the control path of a real plant. It does not
replace HAZOP, LOPA, SIL verification, alarm rationalization, or accountable
human approval. Do not use it to justify autonomous-control deployment without
site-specific safety review.

## MCP plugin trust boundary

The Agent Plugin MCP server (`admitbench.mcp.server`, entry point `admit-bench-mcp`)
speaks JSON-RPC over **stdio** with a local host process. Treat the host
configuration (env vars, cartridge path allowlist, who can launch the server) as
part of the trust boundary.

Deployment posture and blocked claims: [docs/DEPLOYMENT-READINESS.md](docs/DEPLOYMENT-READINESS.md).

- The tools **gate proposed actions only**. They do not execute plant commands.
- A **pass is not plant authorization**. Admissibility of a record against a
  cartridge model is not permission to act on a real process.
- Callers must not be able to invent authority outside the cartridge manifest
  (or a tighter platform allowlist). Evidence trust is derived from the
  cartridge trust map, never from caller-supplied fields. See `plugin/README.md`
  for platform env vars (`ADMITBENCH_AUTHORITY_SCOPE`, `ADMITBENCH_MODE`,
  `ADMITBENCH_REQUIRE_PLATFORM_EVIDENCE`).

## Credentials

The benchmark reads provider API keys from the environment (or a local, ignored
`.env`). Never commit real keys. `.env` is listed in `.gitignore`; the tracked
template is `.env.example`, which contains placeholders only.

## Branch protection (org / repo settings)

GitHub Actions alone do not block merges. The organization (or repository admin)
**must enable branch protection on `main`** that requires the following status
checks before merge:

| Required check | Workflow / job |
|---|---|
| `tests` / `test` | `.github/workflows/tests.yml` (matrix unit/base/stress + doctor) |
| `tests` / `wheel-smoke` | `.github/workflows/tests.yml` (clean-venv wheel install) |
| `gitleaks` / `gitleaks` | `.github/workflows/gitleaks.yml` |
| `codeql` / `Analyze (python)` | `.github/workflows/codeql.yml` |
| `pip-audit` / `audit` | `.github/workflows/pip-audit.yml` (`pip-audit --strict` on `requirements.lock`) |
| `tests` / `plugin-smoke` | `.github/workflows/tests.yml` (wheel + MCP `tools/list`) |
| `release-evidence` / `release-evidence` | `.github/workflows/release-evidence.yml` (wheel + CycloneDX SBOM) |

Also recommend: dismiss stale approvals on new commits, require a CODEOWNERS
review for `admitbench/`, `plugin/`, and `.github/`, and disallow force-pushes
to `main`.

### CodeQL / Code Security

Private repositories need **GitHub Code Security** enabled to upload CodeQL results to the Security tab. Until then, CI runs CodeQL with `upload: never` so analysis still executes without failing on the upload API. After enabling Code Security on the org/repo, set `upload: always` in `.github/workflows/codeql.yml`.
