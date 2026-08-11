"""Trace provenance: which code and which cartridge produced a verdict.

A number without its git revision and cartridge hash cannot be reproduced,
so every trace carries both. Best-effort by design: a wheel install has no
git checkout, and "unknown" is an honest answer there.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def git_rev() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parent,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"
