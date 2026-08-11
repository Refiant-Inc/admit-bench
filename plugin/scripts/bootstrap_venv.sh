#!/usr/bin/env bash
# Partner install: create an isolated venv for the Agent Plugins MCP bridge.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATA_DIR="${PLUGIN_DATA:-$ROOT/.plugin-data}"
VENV="$DATA_DIR/venv"

mkdir -p "$DATA_DIR"
python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip
python -m pip install -e "${ROOT}[dev,plugin]"

PLUGIN_DIR="$("$VENV/bin/admitbench" plugin-root)"

cat <<EOF

Bootstrap complete.

  venv: $VENV
  python: $(command -v python)
  Agent Plugins directory: $PLUGIN_DIR

Point your Agent Plugins client at:

  $PLUGIN_DIR

(or the checkout path $ROOT/plugin during development)

The packaged mcp.json launches \`admit-bench-mcp\` over stdio.

See plugin/INSTALL.md for the short partner install checklist.
EOF
