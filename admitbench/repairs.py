"""The repair layer: what the doctor may do about what it finds.

The ladder mirrors the bench's own gate philosophy — ordered by how much a
fix commits to the world, and honest about what automation cannot reach:

    safe      idempotent and non-destructive: create what is missing, remove
              what is provably litter, quarantine what is provably corrupt.
              Never touches user data content, never touches secrets.
    guarded   deterministic edits to user-owned files (a cartridge, a
              config), always with a .bak backup, applied only under --fix.
    escalate  cannot be fixed offline or deterministically. The repair does
              nothing; its description names the exact path — the env var,
              the command, the reinstall, or the API/model-assisted route.

Two rules are absolute. A repair must verify itself: the doctor re-runs the
corresponding check after applying, and only a passing re-check counts as
healed. And kernel integrity is never auto-fixed: a failing litmus pair,
answer-key seal, or physics check means the kernel is corrupted, and a doctor
that silently patches the kernel is the falsely-green failure with extra
steps — those escalate, always.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

SAFE, GUARDED, ESCALATE = "safe", "guarded", "escalate"


@dataclass
class Repair:
    description: str  # imperative, one line: what applying will do (or the path out)
    kind: str  # safe | guarded | escalate
    _fn: Optional[Callable[[], str]] = None

    def apply(self) -> str:
        if self.kind == ESCALATE or self._fn is None:
            return f"escalation (not auto-applied): {self.description}"
        return self._fn()


def escalate(path_out: str) -> Repair:
    return Repair(description=path_out, kind=ESCALATE)


def _backup(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, bak)
    return bak


# ---------------------------------------------------------------------------
# safe repairs
# ---------------------------------------------------------------------------

def rebuild_config(path: Path) -> Repair:
    def fn() -> str:
        from admitbench.config import DEFAULTS

        note = ""
        if path.exists():
            note = f" (original backed up to {_backup(path).name})"
        path.write_text(json.dumps(DEFAULTS, indent=2) + "\n", encoding="utf-8")
        return f"rebuilt {path.name} from defaults{note}"

    return Repair("rebuild admitbench.config.json from defaults (backing up the original)", SAFE, fn)


def clean_tmp_litter(files: list[Path]) -> Repair:
    def fn() -> str:
        removed = 0
        for f in files:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
        return f"removed {removed} stale .json.tmp file(s) left by interrupted writes"

    return Repair(f"delete {len(files)} stale *.json.tmp file(s) under runs/", SAFE, fn)


def quarantine_corrupt_traces(files: list[Path], runs_dir: Path) -> Repair:
    def fn() -> str:
        pen = runs_dir / "_corrupt"
        pen.mkdir(exist_ok=True)
        moved = 0
        for f in files:
            try:
                target = pen / f"{int(time.time())}_{f.name}"
                shutil.move(str(f), target)
                moved += 1
            except OSError:
                pass
        return f"quarantined {moved} unparseable trace file(s) to {pen}/ (nothing deleted)"

    return Repair(
        f"quarantine {len(files)} corrupt trace file(s) to runs/_corrupt/ (never deleted)", SAFE, fn
    )


def write_env_skeleton(example: Path, target: Path) -> Repair:
    def fn() -> str:
        if target.exists():
            return ".env already exists; left untouched"
        shutil.copy2(example, target)
        return "created .env from .env.example — add real key values yourself (the doctor never handles secrets)"

    return Repair("create a .env skeleton from .env.example (keys left blank for you to fill)", SAFE, fn)


# ---------------------------------------------------------------------------
# guarded repairs — deterministic edits to user-owned files, .bak always
# ---------------------------------------------------------------------------

def reset_config_defaults(path: Path, bad_fields: list[str]) -> Repair:
    def fn() -> str:
        from admitbench.config import DEFAULTS, load_config, save_config

        _backup(path)
        config = load_config(path.parent)
        for key in bad_fields:
            config[key] = DEFAULTS[key]
        save_config(config, path.parent)
        return f"reset {', '.join(bad_fields)} to defaults in {path.name} (.bak saved)"

    return Repair(f"reset invalid config field(s) {bad_fields} to safe defaults (.bak saved)", GUARDED, fn)


def normalize_weights(cartridge_dir: Path) -> Repair:
    def fn() -> str:
        import yaml

        manifest_path = cartridge_dir / "manifest.yaml"
        _backup(manifest_path)
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        weights = manifest["scoring"]["weights"]
        total = sum(weights.values())
        manifest["scoring"]["weights"] = {k: round(v / total, 6) for k, v in weights.items()}
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        return f"normalized scoring weights in {cartridge_dir.name}/manifest.yaml (summed {total:g}; .bak saved)"

    return Repair(f"normalize scoring weights in {cartridge_dir.name} to sum to 1.0 (.bak saved)", GUARDED, fn)


def add_trust_sources(cartridge_dir: Path, sources: list[str]) -> Repair:
    def fn() -> str:
        import yaml

        manifest_path = cartridge_dir / "manifest.yaml"
        _backup(manifest_path)
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        for source in sources:
            manifest.setdefault("trust_map", {}).setdefault(source, "untrusted")
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        return (
            f"added {sources} to {cartridge_dir.name} trust_map as UNTRUSTED (.bak saved) — "
            "promote to trusted only after a human decides the channel deserves it"
        )

    return Repair(
        f"add missing evidence source(s) {sources} to trust_map as untrusted (.bak saved)", GUARDED, fn
    )


def add_tag_stubs(cartridge_dir: Path, tags: list[str]) -> Repair:
    def fn() -> str:
        graph = cartridge_dir / "system_graph.jsonl"
        _backup(graph)
        with graph.open("a") as fh:
            for tag in tags:
                fh.write(
                    json.dumps(
                        {
                            "kind": "tag",
                            "id": tag,
                            "measures": "unknown",
                            "unit": "unknown",
                            "review_status": "candidate",
                            "source": "doctor_repair",
                        }
                    )
                    + "\n"
                )
        return f"appended candidate tag stub(s) {tags} to {cartridge_dir.name}/system_graph.jsonl (.bak saved)"

    return Repair(
        f"append candidate stub(s) for unknown tag(s) {tags} to system_graph.jsonl (.bak saved)", GUARDED, fn
    )


# ---------------------------------------------------------------------------
# cartridge error → repair matching (conservative: unrecognized stays manual)
# ---------------------------------------------------------------------------

def repairs_for_cartridge_errors(cartridge_dir: Path, errors: list[str]) -> list[Repair]:
    import re

    out: list[Repair] = []
    weight_broken = any("weights sum to" in e for e in errors)
    sources = sorted({m.group(1) for e in errors for m in [re.search(r"has source '([\w-]+)' not in trust_map", e)] if m})
    tags = sorted({m.group(1) for e in errors for m in [re.search(r"references tag '([\w-]+)' not in system_graph", e)] if m})

    if weight_broken:
        out.append(normalize_weights(cartridge_dir))
    if sources:
        out.append(add_trust_sources(cartridge_dir, sources))
    if tags:
        out.append(add_tag_stubs(cartridge_dir, tags))

    handled = weight_broken or sources or tags
    unhandled = [
        e for e in errors
        if "weights sum to" not in e
        and "not in trust_map" not in e
        and "not in system_graph" not in e
    ]
    if unhandled or not handled:
        out.append(
            escalate(
                f"{len(unhandled) or len(errors)} error(s) need judgment — run `admitbench validate "
                f"{cartridge_dir}` and fix by hand, or regenerate a draft with a model: "
                f"`admitbench build <source> --out {cartridge_dir} --id {cartridge_dir.name} "
                "--provider <provider> --model <model>`"
            )
        )
    return out
