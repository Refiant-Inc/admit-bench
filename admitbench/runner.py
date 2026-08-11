"""The episode loop: situation → model → record → gates → score → trace.

Every run leaves a replayable trace on disk: the evidence with its trust and
timing, the prompts, the raw model text, the parsed record, every gate verdict
with its violations, the trajectory summaries, and the score. Any verdict can
be re-derived from the trace alone — that is gate T6's whole point.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from admitbench import __version__
from admitbench.provenance import git_rev
from admitbench.ablation import AblationSpec, apply_ablation
from admitbench.cartridge import Cartridge, Case
from admitbench.gates import GateReport, run_gates
from admitbench.hypotheses import candidate_hypotheses
from admitbench.monotonicity import CautionComparison, cmt_score, compare_caution
from admitbench.parser import parse_action_record
from admitbench.prompts import render_system, render_user
from admitbench.providers import Provider, ProviderError
from admitbench.record import ActionRecord, BOLDNESS
from admitbench.scoring import Score, exposure, pass_rate, score_episode


@dataclass
class EpisodeResult:
    cartridge_id: str
    case_id: str
    provider: str
    model: str
    ablation: Optional[AblationSpec] = None
    raw_text: str = ""
    record: Optional[ActionRecord] = None
    parse_error: Optional[str] = None
    gate_report: Optional[GateReport] = None
    artifacts: dict = field(default_factory=dict)
    score: Optional[Score] = None
    trace: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        suffix = f"+{self.ablation.id}" if self.ablation else ""
        return f"{self.case_id}{suffix}"


def _write_json(path: Path, data: dict) -> None:
    """Atomic write: a kill mid-write must not leave truncated JSON in a run dir."""
    import os
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _result_from_trace(trace: dict) -> "EpisodeResult":
    """A light EpisodeResult reconstructed from a stored trace (for resume)."""
    record = ActionRecord.from_dict(trace["action_record"]) if trace.get("action_record") else None
    sc = trace["score"]
    score = Score(
        verdict=sc["verdict"], aggregate=sc["aggregate"], tiers=sc.get("tiers") or {},
        first_failing=sc.get("first_failing"), aas_code=sc.get("aas_code"),
    )
    result = EpisodeResult(
        cartridge_id=(trace.get("cartridge") or {}).get("id", ""),
        case_id=trace.get("case_id", ""),
        provider=trace.get("provider", ""),
        model=trace.get("model", ""),
        record=record,
        parse_error=trace.get("parse_error"),
        score=score,
    )
    result.trace = trace
    return result


def _escalation_outcome(world, state, case: Case, response_time_s: float, horizon_s: float) -> dict:
    """What escalating actually buys: the plant runs unattended for the response
    window, then the human applies the professional response."""
    window = world.project(state, None, None, horizon_s=response_time_s)
    oracle = case.oracle or {}
    after = world.project(
        window.final_state,
        oracle.get("action"),
        oracle.get("params"),
        horizon_s=max(horizon_s - response_time_s, 0.0),
    )
    return {
        "min_margin": min(window.min_margin, after.min_margin),
        "crossed": window.crossed or after.crossed,
        "handoff_margin": window.min_margin,
    }


def run_episode(
    cartridge: Cartridge,
    case: Case,
    provider: Provider,
    ablation: Optional[AblationSpec | dict] = None,
    prompt_style: str = "narrative",
    max_tokens: Optional[int] = None,
) -> EpisodeResult:
    spec = AblationSpec.from_dict(ablation) if isinstance(ablation, dict) else ablation

    log = cartridge.evidence_log_for(case)
    if spec is not None:
        log = apply_ablation(log, spec)
    ctx = cartridge.context_for(case)
    system = render_system(cartridge, style=prompt_style)
    user = render_user(cartridge, case, log, ctx, style=prompt_style)

    play = getattr(provider, "play", None)
    completion_meta: dict = {}
    if callable(play):
        text = play(cartridge, case, log, ctx)
    else:
        kwargs = {"max_tokens": max_tokens} if max_tokens else {}
        completion = provider.complete(system, user, **kwargs)
        text = completion.text
        completion_meta = {
            "latency_s": completion.latency_s,
            "input_tokens": completion.input_tokens,
            "output_tokens": completion.output_tokens,
            "cost_usd": completion.cost_usd,
            "finish_reason": completion.finish_reason,
            "truncated": completion.truncated,
        }

    record, parse_error = parse_action_record(text)
    world = cartridge.world()
    hypotheses = candidate_hypotheses(cartridge, log, ctx)
    gate_report, artifacts = run_gates(
        record,
        case,
        cartridge.rulebook,
        log,
        ctx,
        world,
        parse_error=parse_error,
        horizon_s=cartridge.horizon_s,
    )

    artifacts["hypotheses"] = [h.to_dict() for h in hypotheses]
    state = world.initial_state(case.initial_state)
    if case.oracle:
        oracle_traj = world.project(
            state, case.oracle["action"], case.oracle.get("params"), horizon_s=cartridge.horizon_s
        )
        artifacts["oracle_trajectory"] = oracle_traj.summary()
    if record is not None:
        rule = cartridge.rulebook.get(record.action)
        if rule is not None and BOLDNESS[rule.action_class] <= BOLDNESS["escalate"]:
            artifacts["escalation_outcome_trajectory"] = _escalation_outcome(
                world, state, case, ctx.response_time_s, cartridge.horizon_s
            )

    score = score_episode(record, case, cartridge.rulebook, gate_report, artifacts, cartridge.weights)

    result = EpisodeResult(
        cartridge_id=cartridge.id,
        case_id=case.id,
        provider=provider.name,
        model=provider.model,
        ablation=spec,
        raw_text=text,
        record=record,
        parse_error=parse_error,
        gate_report=gate_report,
        artifacts=artifacts,
        score=score,
    )
    result.trace = {
        "admitbench_version": __version__,
        "provenance": {"git_rev": git_rev(), "cartridge_hash": cartridge.content_hash()},
        "cartridge": {"id": cartridge.id, "version": cartridge.manifest.get("version")},
        "case_id": case.id,
        "ablation": spec.to_dict() if spec else None,
        "provider": provider.name,
        "model": provider.model,
        "completion": completion_meta,
        "prompt_style": prompt_style,
        "sampling": {"max_tokens": max_tokens or 4000, "temperature": 0.0},
        "context": ctx.to_dict(),
        "evidence": log.to_dicts(),
        "ledger_head": log.ledger_head,
        "prompts": {"system": system, "user": user},
        "raw_model_text": text,
        "parse_error": parse_error,
        "action_record": record.to_dict() if record else None,
        "gates": gate_report.to_dict(),
        "artifacts": artifacts,
        "score": score.to_dict(),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    return result


@dataclass
class SuiteResult:
    cartridge_id: str
    provider: str
    model: str
    results: list[EpisodeResult] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)  # episodes lost to provider faults
    skipped: int = 0  # episodes restored from existing traces on resume
    out_dir: Optional[str] = None

    def scores(self) -> list[Score]:
        return [r.score for r in self.results]

    def records(self) -> list[Optional[ActionRecord]]:
        return [r.record for r in self.results]

    def pass_rate(self) -> float:
        return pass_rate(self.scores())

    def exposure(self, rulebook, weights=None) -> float:
        return exposure(self.scores(), self.records(), rulebook, weights)


def run_suite(
    cartridge: Cartridge,
    provider: Provider,
    case_ids: Optional[list[str]] = None,
    out_dir: Optional[str | Path] = None,
    prompt_style: str = "narrative",
    max_tokens: Optional[int] = None,
    resume: bool = False,
) -> SuiteResult:
    """One model over a cartridge's cases. A provider fault loses that episode,
    never the suite; with `resume`, episodes whose traces already exist in
    `out_dir` are restored from disk instead of re-run (and re-paid)."""
    cases = [cartridge.case(cid) for cid in case_ids] if case_ids else cartridge.cases
    suite = SuiteResult(cartridge_id=cartridge.id, provider=provider.name, model=provider.model)

    directory: Optional[Path] = None
    if out_dir:
        directory = Path(out_dir)
        directory.mkdir(parents=True, exist_ok=True)
        suite.out_dir = str(directory)

    for case in cases:
        trace_path = directory / f"{case.id}.json" if directory else None
        if resume and trace_path and trace_path.exists():
            try:
                suite.results.append(_result_from_trace(json.loads(trace_path.read_text(encoding="utf-8"))))
                suite.skipped += 1
                continue
            except (json.JSONDecodeError, KeyError):
                pass  # truncated or foreign file: re-run the episode
        try:
            result = run_episode(
                cartridge, case, provider, prompt_style=prompt_style, max_tokens=max_tokens
            )
        except ProviderError as exc:
            suite.errors.append({"case_id": case.id, "error": str(exc)})
            continue
        suite.results.append(result)
        if trace_path:
            _write_json(trace_path, result.trace)
    return suite


# ---------------------------------------------------------------------------
# Caution monotonicity runs
# ---------------------------------------------------------------------------

@dataclass
class CMTResult:
    cartridge_id: str
    case_id: str
    provider: str
    model: str
    score: Optional[float] = None  # None = no pair was evaluable (silent model)
    full: Optional[EpisodeResult] = None
    pairs: list[tuple[AblationSpec, EpisodeResult, CautionComparison]] = field(default_factory=list)

    def violations(self) -> list[str]:
        return [code for _, _, cmp in self.pairs for code in cmp.violations]

    def to_dict(self) -> dict:
        return {
            "cartridge": self.cartridge_id,
            "case": self.case_id,
            "provider": self.provider,
            "model": self.model,
            "score": self.score,
            "evaluable_pairs": sum(
                1 for _, _, cmp in self.pairs if cmp.comparable or cmp.violations
            ),
            "pairs": [
                {
                    "ablation": spec.to_dict(),
                    "degraded_action": (res.record.action if res.record else None),
                    "comparison": cmp.to_dict(),
                }
                for spec, res, cmp in self.pairs
            ],
        }


def run_cmt_case(
    cartridge: Cartridge,
    case: Case,
    provider: Provider,
    full: Optional[EpisodeResult] = None,
    trace_dir: Optional[Path] = None,
) -> Optional[CMTResult]:
    """Pass `full` to reuse an already-run episode instead of paying for it
    twice; degraded episodes' complete traces are persisted when `trace_dir`
    is given — T6 applies to ablated runs too."""
    specs = [AblationSpec.from_dict(a) for a in case.ablations]
    if not specs:
        return None

    if full is None:
        full = run_episode(cartridge, case, provider)
    result = CMTResult(
        cartridge_id=cartridge.id,
        case_id=case.id,
        provider=provider.name,
        model=provider.model,
        full=full,
    )
    for spec in specs:
        degraded = run_episode(cartridge, case, provider, ablation=spec)
        if trace_dir:
            _write_json(trace_dir / f"{degraded.label}.json", degraded.trace)
        comparison = compare_caution(full.record, degraded.record, spec, cartridge.rulebook)
        result.pairs.append((spec, degraded, comparison))

    result.score = cmt_score(
        [cmp for _, _, cmp in result.pairs],
        [res.record for _, res, _ in result.pairs],
        cartridge.rulebook,
    )
    return result


def run_cmt_suite(
    cartridge: Cartridge,
    provider: Provider,
    case_ids: Optional[list[str]] = None,
    out_dir: Optional[str | Path] = None,
    full_results: Optional[dict] = None,
) -> list[CMTResult]:
    """`full_results` maps case_id → the already-run base EpisodeResult, so a
    `run --cmt` pays for each base episode exactly once."""
    cases = [cartridge.case(cid) for cid in case_ids] if case_ids else cartridge.cases
    results = []
    directory: Optional[Path] = None
    if out_dir:
        directory = Path(out_dir)
        directory.mkdir(parents=True, exist_ok=True)
    for case in cases:
        try:
            cmt = run_cmt_case(
                cartridge, case, provider,
                full=(full_results or {}).get(case.id),
                trace_dir=directory,
            )
        except ProviderError:
            continue
        if cmt is not None:
            results.append(cmt)
            if directory:
                _write_json(directory / f"cmt_{case.id}.json", cmt.to_dict())
    return results
