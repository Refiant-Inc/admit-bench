"""The paired diagnosis–action statistic.

The report's tier means are computed over admissible episodes only — correct
for ranking survivors, but the WRONG basis for the paper's central claim,
which is a *within-record* statement: the same record diagnoses the hazard
correctly yet proposes an inadmissible action. Averages over different subsets
cannot establish that.

This module computes the paired statistic over every parseable base output
(not admissible-only), so it is immune to the selection bias:

    P(inadmissible action | diagnosis correct), with a clustered-by-case CI,
    the full 2x2 contingency table, per-model rates, and the first-failing
    gate of every discordant (diagnosed-right-but-inadmissible) record.

Reads stored traces — the same objects the dashboard reads — so any published
result is reproducible from the trace set alone.
"""
from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path


def _t2_info(trace: dict) -> dict:
    for g in (trace.get("gates") or {}).get("results", []):
        if g.get("tier") == "T2":
            return g.get("info") or {}
    return {}


def hazard_active(trace: dict) -> bool:
    return bool(_t2_info(trace).get("hazard_active"))


def diagnosis_correct(trace: dict) -> bool:
    """Named the active hazard — structured hazard_id or a matching diagnosis,
    as decided deterministically at gate T2 (info.diagnosed)."""
    info = _t2_info(trace)
    return bool(info.get("hazard_active") and info.get("diagnosed"))


def _is_base_parseable(trace: dict) -> bool:
    return bool(
        trace.get("action_record")
        and trace.get("case_id")
        and not trace.get("ablation")
        and trace.get("attempt") != 2
    )


def collect_base_traces(root: str | Path) -> list[dict]:
    rows = []
    for f in sorted(Path(root).rglob("*.json")):
        if f.parent.name == "layer1" or f.name.startswith(("cmt_", "_summary", "report")):
            continue
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if "score" in t and "case_id" in t:
            rows.append(t)
    return rows


def paired_table(traces: list[dict], n_boot: int = 2000, seed: int = 0) -> dict:
    """The diagnosis-correct x admissible contingency over hazard-active
    parseable base outputs, with the conditional inadmissibility rate."""
    pop = [t for t in traces if _is_base_parseable(t) and hazard_active(t)]
    cell = Counter()
    discordant_gate = Counter()
    per_model = defaultdict(lambda: [0, 0])          # [inadmissible, total] among diag-correct
    by_case: dict[str, list[int]] = defaultdict(list)  # for clustered CI

    for t in pop:
        dc = diagnosis_correct(t)
        adm = (t.get("score") or {}).get("verdict") == "admissible"
        cell[("diagOK" if dc else "diagBAD", "adm" if adm else "inadm")] += 1
        if dc:
            model = f"{t.get('provider')}:{t.get('model')}"
            per_model[model][1] += 1
            by_case[t["case_id"]].append(0 if adm else 1)
            if not adm:
                per_model[model][0] += 1
                discordant_gate[(t.get("score") or {}).get("aas_code")] += 1

    diag_ok = cell[("diagOK", "adm")] + cell[("diagOK", "inadm")]
    discordant = cell[("diagOK", "inadm")]
    p = discordant / diag_ok if diag_ok else None

    ci = None
    if by_case:
        rng = random.Random(seed)
        keys = list(by_case)
        boots = []
        for _ in range(n_boot):
            sample = [x for k in (rng.choice(keys) for _ in keys) for x in by_case[k]]
            if sample:
                boots.append(sum(sample) / len(sample))
        boots.sort()
        if boots:
            ci = (round(boots[int(0.025 * len(boots))], 4),
                  round(boots[min(int(0.975 * len(boots)), len(boots) - 1)], 4))

    t4_discordant = sum(v for k, v in discordant_gate.items() if k and k.startswith("AAS-T4"))
    return {
        "n_hazard_active_parseable": len(pop),
        "cross_tab": {
            "diag_correct_admissible": cell[("diagOK", "adm")],
            "diag_correct_inadmissible": cell[("diagOK", "inadm")],
            "diag_wrong_admissible": cell[("diagBAD", "adm")],
            "diag_wrong_inadmissible": cell[("diagBAD", "inadm")],
        },
        "diagnosis_correct_total": diag_ok,
        "p_inadmissible_given_diagnosis_correct": round(p, 4) if p is not None else None,
        "clustered_ci_95": ci,
        "discordant_total": discordant,
        "discordant_by_first_failing_gate": dict(discordant_gate),
        "discordant_at_consequence_gate": t4_discordant,
        "discordant_consequence_share": round(t4_discordant / discordant, 4) if discordant else None,
        "per_model": {
            m: {"inadmissible": v[0], "total": v[1],
                "rate": round(v[0] / v[1], 4) if v[1] else None}
            for m, v in sorted(per_model.items())
        },
    }


def paired_markdown(tab: dict) -> str:
    ct = tab["cross_tab"]
    ci = tab["clustered_ci_95"]
    p = tab["p_inadmissible_given_diagnosis_correct"]
    lines = [
        "## Paired diagnosis–action gap",
        "",
        f"_Over {tab['n_hazard_active_parseable']} hazard-active parseable base outputs "
        "(not admissible-only — no selection bias)._",
        "",
        "| | admissible | inadmissible |",
        "|---|---|---|",
        f"| **diagnosis correct** | {ct['diag_correct_admissible']} | {ct['diag_correct_inadmissible']} |",
        f"| **diagnosis wrong** | {ct['diag_wrong_admissible']} | {ct['diag_wrong_inadmissible']} |",
        "",
    ]
    if p is not None:
        ci_txt = f" [95% CI {ci[0]:.0%}, {ci[1]:.0%}]" if ci else ""
        lines.append(
            f"**P(inadmissible | diagnosis correct) = {tab['discordant_total']}/"
            f"{tab['diagnosis_correct_total']} = {p:.1%}**{ci_txt} "
            f"(clustered by case). Of those {tab['discordant_total']} discordant records, "
            f"{tab['discordant_at_consequence_gate']} "
            f"({tab['discordant_consequence_share']:.0%}) fail first at the consequence gate T4."
        )
    lines += ["", "| model | P(inadmissible \\| diagnosis correct) |", "|---|---|"]
    for m, v in tab["per_model"].items():
        r = f"{v['rate']:.0%}" if v["rate"] is not None else "—"
        lines.append(f"| {m} | {v['inadmissible']}/{v['total']} = {r} |")
    return "\n".join(lines)
