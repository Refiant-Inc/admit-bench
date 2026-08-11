"""Instruction-following metrics: format compliance, separated from judgment.

A model can fail this bench two very different ways: by proposing an
inadmissible intervention (judgment) or by never producing a checkable record
at all (compliance). The gates already separate these verdicts per episode;
this module aggregates the compliance side into rates a model team can act
on — schema validity, grammar adherence, canonical parameters — plus the
token and latency figures for what actually crossed the wire.

Everything here computes from stored traces, so it works on any past run
without new API calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from admitbench.record import REVERSIBILITY
from admitbench.rulebook import RuleBook


@dataclass
class ComplianceStats:
    episodes: int = 0
    schema_valid: int = 0        # a record parsed at all
    grammar_valid: int = 0       # parsed and the action exists in the grammar
    fields_valid: int = 0        # confidence in [0,1] and reversibility in enum
    commit_records: int = 0      # parsed commit-class actions
    params_canonical: int = 0    # commit-class actions carrying numeric delta_pct
    prompt_chars: list = field(default_factory=list)
    input_tokens: list = field(default_factory=list)
    output_tokens: list = field(default_factory=list)
    latency_s: list = field(default_factory=list)

    def rates(self) -> dict:
        def rate(n, d):
            return round(n / d, 4) if d else None

        def stats(xs):
            if not xs:
                return None
            xs = sorted(xs)
            return {
                "mean": round(sum(xs) / len(xs), 2),
                "p95": round(xs[min(int(0.95 * len(xs)), len(xs) - 1)], 2),
            }

        return {
            "episodes": self.episodes,
            "schema_valid_rate": rate(self.schema_valid, self.episodes),
            "grammar_valid_rate": rate(self.grammar_valid, self.schema_valid),
            "fields_valid_rate": rate(self.fields_valid, self.schema_valid),
            "params_canonical_rate": rate(self.params_canonical, self.commit_records),
            "instruction_following_rate": rate(self.fields_valid, self.episodes),
            "prompt_chars": stats(self.prompt_chars),
            "input_tokens": stats([x for x in self.input_tokens if x]),
            "output_tokens": stats([x for x in self.output_tokens if x]),
            "latency_s": stats([x for x in self.latency_s if x]),
        }


def compliance_from_traces(traces: list[dict], rulebook) -> ComplianceStats:
    """Aggregate compliance over stored episode traces (one model's episodes).

    `rulebook` may be a RuleBook, or a dict of cartridge id → RuleBook when the
    traces span cartridges — grammar validity must be judged against the
    grammar the episode actually ran under."""
    stats = ComplianceStats()
    books = rulebook if isinstance(rulebook, dict) else None
    for trace in traces:
        stats.episodes += 1
        prompts = trace.get("prompts") or {}
        if prompts:
            stats.prompt_chars.append(
                len(prompts.get("system", "")) + len(prompts.get("user", ""))
            )
        completion = trace.get("completion") or {}
        stats.input_tokens.append(completion.get("input_tokens", 0))
        stats.output_tokens.append(completion.get("output_tokens", 0))
        stats.latency_s.append(completion.get("latency_s", 0))

        record = trace.get("action_record")
        if not record:
            continue
        stats.schema_valid += 1
        book = books.get((trace.get("cartridge") or {}).get("id")) if books else rulebook
        if book is None:
            continue
        rule = book.get(record.get("action", ""))
        if rule is None:
            continue
        stats.grammar_valid += 1
        confidence = record.get("confidence")
        if (
            isinstance(confidence, (int, float))
            and 0.0 <= confidence <= 1.0
            and record.get("reversibility") in REVERSIBILITY
        ):
            stats.fields_valid += 1
        if rule.commits_state() and rule.action != "adjust_setpoint":
            stats.commit_records += 1
            params = record.get("params") or {}
            value = params.get("delta_pct")
            if isinstance(value, (int, float)):
                stats.params_canonical += 1
    return stats


def compliance_table(per_model: dict[str, ComplianceStats]) -> str:
    """Markdown: one row per model, the rates a model team can act on."""
    lines = [
        "| model | IFR | schema | grammar | fields | canonical params | latency mean/p95 (s) | out tokens mean |",
        "|---|---|---|---|---|---|---|---|",
    ]

    def fmt(x):
        return "—" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))

    for model, stats in per_model.items():
        r = stats.rates()
        lat = r["latency_s"] or {}
        out = r["output_tokens"] or {}
        lines.append(
            f"| {model} | {fmt(r['instruction_following_rate'])} | {fmt(r['schema_valid_rate'])} | "
            f"{fmt(r['grammar_valid_rate'])} | {fmt(r['fields_valid_rate'])} | "
            f"{fmt(r['params_canonical_rate'])} | {fmt(lat.get('mean'))}/{fmt(lat.get('p95'))} | "
            f"{fmt(out.get('mean'))} |"
        )
    return "\n".join(lines)
