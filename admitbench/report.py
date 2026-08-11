"""Three tables, three different claims — never mixed.

    1. Safety failures     which episodes were inadmissible, and why (no score)
    2. Safe performance    ranking among the admissible only
    3. Robustness          does admissibility survive degraded evidence?
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Optional

from admitbench.runner import CMTResult, SuiteResult
from admitbench.scoring import VERDICT_ADMISSIBLE

TIERS = ("T1", "T2", "T3", "T4", "T5")


def _fmt(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:.3f}"


def build_report(
    suites: list[SuiteResult],
    cartridge_rulebooks: dict,
    cmt_results: Optional[list[CMTResult]] = None,
) -> tuple[str, dict]:
    """Returns (markdown, data). `cartridge_rulebooks` maps cartridge_id → RuleBook."""
    lines: list[str] = ["# ADMIT Bench report", ""]
    data: dict = {"suites": [], "safety_failures": [], "leaderboard": [], "robustness": []}

    # ---- Table 1 — safety failures ----------------------------------------
    lines += ["## 1 · Safety failures (no aggregate — ineligible, not ranked low)", ""]
    failures = [
        (suite, result)
        for suite in suites
        for result in suite.results
        if result.score and result.score.verdict != VERDICT_ADMISSIBLE
    ]
    if failures:
        lines.append("| model | case | verdict | first failing gate | code | attempted action |")
        lines.append("|---|---|---|---|---|---|")
        for suite, result in failures:
            action = result.record.action if result.record else "(no record)"
            row = {
                "model": f"{suite.provider}:{suite.model}",
                "case": f"{result.cartridge_id}/{result.label}",
                "verdict": result.score.verdict,
                "gate": result.score.first_failing,
                "code": result.score.aas_code,
                "action": action,
            }
            data["safety_failures"].append(row)
            lines.append(
                f"| {row['model']} | {row['case']} | {row['verdict']} | "
                f"{row['gate']} | `{row['code']}` | {row['action']} |"
            )
    else:
        lines.append("_none_")
    lines.append("")

    # ---- Table 2 — safe performance ----------------------------------------
    lines += [
        "## 2 · Safe performance (admissible episodes only)",
        "",
        "_Tier means below rank the admissible survivors; they are NOT a diagnosis "
        "metric. Diagnosis accuracy and the paired diagnosis–action gap are reported "
        "over all parseable outputs separately (`admitbench paired`) to avoid the "
        "admissible-only selection bias._",
        "",
    ]
    lines.append("| model | cartridge | pass rate | exposure | " + " | ".join(TIERS) + " | aggregate |")
    lines.append("|---|---|---|---|" + "---|" * len(TIERS) + "---|")
    for suite in suites:
        rulebook = cartridge_rulebooks[suite.cartridge_id]
        admissible = [r for r in suite.results if r.score and r.score.verdict == VERDICT_ADMISSIBLE]
        tier_means = {}
        for tier in TIERS:
            values = [r.score.tiers.get(tier) for r in admissible if tier in r.score.tiers]
            tier_means[tier] = sum(values) / len(values) if values else None
        aggregates = [r.score.aggregate for r in admissible if r.score.aggregate is not None]
        mean_aggregate = sum(aggregates) / len(aggregates) if aggregates else None
        row = {
            "model": f"{suite.provider}:{suite.model}",
            "cartridge": suite.cartridge_id,
            "pass_rate": suite.pass_rate(),
            "exposure": suite.exposure(rulebook),
            "tiers": tier_means,
            "aggregate": mean_aggregate,
            "episodes": len(suite.results),
        }
        data["leaderboard"].append(row)
        lines.append(
            f"| {row['model']} | {row['cartridge']} | {row['pass_rate']:.2f} | "
            f"{row['exposure']:.1f} | "
            + " | ".join(_fmt(tier_means[t]) for t in TIERS)
            + f" | {_fmt(mean_aggregate)} |"
        )
    lines.append("")

    # ---- Table 3 — robustness ----------------------------------------------
    if cmt_results:
        lines += ["## 3 · Robustness — caution monotonicity", ""]
        lines.append("| model | case | CMT score | violations |")
        lines.append("|---|---|---|---|")
        by_model: dict[str, list[CMTResult]] = defaultdict(list)
        for cmt in cmt_results:
            by_model[f"{cmt.provider}:{cmt.model}"].append(cmt)
            codes = Counter(cmt.violations())
            shown = ", ".join(f"`{c}`×{n}" for c, n in codes.items()) or "—"
            row = {
                "model": f"{cmt.provider}:{cmt.model}",
                "case": f"{cmt.cartridge_id}/{cmt.case_id}",
                "score": cmt.score,
                "violations": dict(codes),
            }
            data["robustness"].append(row)
            shown_score = f"{cmt.score:.2f}" if cmt.score is not None else "n/a (silent)"
            lines.append(f"| {row['model']} | {row['case']} | {shown_score} | {shown} |")
        lines.append("")
        for model, results in by_model.items():
            scored = [r.score for r in results if r.score is not None]
            if scored:
                lines.append(
                    f"- **{model}** mean CMT score: {sum(scored) / len(scored):.3f} "
                    f"over {len(scored)} scoreable cases"
                    + (f" ({len(results) - len(scored)} unscoreable — no baseline record)"
                       if len(scored) < len(results) else "")
                )
            else:
                lines.append(f"- **{model}** CMT: not scoreable — no episode produced a baseline record")
        lines.append("")

    lines += [
        "---",
        "_Gates decide admissibility. Scores rank only safe interventions. "
        "Traces make the decision auditable._",
    ]
    return "\n".join(lines), data
