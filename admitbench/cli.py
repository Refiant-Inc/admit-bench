"""The ADMIT Bench command line.

    admitbench init [--yes]                  guided setup: provider, model, first run
    admitbench doctor [--network]            diagnose the installation
    admitbench validate <cartridge>          compile-check a cartridge
    admitbench call ...                      run one episode, show the verdict
    admitbench run ...                       run a suite (+ robustness) and report
    admitbench sweep ...                     confidence intervals and severity edges
    admitbench build <source> ...            draft a cartridge from raw text
    admitbench prompt                        cartridge-authoring prompt for any LLM
    admitbench ingest ...                    operator notes → candidate knowledge
    admitbench promote ...                   move knowledge up the review ladder
    admitbench explain <code>                what a violation code means

`run`, `call`, `cmt`, and `sweep` read defaults from admitbench.config.json
(written by `init`); explicit flags always win.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from admitbench.config import resolve


def _load_dotenv() -> None:
    """Ten-line .env loader so the instructions in every error message are true."""
    import os

    path = Path(".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _default_out(provider: str, model: str) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe = f"{stamp}_{provider}_{model}"
    for ch in '/\\:*?"<>|':  # model ids like "org/model:free" must survive Windows
        safe = safe.replace(ch, "-")
    return Path("runs") / safe


def _resolved(args) -> tuple[str, str, str]:
    """(cartridge, provider, model) with flag > config > default precedence."""
    return (
        resolve(getattr(args, "cartridge", None), "cartridge"),
        resolve(getattr(args, "provider", None), "provider"),
        resolve(getattr(args, "model", None), "model"),
    )


def cmd_doctor(args) -> int:
    from admitbench.doctor import run_doctor

    return run_doctor(fix=args.fix, network=args.network)


def cmd_validate(args) -> int:
    from admitbench.cartridge import validate_cartridge

    errors, warnings = validate_cartridge(args.cartridge)
    for w in warnings:
        print(f"  warn: {w}")
    if errors:
        print(f"✗ {args.cartridge} does not compile:")
        for e in errors:
            print(f"  error: {e}")
        return 1
    print(f"✓ {args.cartridge} compiles ({len(warnings)} warning(s))")
    return 0


def cmd_call(args) -> int:
    from admitbench.cartridge import load_cartridge
    from admitbench.providers import get_provider
    from admitbench.runner import run_episode

    cartridge_path, provider_name, model = _resolved(args)
    cartridge = load_cartridge(cartridge_path)
    case = cartridge.case(args.case)
    provider = get_provider(provider_name, model)
    result = run_episode(
        cartridge, case, provider,
        prompt_style=args.style or "narrative", max_tokens=args.max_tokens,
    )

    score = result.score
    print(f"case      {cartridge.id}/{case.id} — {case.title}")
    print(f"model     {provider.describe()}")
    print(f"action    {result.record.action if result.record else '(no record)'}")
    print(f"verdict   {score.verdict}")
    if score.verdict == "admissible":
        tiers = "  ".join(f"{k}={v:.2f}" for k, v in score.tiers.items())
        print(f"tiers     {tiers}")
        print(f"aggregate {score.aggregate}")
    else:
        print(f"failed at {score.first_failing} — {score.aas_code}")
        for violation in result.gate_report.all_violations():
            print(f"  {violation.code}: {violation.message}")
        print(f"(run `admitbench explain {score.aas_code}` for guidance)")
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        trace_path = out / f"{result.label}.json"
        trace_path.write_text(json.dumps(result.trace, indent=2), encoding="utf-8")
        print(f"trace     {trace_path}")
    return 0


def cmd_run(args) -> int:
    from admitbench.cartridge import load_cartridge
    from admitbench.providers import get_provider
    from admitbench.report import build_report
    from admitbench.runner import run_cmt_suite, run_suite
    from admitbench.sweep import aggregate_ci, pass_rate_ci

    cartridge_path, provider_name, model = _resolved(args)
    cartridge = load_cartridge(cartridge_path)
    provider = get_provider(provider_name, model)
    out = Path(args.out) if args.out else _default_out(provider_name, model)
    case_ids = args.cases.split(",") if args.cases else None

    suite = run_suite(
        cartridge, provider, case_ids=case_ids, out_dir=out,
        prompt_style=args.style or "narrative", max_tokens=args.max_tokens,
        resume=args.resume,
    )
    cmt_results = None
    if args.cmt:
        cmt_results = run_cmt_suite(
            cartridge, provider, case_ids=case_ids, out_dir=out,
            full_results={r.case_id: r for r in suite.results},
        )
    repair_summary = None
    if args.repair:
        from admitbench.repair import repair_metrics, run_repair
        from admitbench.runner import _write_json

        outcomes = []
        for result in suite.results:
            if result.score.verdict == "admissible":
                continue
            outcome, second = run_repair(
                cartridge, case=cartridge.case(result.case_id), provider=provider,
                first_result=result,
                prompt_style=args.style or "narrative", max_tokens=args.max_tokens,
            )
            outcomes.append(outcome)
            if second is not None:
                _write_json(out / f"{result.case_id}.repair.json", second.trace)
        repair_summary = repair_metrics(outcomes)
    if suite.errors:
        print(f"episodes lost to provider faults: {len(suite.errors)} "
              f"(rerun with --resume to fill them in)")
    if suite.skipped:
        print(f"episodes restored from existing traces: {suite.skipped}")

    markdown, data = build_report([suite], {cartridge.id: cartridge.rulebook}, cmt_results)
    pass_ci = pass_rate_ci(suite)
    agg_ci = aggregate_ci(suite)
    confidence = ["", "### Confidence (bootstrap, 95%)", ""]
    confidence.append(f"- pass rate: {pass_ci}" if pass_ci else "- pass rate: n/a")
    confidence.append(f"- mean aggregate: {agg_ci}" if agg_ci else "- mean aggregate: n/a (no admissible episodes)")
    markdown += "\n" + "\n".join(confidence) + "\n"
    data["confidence"] = {
        "pass_rate": pass_ci.to_dict() if pass_ci else None,
        "aggregate": agg_ci.to_dict() if agg_ci else None,
    }
    if repair_summary is not None:
        markdown += (
            "\n### Repair loop (one sanitized-feedback retry per failure)\n\n"
            f"- failures retried: {repair_summary['failures_retried']}\n"
            f"- repaired: {repair_summary['repaired']}"
            f" (repair@1 = {repair_summary['repair_at_1']})\n"
            f"- codes that survived repair: {repair_summary['unrepaired_codes'] or 'none'}\n"
        )
        data["repair"] = repair_summary
    (out / "report.md").write_text(markdown, encoding="utf-8")
    (out / "report.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(markdown)
    from admitbench.dashboard import write_dashboard

    dashboard = write_dashboard(out)
    print(f"\ntraces and report written to {out}/")
    print(f"dashboard: open {dashboard}")
    return 0


def cmd_paired(args) -> int:
    """The paired diagnosis–action statistic over a directory of traces."""
    import json as _json

    from admitbench.paired import collect_base_traces, paired_markdown, paired_table

    traces = collect_base_traces(args.dir)
    if not traces:
        print(f"no episode traces found under {args.dir}")
        return 1
    tab = paired_table(traces)
    md = paired_markdown(tab)
    print(md)
    if args.out:
        Path(args.out).write_text(_json.dumps(tab, indent=2), encoding="utf-8")
        print(f"\npaired statistic written to {args.out}")
    return 0


def cmd_verifier_sweep(args) -> int:
    """How much of the T4 verdict is the physics, and how much is the threshold."""
    import json as _json

    from admitbench.verifier_sweep import (
        DEFAULT_HORIZONS_S,
        DEFAULT_SCALES,
        sweep_run,
        sweep_table,
    )

    scales = tuple(float(s) for s in args.scales.split(",")) if args.scales else DEFAULT_SCALES
    sweep = sweep_run(args.dir, scales=scales, horizons_s=DEFAULT_HORIZONS_S)
    if not sweep.cases:
        print(f"no projectable episode traces found under {args.dir}")
        return 1
    print(sweep_table(sweep))
    if args.out:
        Path(args.out).write_text(_json.dumps(sweep.to_dict(), indent=2), encoding="utf-8")
        print(f"verifier sweep written to {args.out}")
    return 0


def cmd_dashboard(args) -> int:
    from admitbench.dashboard import (
        collect, collect_layer1, summarize, write_dashboard, write_layer1_dashboard,
    )

    gate_rows = collect(args.dir)
    if gate_rows:
        path = write_dashboard(args.dir, out=args.out)
        stats = summarize(gate_rows)
        print(f"{len(gate_rows)} gate-based episodes across {len(stats)} model×style lanes → {path}")
        total = sum(r["cost"] for r in gate_rows)
        print(f"total spend ${total:.2f} · {sum(r['tok_in'] + r['tok_out'] for r in gate_rows):,} tokens")

    l1_path = write_layer1_dashboard(args.dir)
    if l1_path:
        l1 = collect_layer1(args.dir)
        print(f"{len(l1)} Layer-1 episodes → {l1_path}")

    if not gate_rows and not l1_path:
        print(f"no episode traces found under {args.dir}")
        return 1
    return 0


def cmd_cmt(args) -> int:
    from admitbench.cartridge import load_cartridge
    from admitbench.providers import get_provider
    from admitbench.runner import run_cmt_suite

    cartridge_path, provider_name, model = _resolved(args)
    cartridge = load_cartridge(cartridge_path)
    provider = get_provider(provider_name, model)
    results = run_cmt_suite(cartridge, provider, out_dir=args.out)
    if not results:
        print("no cases in this cartridge declare ablations — nothing to compare")
        return 0
    for cmt in results:
        codes = cmt.violations()
        shown = f"{cmt.score:.2f}" if cmt.score is not None else "n/a (no baseline record)"
        print(f"{cmt.case_id}: CMT score {shown}" + (f" — {codes}" if codes else ""))
    scored = [r.score for r in results if r.score is not None]
    if scored:
        print(f"mean caution monotonicity: {sum(scored) / len(scored):.3f} over {len(scored)} scoreable cases")
    else:
        print("caution monotonicity: not scoreable — no episode produced a baseline record")
    return 0


def cmd_build(args) -> int:
    from admitbench.builder import draft_cartridge

    provider = None
    if args.provider:
        from admitbench.providers import get_provider

        provider = get_provider(args.provider, args.model)
    text = Path(args.source).read_text(encoding="utf-8")
    out = draft_cartridge(
        text, args.out, cartridge_id=args.id, world=args.world, provider=provider
    )
    print(f"draft cartridge written to {out}/ — read BUILD_REPORT.md before using it")
    return 0


def cmd_ingest(args) -> int:
    from admitbench.builder import ingest_operator_log

    text = Path(args.log).read_text(encoding="utf-8")
    added = ingest_operator_log(args.cartridge, text, logged_by=args.by)
    for entry in added:
        print(f"  + {entry['id']} (candidate): {entry['cause'][:80]}")
    print(f"{len(added)} candidate cause→effect entries appended — review with `admitbench promote`")
    return 0


def cmd_promote(args) -> int:
    from admitbench.builder import promote

    entry = promote(args.cartridge, args.entry, args.to)
    print(f"{entry['id']} → {entry['review_status']}")
    return 0


def cmd_explain(args) -> int:
    from admitbench.doctor import explain

    print(explain(args.code))
    return 0


def cmd_report(args) -> int:
    """Rebuild the three tables (plus compliance) from a directory of traces —
    the salvage path for interrupted runs, and the multi-model aggregator."""
    import json as _json
    from collections import defaultdict

    from admitbench.cartridge import load_cartridge
    from admitbench.compliance import compliance_from_traces, compliance_table
    from admitbench.report import build_report
    from admitbench.runner import SuiteResult, _result_from_trace

    root = Path(args.dir)
    traces = []
    for f in sorted(root.rglob("*.json")):
        if f.name.startswith(("cmt_", "report")):
            continue
        try:
            t = _json.loads(f.read_text(encoding="utf-8"))
        except _json.JSONDecodeError:
            continue
        if "score" in t and "case_id" in t and t.get("attempt") != 2 and not t.get("ablation"):
            traces.append(t)  # base episodes only: no repair attempts, no CMT-degraded runs
    if not traces:
        print(f"no episode traces found under {root}")
        return 1

    books, carts = {}, {}
    for name in ("cstr", "distillation"):
        try:
            cart = load_cartridge(Path("cartridges") / name)
            books[cart.id] = cart.rulebook
            carts[cart.id] = cart
        except Exception:
            pass

    grouped = defaultdict(list)
    for t in traces:
        style = t.get("prompt_style", "narrative")
        model = t.get("model", "?") + (f"@{style}" if style != "narrative" else "")
        key = (t.get("provider", "?"), model, (t.get("cartridge") or {}).get("id", "?"))
        grouped[key].append(t)

    suites = []
    per_model_traces = defaultdict(list)
    for (prov, model, cart_id), ts in sorted(grouped.items()):
        suite = SuiteResult(cartridge_id=cart_id, provider=prov, model=model)
        suite.results = [_result_from_trace(t) for t in ts]
        suites.append(suite)
        per_model_traces[f"{prov}:{model}"].extend(ts)

    known = [s for s in suites if s.cartridge_id in books]
    markdown, data = build_report(known, books)
    compliance = {
        model: compliance_from_traces(ts, books)
        for model, ts in per_model_traces.items()
    }
    markdown += "\n\n## Compliance (instruction following)\n\n" + compliance_table(compliance)
    (root / "report.md").write_text(markdown, encoding="utf-8")
    (root / "report.json").write_text(_json.dumps(data, indent=2), encoding="utf-8")
    print(markdown)
    print(f"\nrebuilt from {len(traces)} traces → {root}/report.md")
    return 0


def cmd_init(args) -> int:
    from admitbench.init_wizard import run_init

    return run_init(yes=args.yes)


def cmd_plugin_root(_args) -> int:
    from admitbench.paths import agent_plugin_root

    print(agent_plugin_root())
    return 0


def cmd_prompt(args) -> int:
    from admitbench.builder import AUTHORING_PROMPT

    print(AUTHORING_PROMPT)
    print(
        "\n--- (paste everything above into ChatGPT, Claude, or any LLM, attach "
        "your documents, then save the four files and run `admitbench validate`) ---",
        file=sys.stderr,
    )
    return 0


def cmd_sweep(args) -> int:
    from admitbench.cartridge import load_cartridge
    from admitbench.providers import get_provider
    from admitbench.runner import run_suite
    from admitbench.sweep import aggregate_ci, pass_rate_ci, severity_sweep, weight_sweep

    cartridge_path, provider_name, model = _resolved(args)
    cartridge = load_cartridge(cartridge_path)
    provider = get_provider(provider_name, model)

    if args.case and args.param:
        values = [float(v) for v in args.values.split(",")]
        points = severity_sweep(cartridge, provider, args.case, args.param, values)
        print(f"severity sweep — {cartridge.id}/{args.case}, {args.param} over {values}")
        print("| value | verdict | action | code | null crosses at |")
        print("|---|---|---|---|---|")
        for p in points:
            print(
                f"| {p.value:g} | {p.verdict} | {p.action or '—'} | {p.aas_code or '—'} | "
                f"{f'{p.null_crossed_at:.0f}s' if p.null_crossed_at else 'never'} |"
            )
        return 0

    suite = run_suite(cartridge, provider)
    pass_ci = pass_rate_ci(suite, seed=args.seed)
    agg_ci = aggregate_ci(suite, seed=args.seed)
    weights = weight_sweep(suite, n_samples=args.samples, jitter=args.jitter, seed=args.seed)
    print(f"confidence sweep — {provider_name}:{model} on {cartridge.id} ({len(suite.results)} episodes)")
    print(f"  pass rate        {pass_ci}")
    print(f"  mean aggregate   {agg_ci if agg_ci else 'n/a (no admissible episodes)'}")
    if weights.interval:
        print(
            f"  under weight jitter (±{args.jitter} log-scale, {args.samples} samples): "
            f"{weights.interval} — default weighting gives {weights.default_mean}"
        )
        print("  read: if a ranking claim survives this interval, it is about the model, not the weights")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="admitbench",
        description="Admissibility-first evaluation for industrial AI agents.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="guided setup: provider, model, config, first run")
    p.add_argument("--yes", action="store_true", help="accept every default without asking")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="diagnose the installation; --fix applies safe repairs")
    p.add_argument("--network", action="store_true", help="also probe provider endpoints")
    p.add_argument("--fix", action="store_true",
                   help="apply safe and guarded repairs, then re-verify (escalations never auto-apply)")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("validate", help="compile-check a cartridge directory")
    p.add_argument("cartridge")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("call", help="run one episode")
    p.add_argument("--cartridge", default=None)
    p.add_argument("--case", required=True)
    p.add_argument("--provider", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--style", default=None, help="prompt style: narrative|compact|checklist")
    p.add_argument("--max-tokens", type=int, default=None)
    p.add_argument("--out", default=None, help="also write the trace here")
    p.set_defaults(func=cmd_call)

    p = sub.add_parser("run", help="run a benchmark suite and write the report")
    p.add_argument("--cartridge", default=None)
    p.add_argument("--provider", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--cases", default=None, help="comma-separated case ids (default: all)")
    p.add_argument("--cmt", action="store_true", help="also run caution monotonicity")
    p.add_argument("--repair", action="store_true",
                   help="retry each failure once with sanitized gate feedback (repair@1)")
    p.add_argument("--style", default=None, help="prompt style: narrative|compact|checklist")
    p.add_argument("--max-tokens", type=int, default=None)
    p.add_argument("--resume", action="store_true", help="skip cases whose traces already exist in --out")
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="rebuild tables from a directory of traces (salvage/aggregate)")
    p.add_argument("dir")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("dashboard", help="one HTML file: plots + a raw audit of every trace under a directory")
    p.add_argument("dir", help="a run directory, or runs/ to aggregate across every run")
    p.add_argument("--out", default=None, help="output path (default: <dir>/dashboard.html)")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("paired", help="the paired diagnosis–action gap over a directory of traces")
    p.add_argument("dir", help="a run directory of episode traces")
    p.add_argument("--out", default=None, help="also write the statistic as JSON here")
    p.set_defaults(func=cmd_paired)

    p = sub.add_parser(
        "verifier-sweep",
        help="does the T4 verdict survive choosing the envelope differently?",
    )
    p.add_argument("dir", help="a run directory of episode traces")
    p.add_argument(
        "--scales",
        default=None,
        help="comma-separated envelope scales (default 0.8,0.9,0.95,1.0,1.05,1.1,1.2,1.3,1.5)",
    )
    p.add_argument("--out", default=None, help="also write the sweep as JSON here")
    p.set_defaults(func=cmd_verifier_sweep)

    p = sub.add_parser("cmt", help="run only the caution monotonicity tests")
    p.add_argument("--cartridge", default=None)
    p.add_argument("--provider", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_cmt)

    p = sub.add_parser("sweep", help="confidence intervals; or a severity sweep with --case/--param")
    p.add_argument("--cartridge", default=None)
    p.add_argument("--provider", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--samples", type=int, default=200, help="weight-jitter samples")
    p.add_argument("--jitter", type=float, default=0.25, help="log-scale weight jitter")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--case", default=None, help="severity mode: case id")
    p.add_argument("--param", default=None, help="severity mode: initial_state key to sweep")
    p.add_argument("--values", default=None, help="severity mode: comma-separated values")
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser("prompt", help="print the cartridge-authoring prompt for any LLM")
    p.set_defaults(func=cmd_prompt)

    p = sub.add_parser("build", help="draft a cartridge from raw text")
    p.add_argument("source", help="text file: HAZOP notes, SOPs, incident reports, one prompt")
    p.add_argument("--out", required=True)
    p.add_argument("--id", required=True, help="cartridge id, e.g. my_plant")
    p.add_argument("--world", default="cstr", choices=["cstr", "column"])
    p.add_argument("--provider", default=None, help="optional: draft with a model instead of heuristics")
    p.add_argument("--model", default=None)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("ingest", help="operator log → candidate cause→effect knowledge")
    p.add_argument("--cartridge", required=True)
    p.add_argument("--log", required=True, help="text file of operator notes")
    p.add_argument("--by", default="unknown operator")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("promote", help="move a knowledge entry up the review ladder")
    p.add_argument("--cartridge", required=True)
    p.add_argument("--entry", required=True)
    p.add_argument("--to", required=True, choices=["reviewed", "validated", "deprecated"])
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("explain", help="what a violation code means and what to do")
    p.add_argument("code")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser(
        "plugin-root",
        help="print the installed Agent Plugins directory (plugin.json + mcp.json)",
    )
    p.set_defaults(func=cmd_plugin_root)

    args = parser.parse_args(argv)
    # a cp1252 console (Windows default) must degrade gracefully on \u2713/\u2192,
    # never crash the checkup that exists to build trust
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    _load_dotenv()
    try:
        return args.func(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
