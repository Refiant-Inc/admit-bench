"""Project configuration: one small JSON file, written by `admitbench init`.

Precedence everywhere: explicit flag > admitbench.config.json > built-in
default. The config never stores API keys — those stay in the environment.
"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_FILE = "admitbench.config.json"

DEFAULTS = {
    "provider": "stub",
    "model": "oracle",
    "cartridge": "cartridges/cstr",
}

DEFAULT_MODELS = {
    "refiant": "protea-5",
    "openrouter": "anthropic/claude-sonnet-4",
    "anthropic": "claude-sonnet-5",
    "custom": "",
    "stub": "oracle",
}


def config_path(directory: str | Path | None = None) -> Path:
    return Path(directory or ".") / CONFIG_FILE


def load_config(directory: str | Path | None = None) -> dict:
    path = config_path(directory)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_config(config: dict, directory: str | Path | None = None) -> Path:
    path = config_path(directory)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def resolve(flag_value, key: str, directory: str | Path | None = None):
    """flag > config file > built-in default."""
    if flag_value:
        return flag_value
    config = load_config(directory)
    if config.get(key):
        return config[key]
    return DEFAULTS.get(key)
