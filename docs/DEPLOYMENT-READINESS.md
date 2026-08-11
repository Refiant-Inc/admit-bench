# Deployment readiness — ADMIT Bench

Where this can be used today, what blocks production-adjacent claims, and what
operators must configure. Written for Refiant Inc private deployment and partner
pilot rollout.

---

## 1. The blunt answer

**ADMIT Bench must not sit in the control path of a real plant.** It is an
**evaluation and rehearsal** framework. A passing admissibility check is **not**
plant authorization.

What **is** ready today:

- Offline benchmark runs (`admitbench run`, stub provider, published cartridges)
- Research reproduction (arXiv:2608.03866 reference implementation)
- Agent Plugins MCP gate on **stdio** for rehearsal and partner integration
- Self-contained HTML dashboards from stored traces

What is **not** ready without additional platform work:

- Treating MCP as enforcement without platform-attested evidence
- Public competitive leaderboards on shipped research cartridges (answer keys in wheel)
- Substituting for HAZOP, LOPA, SIL verification, or accountable human approval

---

## 2. Readiness by use

| Use | Ready? | Notes |
|---|---|---|
| Offline evaluation / paper reproduction | **Yes** | Install wheel or editable; `admitbench doctor` must pass |
| Partner Agent Plugins rehearsal (loopback) | **Yes** | `pip install "admit-bench[plugin]"`; point client at `admitbench plugin-root` |
| Shadow logging of agent proposals | **Conditional** | Requires `ADMITBENCH_REQUIRE_PLATFORM_EVIDENCE=1` and host-injected evidence |
| Live enforcement on equipment | **No** | Out of scope; use platform gate + signed cartridges + WASM path (see admit-wasm) |
| Public PyPI “plugin-only” SKU without oracles | **No** | Research wheel still ships case keys in `procedures_cases.jsonl` |

---

## 3. Install shapes

### Evaluation (wheel)

```bash
pip install admit-bench
admitbench doctor
admitbench run --provider stub --model oracle --cartridge cstr
```

### Agent Plugins gate (wheel + manifests)

```bash
pip install "admit-bench[plugin]"
PLUGIN_DIR="$(admitbench plugin-root)"
# Point your Agent Plugins client at $PLUGIN_DIR
# mcp.json launches: admit-bench-mcp (stdio)
```

### Developer checkout

```bash
pip install -e ".[dev,plugin]"
./plugin/scripts/bootstrap_venv.sh   # optional isolated venv
# Client may use repo plugin/ or packaged admitbench/agent_plugin/
```

---

## 4. MCP trust boundary (live gate posture)

The MCP server speaks JSON-RPC over **stdio**. Treat these as part of the trust
boundary:

| Control | Env var | Purpose |
|---|---|---|
| Platform evidence only | `ADMITBENCH_REQUIRE_PLATFORM_EVIDENCE=1` | Refuse caller-supplied evidence lists |
| Authority clamp | `ADMITBENCH_AUTHORITY_SCOPE` | Comma-separated allowlist intersected with cartridge manifest |
| Mode binding | `ADMITBENCH_MODE` | Host overrides caller mode (blocks mode laundering) |
| Private cartridges | `ADMITBENCH_CARTRIDGE_PATHS` | Opt-in roots; basename-only resolution with symlink containment |

**Integrator loop:** `admit_describe_contract` once → **`admit_full_check`** before
every commit-class action → `admit_explain` on violations.

Split tools (`admit_check_record`, `admit_verify_consequence`) are for staged
debugging only.

Resource limits (DoS): `horizon_s` ≤ 3600s, evidence entries ≤ 256, stdio line
≤ 1 MiB.

---

## 5. Residual risks (tracked)

| ID | Risk | Mitigation |
|---|---|---|
| R1 | Research wheel bundles case oracles | MCP tools do not leak; use private cartridges for competitive eval |
| R2 | Default MCP accepts caller evidence | Enable platform evidence mode for enforcement |
| R3 | Canonical ledger encoding (D-13) | Re-mint attestations after upgrade; old traces may fail T0 replay |
| R4 | Branch protection | Org must require CI checks listed in `SECURITY.md` |
| R5 | D-13 in-component ledger verify (ecosystem) | Platform-side verification until WASM D-13 closes |

---

## 6. Required CI checks before merge

Enable on `main` in GitHub (Settings → Branches):

| Check | Workflow |
|---|---|
| `test` | `tests.yml` |
| `wheel-smoke` | `tests.yml` |
| `plugin-smoke` | `tests.yml` |
| `gitleaks` | `gitleaks.yml` |
| `Analyze (python)` | `codeql.yml` |
| `audit` | `pip-audit.yml` |
| `release-evidence` | `release-evidence.yml` |

Also: CODEOWNERS review on `admitbench/`, `plugin/`, `.github/`, `SECURITY.md`.

---

## 7. Security contact

Report vulnerabilities privately to **team@refiant.ai** or via GitHub private
advisories. See [SECURITY.md](../SECURITY.md).
