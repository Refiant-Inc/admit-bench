#!/usr/bin/env python3
"""Build a sanitized public release tree from the current git HEAD.

Exports tracked files via ``git archive``, applies the approved exclusion
manifest, and optionally initializes a one-commit public repository.

Usage:
  python scripts/build_public_snapshot.py /tmp/admit-bench-public
  python scripts/build_public_snapshot.py /tmp/admit-bench-public --init-git
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "release_audit" / "public_content_manifest.csv"

# Default exclusions for paths marked "Do not ship" or PRIVATE_INTERNAL in manifest.
DEFAULT_EXCLUDE_PREFIXES = (
    "docs/_agent_reports/",
    "docs/security/",
    "docs/ecosystem/",
    "release_audit/",
)

DEFAULT_EXCLUDE_PATHS = (
    "Contracts/",
    "Fixtures/",
)


def _load_manifest_excludes() -> set[str]:
    excludes: set[str] = set(DEFAULT_EXCLUDE_PATHS)
    if not MANIFEST.is_file():
        return excludes
    with MANIFEST.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            action = (row.get("action") or "").lower()
            path = (row.get("path") or "").strip()
            if not path or path.endswith("/"):
                if "do not ship" in action:
                    excludes.add(path.rstrip("/") + "/")
                continue
            if "do not ship" in action:
                excludes.add(path)
    return excludes


def _should_exclude(rel: str, excludes: set[str]) -> bool:
    rel = rel.replace("\\", "/")
    for prefix in DEFAULT_EXCLUDE_PREFIXES:
        if rel.startswith(prefix):
            return True
    for item in excludes:
        item = item.replace("\\", "/")
        if item.endswith("/") and rel.startswith(item):
            return True
        if rel == item.rstrip("/"):
            return True
    return False


def export_snapshot(out_dir: Path) -> list[str]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    proc = subprocess.run(
        ["git", "archive", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    extract = subprocess.run(
        ["tar", "-x", "-C", str(out_dir)],
        input=proc.stdout,
        check=True,
    )
    del extract

    excludes = _load_manifest_excludes()
    removed: list[str] = []
    for path in sorted(out_dir.rglob("*"), reverse=True):
        if not path.is_file() and not path.is_dir():
            continue
        rel = path.relative_to(out_dir).as_posix()
        if _should_exclude(rel, excludes):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            removed.append(rel)

    # Fail if forbidden paths remain
    forbidden_hits = []
    for path in out_dir.rglob("*"):
        if path.is_file():
            rel = path.relative_to(out_dir).as_posix()
            if _should_exclude(rel, excludes):
                forbidden_hits.append(rel)
    if forbidden_hits:
        raise SystemExit(f"forbidden paths remain after sanitation: {forbidden_hits[:10]}")

    manifest_path = out_dir / "PUBLIC_SNAPSHOT_MANIFEST.txt"
    lines = ["# Public snapshot sanitation log", "# excluded paths:"]
    lines.extend(f"- {p}" for p in sorted(set(removed)))
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sorted(set(removed))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def init_public_git(out_dir: Path) -> str:
    subprocess.run(["git", "init"], cwd=out_dir, check=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=out_dir, check=True)
    subprocess.run(["git", "add", "-A"], cwd=out_dir, check=True)
    subprocess.run(
        [
            "git",
            "commit",
            "-m",
            "ADMIT Bench v0.1.0 — initial public release",
        ],
        cwd=out_dir,
        check=True,
    )
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=out_dir, text=True
    ).strip()
    count = subprocess.check_output(
        ["git", "rev-list", "--count", "HEAD"], cwd=out_dir, text=True
    ).strip()
    if count != "1":
        raise SystemExit(f"expected 1 commit, got {count}")
    return sha


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path, help="output directory (outside repo)")
    parser.add_argument(
        "--init-git",
        action="store_true",
        help="initialize one-commit git repository in out_dir",
    )
    args = parser.parse_args(argv)

    out_dir = args.out_dir.resolve()
    if REPO_ROOT in out_dir.parents or out_dir == REPO_ROOT:
        raise SystemExit("out_dir must be outside the source repository")

    removed = export_snapshot(out_dir)
    file_count = sum(1 for p in out_dir.rglob("*") if p.is_file())
    print(f"snapshot: {out_dir}")
    print(f"files: {file_count}")
    print(f"excluded: {len(removed)} paths")
    print(f"wheel hash (if built separately): run python -m build in snapshot")

    if args.init_git:
        sha = init_public_git(out_dir)
        print(f"root commit: {sha}")
        print("commit count: 1")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
