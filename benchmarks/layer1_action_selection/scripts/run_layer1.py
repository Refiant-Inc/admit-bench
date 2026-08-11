#!/usr/bin/env python3
"""Model runner for Layer-1 action selection — the sealed evaluation loop.

Loads scenarios, renders each through build_layer1_prompt (the answer key is
structurally unreachable — the renderer never reads PRIVATE_FIELDS), calls a
ADMIT Bench Provider, extracts the A/B/C/D choice with the deterministic
scorer, scores it against the sealed key, and writes one trace per scenario
plus a summary. Resumable like the main runner: scenarios whose traces already
exist are skipped, so a killed run resumes without re-paying.

This is a different benchmark from the gate-based one: it scores a single
multiple-choice selection on the correct/incorrect × safe/unsafe matrix, not a
full action record through T0-T6. Its traces live in their own directory and
never mix into the ADMIT Bench dashboard.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prompts = _sibling("build_layer1_prompt")
scorer = _sibling("score_layer1_choice")


def _load_env() -> None:
    """Same ten-line .env loader the CLI uses, so keys resolve identically."""
    path = Path(".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def load_scenarios(path: str | Path, limit: int | None = None) -> list[dict]:
    rows = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows[:limit] if limit else rows


def _git_rev() -> str:
    try:
        from admitbench.provenance import git_rev
        return git_rev()
    except Exception:
        return "unknown"


def run_scenario(scenario: dict, provider, max_tokens: int = 600) -> dict:
    """Render sealed, call the model, extract + score the choice, return a trace."""
    system = prompts.SYSTEM_PROMPT
    user = prompts.build_user_prompt(scenario)

    # defense in depth: the answer key must not be in the rendered prompt
    leaked = [f for f in prompts.PRIVATE_FIELDS if f in (system + user)]
    if leaked:
        raise RuntimeError(f"sealing breach: {leaked} reached the prompt for {scenario['scenario_id']}")

    completion = provider.complete(system, user, max_tokens=max_tokens)
    text = completion.text
    meta = {
        "latency_s": completion.latency_s, "input_tokens": completion.input_tokens,
        "output_tokens": completion.output_tokens, "cost_usd": completion.cost_usd,
        "finish_reason": completion.finish_reason, "truncated": completion.truncated,
    }

    result = scorer.score_choice(text, scenario)
    return {
        "benchmark_layer": "layer1_action_selection",
        "scenario_id": scenario["scenario_id"],
        "cartridge": scenario.get("cartridge"),
        "fault_family": scenario.get("fault_family"),
        "variant_factors": scenario.get("variant_factors"),
        "provider": provider.name, "model": provider.model,
        "selected_action": result["selected_action"],
        "matrix_class": result["matrix_class"],
        "score": result["score"], "safe": result["safe"], "correct": result["correct"],
        "hard_violation": result["hard_violation"],
        "raw_model_text": text,
        "completion": meta,
        "provenance": {"git_rev": _git_rev()},
        "sealed": True,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def run_suite(scenarios, provider, out_dir, resume: bool = False, max_tokens: int = 600,
              on_progress=None) -> dict:
    from admitbench.providers import ProviderError

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    traces, errors, skipped = [], [], 0
    for i, scenario in enumerate(scenarios, 1):
        trace_path = out / f"{scenario['scenario_id']}.json"
        if resume and trace_path.exists():
            try:
                traces.append(json.loads(trace_path.read_text(encoding="utf-8")))
                skipped += 1
                continue
            except (json.JSONDecodeError, OSError):
                pass
        try:
            trace = run_scenario(scenario, provider, max_tokens=max_tokens)
        except ProviderError as exc:
            errors.append({"scenario_id": scenario["scenario_id"], "error": str(exc)})
            continue
        _write_json(trace_path, trace)
        traces.append(trace)
        if on_progress and i % 25 == 0:
            on_progress(i, len(scenarios), summarize(traces))
    summary = summarize(traces)
    summary["errors"] = len(errors)
    summary["skipped"] = skipped
    _write_json(out / "_summary.json", summary)
    return summary


def summarize(traces: list[dict]) -> dict:
    if not traces:
        return {"n": 0}
    from collections import Counter
    matrix = Counter(t["matrix_class"] for t in traces)
    n = len(traces)
    cost = sum((t.get("completion") or {}).get("cost_usd", 0.0) or 0.0 for t in traces)
    return {
        "n": n,
        "mean_score": round(sum(t["score"] for t in traces) / n, 4),
        "safe_rate": round(sum(1 for t in traces if t["safe"]) / n, 4),
        "correct_rate": round(sum(1 for t in traces if t["correct"]) / n, 4),
        "unsafe_selection_rate": round(sum(1 for t in traces if not t["safe"]) / n, 4),
        "unsupported_rate": round(matrix.get("unsupported_action", 0) / n, 4),
        "matrix": dict(matrix),
        "cost_usd": round(cost, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Layer-1 action-selection benchmark on a model.")
    parser.add_argument("--scenarios", type=Path,
                        default=_HERE.parent / "data" / "draft" / "industrialbench_layer1_generated_420.jsonl")
    parser.add_argument("--provider", required=True, help="refiant | openrouter | anthropic | stub")
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None, help="first N scenarios (smoke test)")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-tokens", type=int, default=600)
    args = parser.parse_args()

    _load_env()
    sys.path.insert(0, str(_HERE.parent.parent.parent))  # repo root: import admitbench
    from admitbench.providers import get_provider

    scenarios = load_scenarios(args.scenarios, limit=args.limit)
    provider = get_provider(args.provider, args.model)
    print(f"Layer-1: {len(scenarios)} scenarios -> {provider.name}:{provider.model} -> {args.out}")

    def progress(i, total, s):
        print(f"  [{i}/{total}] mean_score={s['mean_score']} safe={s['safe_rate']} "
              f"unsafe_sel={s['unsafe_selection_rate']} ${s['cost_usd']}", flush=True)

    summary = run_suite(scenarios, provider, args.out, resume=args.resume,
                        max_tokens=args.max_tokens, on_progress=progress)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
