# Partner install (Agent Plugins)

## Requirements

- Python **≥ 3.10**
- `pip install "admit-bench[plugin]"` (PyPI or wheel) **or** a git checkout

## Production install (no checkout)

```bash
pip install "admit-bench[plugin]"
PLUGIN_DIR="$(admitbench plugin-root)"
# Point your Agent Plugins client at $PLUGIN_DIR
```

The directory contains `plugin.json`, `mcp.json`, and `skills/`. The MCP server
is the `admit-bench-mcp` console script (stdlib JSON-RPC over stdio).

## Developer / checkout install

From the repo root:

```bash
./plugin/scripts/bootstrap_venv.sh
# or: pip install -e ".[dev,plugin]"
```

Point the client at either:

- `$(admitbench plugin-root)` — packaged manifests from the install, or
- `plugin/` in the checkout — same schema, convenient for editing

Optional: set `PLUGIN_DATA` so the bootstrap venv lives outside the checkout.

## Trust boundary

For live-gate posture, set `ADMITBENCH_REQUIRE_PLATFORM_EVIDENCE=1` and configure
`ADMITBENCH_AUTHORITY_SCOPE` / `ADMITBENCH_MODE`. See `plugin/README.md` and
`docs/DEPLOYMENT-READINESS.md`.

Prefer **`admit_full_check`** for the complete T0–T4 path before commit-class actions.
