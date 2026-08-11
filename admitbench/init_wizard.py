"""`admitbench init` — from a fresh clone to a first verdict in one sitting.

A short menu: detect which provider keys exist, confirm provider and model,
write admitbench.config.json, then offer to run the doctor, the offline demo,
and (if a key is present) a real benchmark. Every question is a yes/no or a
numbered pick with a sensible default, so pressing Enter all the way through
does the right thing. `--yes` accepts every default without asking.
"""

from __future__ import annotations

import os
from pathlib import Path

from admitbench.config import DEFAULT_MODELS, load_config, save_config

KEY_ENVS = [
    ("refiant", "REFIANT_API_KEY"),
    ("openrouter", "OPENROUTER_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
]

BANNER = """\
ADMIT Bench — judge the record, not the answer
------------------------------------------------
This takes about a minute. Enter accepts the [default].
"""


def detect_providers() -> list[str]:
    return [name for name, env in KEY_ENVS if os.environ.get(env, "").strip()]


def bundled_cartridge(name: str = "cstr") -> str:
    """Prefer ./cartridges/<name>; fall back to the copy shipped with the package."""
    local = Path("cartridges") / name
    if (local / "manifest.yaml").exists():
        return str(local)
    packaged = Path(__file__).resolve().parent / "cartridges" / name
    if (packaged / "manifest.yaml").exists():
        return str(packaged)
    return str(local)


def _ask(prompt: str, default: str, yes: bool) -> str:
    if yes:
        return default
    answer = input(f"{prompt} [{default}]: ").strip()
    return answer or default


def _confirm(prompt: str, default: bool, yes: bool) -> bool:
    if yes:
        return default
    hint = "Y/n" if default else "y/N"
    answer = input(f"{prompt} [{hint}]: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def run_init(yes: bool = False, directory: str | Path | None = None) -> int:
    print(BANNER)

    detected = detect_providers()
    if detected:
        print(f"API keys found for: {', '.join(detected)}")
    else:
        print("No API keys found — that's fine, the stub provider runs everything offline.")
        print("(Add a key later via .env.example and rerun `admitbench init`.)")
    print()

    existing = load_config(directory)
    default_provider = existing.get("provider") or (detected[0] if detected else "stub")
    provider = _ask(
        f"Provider ({'/'.join([n for n, _ in KEY_ENVS] + ['custom', 'stub'])})",
        default_provider,
        yes,
    )
    default_model = existing.get("model") or DEFAULT_MODELS.get(provider, "oracle")
    model = _ask("Model", default_model, yes)
    cartridge = _ask("Cartridge", existing.get("cartridge") or bundled_cartridge(), yes)

    path = save_config({"provider": provider, "model": model, "cartridge": cartridge}, directory)
    print(f"\nSaved {path} — every command now uses these defaults; flags override.\n")

    if _confirm("Run the doctor now (offline self-check, ~seconds)?", True, yes):
        from admitbench.doctor import render_checks, run_checks

        print()
        print(render_checks(run_checks()))
        print()

    if _confirm("Run the offline demo (stub oracle on the CSTR cartridge)?", True, yes):
        from admitbench.cartridge import load_cartridge
        from admitbench.providers import get_provider
        from admitbench.report import build_report
        from admitbench.runner import run_suite

        cart = load_cartridge(bundled_cartridge())
        suite = run_suite(cart, get_provider("stub", "oracle"))
        markdown, _ = build_report([suite], {cart.id: cart.rulebook})
        print()
        print(markdown)
        print()

    if provider != "stub" and _confirm(
        f"Run the real benchmark now ({provider}:{model} on {cartridge})? Costs API spend.",
        False,
        yes,
    ):
        from admitbench.cli import main as cli_main

        return cli_main(["run", "--cartridge", cartridge, "--provider", provider, "--model", model])

    print("Done. Next steps:")
    print("  admitbench run --cmt          # full suite + caution monotonicity, using your config")
    print("  admitbench prompt             # cartridge-authoring prompt for ChatGPT/Claude")
    print("  jupyter notebook START_HERE.ipynb")
    return 0
