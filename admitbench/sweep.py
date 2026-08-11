"""Parameter sweeps: how much should you trust the numbers?

Three questions, three sweeps, all deterministic given a seed:

  bootstrap CI      resample episodes → confidence interval on the pass rate
                    and the mean aggregate. Small suites have wide intervals;
                    the report should say so rather than imply false precision.

  weight sweep      jitter the T1–T5 scoring weights and recompute every
                    aggregate. If a ranking claim survives hundreds of weight
                    perturbations, it is a claim about the models; if it
                    flips, it was a claim about the weights.

  severity sweep    scale one physical parameter of a case across a range and
                    rerun the episode. Shows where the verdict flips — e.g.
                    the coolant flow below which escalation becomes an unsafe
                    delay — which is the benchmark showing its own edges.

No dependency beyond the standard library; every function takes a seed.
"""

from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass
from typing import Optional

from admitbench.scoring import DEFAULT_WEIGHTS, VERDICT_ADMISSIBLE


@dataclass
class Interval:
    mean: float
    lo: float
    hi: float
    n: int  # number of underlying observations

    def to_dict(self) -> dict:
        return {"mean": round(self.mean, 4), "lo": round(self.lo, 4), "hi": round(self.hi, 4), "n": self.n}

    def __str__(self) -> str:
        return f"{self.mean:.3f} [{self.lo:.3f}, {self.hi:.3f}] (n={self.n})"


def bootstrap_ci(
    values: list[float], n_boot: int = 2000, seed: int = 0, alpha: float = 0.05
) -> Optional[Interval]:
    """Percentile bootstrap over the observations. None when there is nothing to resample."""
    if not values:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        sample = [values[rng.randrange(len(values))] for _ in values]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int((alpha / 2) * n_boot)]
    hi = means[min(int((1 - alpha / 2) * n_boot), n_boot - 1)]
    return Interval(mean=sum(values) / len(values), lo=lo, hi=hi, n=len(values))


def pass_rate_ci(suite, n_boot: int = 2000, seed: int = 0) -> Optional[Interval]:
    outcomes = [1.0 if r.score.verdict == VERDICT_ADMISSIBLE else 0.0 for r in suite.results]
    return bootstrap_ci(outcomes, n_boot=n_boot, seed=seed)


def aggregate_ci(suite, n_boot: int = 2000, seed: int = 0) -> Optional[Interval]:
    aggregates = [r.score.aggregate for r in suite.results if r.score.aggregate is not None]
    return bootstrap_ci(aggregates, n_boot=n_boot, seed=seed)


# ---------------------------------------------------------------------------
# Weight sweep
# ---------------------------------------------------------------------------

def _jittered_weights(rng: random.Random, base: dict, jitter: float) -> dict:
    raw = {k: v * math.exp(jitter * rng.gauss(0.0, 1.0)) for k, v in base.items()}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


@dataclass
class WeightSweep:
    default_mean: Optional[float]
    interval: Optional[Interval]  # spread of mean aggregate across weight samples
    n_samples: int
    jitter: float

    def to_dict(self) -> dict:
        return {
            "default_mean": self.default_mean,
            "across_weights": self.interval.to_dict() if self.interval else None,
            "n_samples": self.n_samples,
            "jitter": self.jitter,
        }


def weight_sweep(suite, n_samples: int = 200, jitter: float = 0.25, seed: int = 0) -> WeightSweep:
    """Recompute the suite's mean aggregate under jittered tier weights.

    Tier scores are already on the trace; only the weighting is in question.
    Hard-gate failures stay ineligible under every weighting — the sweep
    cannot resurrect an inadmissible episode, by construction.
    """
    rng = random.Random(seed)
    tier_sets = [r.score.tiers for r in suite.results if r.score.verdict == VERDICT_ADMISSIBLE]
    if not tier_sets:
        return WeightSweep(default_mean=None, interval=None, n_samples=n_samples, jitter=jitter)

    def mean_aggregate(weights: dict) -> float:
        totals = [sum(weights[t] * tiers.get(t, 0.0) for t in weights) for tiers in tier_sets]
        return sum(totals) / len(totals)

    default_mean = mean_aggregate(DEFAULT_WEIGHTS)
    samples = sorted(
        mean_aggregate(_jittered_weights(rng, DEFAULT_WEIGHTS, jitter)) for _ in range(n_samples)
    )
    interval = Interval(
        mean=sum(samples) / len(samples),
        lo=samples[int(0.025 * len(samples))],
        hi=samples[min(int(0.975 * len(samples)), len(samples) - 1)],
        n=len(tier_sets),
    )
    return WeightSweep(default_mean=round(default_mean, 4), interval=interval,
                       n_samples=n_samples, jitter=jitter)


def rank_stability(suites: list, n_samples: int = 200, jitter: float = 0.25, seed: int = 0) -> float:
    """Fraction of weight samples under which the model ranking matches the
    default-weight ranking. 1.0 = the ranking is about the models, not the weights."""
    rng = random.Random(seed)
    per_suite_tiers = [
        [r.score.tiers for r in s.results if r.score.verdict == VERDICT_ADMISSIBLE] for s in suites
    ]

    def ordering(weights: dict) -> tuple:
        means = []
        for tiers_list in per_suite_tiers:
            if not tiers_list:
                means.append(-1.0)
                continue
            totals = [sum(weights[t] * tiers.get(t, 0.0) for t in weights) for tiers in tiers_list]
            means.append(sum(totals) / len(totals))
        return tuple(sorted(range(len(means)), key=lambda i: -means[i]))

    reference = ordering(DEFAULT_WEIGHTS)
    stable = sum(
        1
        for _ in range(n_samples)
        if ordering(_jittered_weights(rng, DEFAULT_WEIGHTS, jitter)) == reference
    )
    return round(stable / n_samples, 4)


def consistency_at_k(suites: list) -> dict:
    """Verdict stability across k repeated runs of the same model on the same
    cases. A case is consistent when all k runs agree (all pass or all fail);
    everything else is flicker — and flicker at temperature 0 is a property
    of the serving stack worth reporting on its own.
    """
    from collections import defaultdict

    by_case: dict[str, list[float]] = defaultdict(list)
    for suite in suites:
        for r in suite.results:
            by_case[r.case_id].append(1.0 if r.score.verdict == VERDICT_ADMISSIBLE else 0.0)
    if not by_case:
        return {"k": 0, "cases": 0, "consistency": None}
    consistent_pass = [c for c, v in by_case.items() if all(v)]
    consistent_fail = [c for c, v in by_case.items() if not any(v)]
    flicker = sorted(c for c, v in by_case.items() if any(v) and not all(v))
    return {
        "k": min(len(v) for v in by_case.values()),
        "cases": len(by_case),
        "consistent_pass": len(consistent_pass),
        "consistent_fail": len(consistent_fail),
        "flicker_cases": flicker,
        "consistency": round((len(consistent_pass) + len(consistent_fail)) / len(by_case), 4),
        "mean_pass_rate": round(
            sum(sum(v) / len(v) for v in by_case.values()) / len(by_case), 4
        ),
    }


# ---------------------------------------------------------------------------
# Severity sweep
# ---------------------------------------------------------------------------

@dataclass
class SeverityPoint:
    value: float
    verdict: str
    action: Optional[str]
    aas_code: Optional[str]
    aggregate: Optional[float]
    null_crossed_at: Optional[float]

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "verdict": self.verdict,
            "action": self.action,
            "aas_code": self.aas_code,
            "aggregate": self.aggregate,
            "null_crossed_at": self.null_crossed_at,
        }


def severity_sweep(
    cartridge,
    provider,
    case_id: str,
    param: str,
    values: list[float],
) -> list[SeverityPoint]:
    """Rerun one case with `initial_state[param]` swept across `values`.

    The evidence text is left as authored — the sweep probes the physics side
    (where does the hazard become imminent; where does a verdict flip), not
    the narrative side.
    """
    from admitbench.runner import run_episode  # local import: sweep ← runner is one-way

    base = cartridge.case(case_id)
    world = cartridge.world()
    points = []
    for value in values:
        case = copy.deepcopy(base)
        case.initial_state[param] = value
        result = run_episode(cartridge, case, provider)
        null = world.project(world.initial_state(case.initial_state), horizon_s=cartridge.horizon_s)
        points.append(
            SeverityPoint(
                value=value,
                verdict=result.score.verdict,
                action=result.record.action if result.record else None,
                aas_code=result.score.aas_code,
                aggregate=result.score.aggregate,
                null_crossed_at=null.crossed_at,
            )
        )
    return points
