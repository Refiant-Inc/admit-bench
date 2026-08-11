"""Where the bundled cartridges live, for every install shape.

A repo checkout has them at admitbench/cartridges/; a wheel install ships
them as package data at the same relative spot. User-authored cartridges are
plain directories and resolve as given. `resolve_cartridge` therefore tries
the path as written first, then falls back to the bundled copy by name — so
`cartridges/cstr`, `cstr`, and an absolute path to your own cartridge all
find their four files.
"""

from __future__ import annotations

from pathlib import Path


def bundled_cartridge_root() -> Path:
    return Path(__file__).resolve().parent / "cartridges"


def agent_plugin_root() -> Path:
    """Directory containing Agent Plugins manifests (plugin.json, mcp.json, skills/)."""
    return Path(__file__).resolve().parent / "agent_plugin"


def bundled_cartridges() -> list[Path]:
    root = bundled_cartridge_root()
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if (p / "manifest.yaml").exists())


def resolve_cartridge(spec: str | Path) -> Path:
    path = Path(spec)
    if (path / "manifest.yaml").exists():
        return path
    bundled = bundled_cartridge_root() / path.name
    if (bundled / "manifest.yaml").exists():
        return bundled
    return path  # let the loader raise its usual compile-time error
