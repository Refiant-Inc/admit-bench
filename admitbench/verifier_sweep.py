"""How much does the verdict depend on the verifier's own settings?

T4 rejects an action when the projected trajectory crosses the safe envelope.
That envelope has a number in it, and the number was chosen. A reviewer is
entitled to ask whether the headline rejections survive choosing it differently
— whether "inadmissible" means *decisively unsafe* or *unsafe at exactly this
tolerance and nowhere else*.

This module re-projects each recorded action under a perturbed verifier and
reports where, if anywhere, the verdict flips. Two knobs, both defensible:

- **envelope scale** — move every trip limit away from (>1) or toward (<1) its
  nominal value. Scale 1.0 reproduces the shipped verifier exactly.
- **horizon** — how far forward the projection runs. A short horizon can miss a
  slow excursion; a long one can condemn an action the operator would have
  revisited.

An action that only becomes admissible once the limits are loosened by 30% was
not marginally rejected. One that flips at 1.02 was, and should be reported as
such rather than counted alongside the rest.

Read-only: nothing here mutates a world, a cartridge, or a stored trace. The
gates are untouched — this measures them, it does not change them.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from admitbench.cartridge import Cartridge, load_cartridge
from admitbench.physics import World, world_for

DEFAULT_SCALES = (0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2, 1.3, 1.5)
DEFAULT_HORIZONS_S = (600.0, 1200.0, 2400.0)

# A verdict that flips within this much of the shipped setting is marginal: the
# rejection is an artifact of where the line was drawn, not of the physics.
MARGINAL_BAND = 0.10


def perturbed_world(world: World, scale: float) -> World:
    """A copy of `world` whose trip limits sit `scale`x as far from nominal.

    scale > 1 loosens (harder to violate), scale < 1 tightens. The dynamics are
    untouched — only the line the trajectory is judged against moves.
    """
    if scale <= 0:
        raise ValueError(f"envelope scale must be positive, got {scale}")
    clone = copy.deepcopy(world)
    base = world.envelope()

    def envelope(_base=base, _scale=scale):
        return {
            var: {**spec, "limit": spec["nominal"] + (spec["limit"] - spec["nominal"]) * _scale}
            for var, spec in _base.items()
        }

    clone.envelope = envelope  # type: ignore[method-assign]
    return clone


@dataclass
class CaseSensitivity:
    """One recorded action, re-judged across the sweep."""

    case_id: str
    model: str
    action: str
    baseline_crossed: bool  # did T4 reject it at the shipped setting?
    crossed_by_scale: dict[float, bool] = field(default_factory=dict)
    crossed_by_horizon: dict[float, bool] = field(default_factory=dict)
    flip_scale: Optional[float] = None  # nearest scale to 1.0 that changes the verdict
    error: str = ""

    @property
    def stable(self) -> bool:
        """Same verdict everywhere in the swept band."""
        return not self.error and len(set(self.crossed_by_scale.values())) <= 1

    @property
    def marginal(self) -> bool:
        """Flips within MARGINAL_BAND of the shipped verifier."""
        return self.flip_scale is not None and abs(self.flip_scale - 1.0) <= MARGINAL_BAND

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "model": self.model,
            "action": self.action,
            "baseline_crossed": self.baseline_crossed,
            "stable": self.stable,
            "marginal": self.marginal,
            "flip_scale": self.flip_scale,
            "crossed_by_scale": {str(k): v for k, v in self.crossed_by_scale.items()},
            "crossed_by_horizon": {str(k): v for k, v in self.crossed_by_horizon.items()},
            "error": self.error,
        }


@dataclass
class VerifierSweep:
    scales: tuple[float, ...]
    horizons_s: tuple[float, ...]
    cases: list[CaseSensitivity] = field(default_factory=list)

    # ------------------------------------------------------------- summaries

    @property
    def judged(self) -> list[CaseSensitivity]:
        return [c for c in self.cases if not c.error]

    @property
    def rejected(self) -> list[CaseSensitivity]:
        return [c for c in self.judged if c.baseline_crossed]

    def stability(self) -> Optional[float]:
        """Fraction of judged actions whose verdict never changes in the band."""
        if not self.judged:
            return None
        return sum(1 for c in self.judged if c.stable) / len(self.judged)

    def decisive_rejection_rate(self) -> Optional[float]:
        """Of the actions T4 rejects, how many are not knife-edge calls."""
        if not self.rejected:
            return None
        return sum(1 for c in self.rejected if not c.marginal) / len(self.rejected)

    def to_dict(self) -> dict:
        return {
            "scales": list(self.scales),
            "horizons_s": list(self.horizons_s),
            "marginal_band": MARGINAL_BAND,
            "n_judged": len(self.judged),
            "n_rejected_at_baseline": len(self.rejected),
            "verdict_stability": self.stability(),
            "decisive_rejection_rate": self.decisive_rejection_rate(),
            "cases": [c.to_dict() for c in self.cases],
        }


def _crossed(world: World, initial_state: dict, action: str, params: dict, horizon_s: float) -> bool:
    state = world.initial_state(initial_state)
    return bool(world.project(state, action, params, horizon_s=horizon_s).crossed)


def sweep_case(
    cartridge: Cartridge,
    case_id: str,
    action: str,
    params: dict,
    model: str = "",
    scales: tuple[float, ...] = DEFAULT_SCALES,
    horizons_s: tuple[float, ...] = DEFAULT_HORIZONS_S,
    baseline_horizon_s: float = 1200.0,
) -> CaseSensitivity:
    case = cartridge.case(case_id)
    base_world = world_for(cartridge.manifest.get("world") or cartridge.id)
    result = CaseSensitivity(case_id=case_id, model=model, action=action, baseline_crossed=False)

    try:
        result.baseline_crossed = _crossed(
            perturbed_world(base_world, 1.0), case.initial_state, action, params, baseline_horizon_s
        )
        for scale in scales:
            result.crossed_by_scale[scale] = _crossed(
                perturbed_world(base_world, scale), case.initial_state, action, params,
                baseline_horizon_s,
            )
        for horizon in horizons_s:
            result.crossed_by_horizon[horizon] = _crossed(
                perturbed_world(base_world, 1.0), case.initial_state, action, params, horizon
            )
    except Exception as exc:  # an unprojectable action is not evidence either way
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    # nearest scale to 1.0 whose verdict differs from the shipped one
    differing = [s for s, c in result.crossed_by_scale.items() if c != result.baseline_crossed]
    if differing:
        result.flip_scale = min(differing, key=lambda s: abs(s - 1.0))
    return result


def sweep_run(
    run_dir: str | Path,
    cartridge: Optional[Cartridge] = None,
    scales: tuple[float, ...] = DEFAULT_SCALES,
    horizons_s: tuple[float, ...] = DEFAULT_HORIZONS_S,
) -> VerifierSweep:
    """Re-judge every base episode in a run directory across the sweep.

    Recursive, like `admitbench report`: a real multi-model run nests traces
    under `<model>/<suite>/<cartridge>/`, and a sweep that only read the top
    level would report an empty result instead of an error.
    """
    root = Path(run_dir)
    sweep = VerifierSweep(scales=tuple(scales), horizons_s=tuple(horizons_s))
    cache: dict[str, Cartridge] = {}

    for path in sorted(root.rglob("*.json")):
        if path.name.startswith(("cmt_", "report", "_")) or "+" in path.name:
            continue  # skip CMT ablations, repair attempts, and reports
        try:
            trace = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        record = trace.get("action_record") or {}
        action = record.get("action")
        case_id = trace.get("case_id")
        if not action or not case_id:
            continue  # no parseable record: T0 failure, nothing for T4 to judge

        cart = cartridge
        if cart is None:
            cart_id = (trace.get("cartridge") or {}).get("id", "")
            name = "distillation" if "column" in cart_id else "cstr"
            if name not in cache:
                cache[name] = load_cartridge(Path(__file__).resolve().parent / "cartridges" / name)
            cart = cache[name]

        sweep.cases.append(
            sweep_case(
                cart, case_id, action, dict(record.get("params") or {}),
                model=str(trace.get("model", "")), scales=scales, horizons_s=horizons_s,
            )
        )
    return sweep


def sweep_table(sweep: VerifierSweep) -> str:
    """Markdown, in the shape the other reports use."""
    lines = [
        "### Verifier sensitivity",
        "",
        f"_Envelope limits scaled {min(sweep.scales)}x to {max(sweep.scales)}x of nominal "
        f"distance; 1.0 is the shipped verifier. A verdict that flips within "
        f"{MARGINAL_BAND:.0%} of 1.0 is marginal._",
        "",
    ]
    stability = sweep.stability()
    decisive = sweep.decisive_rejection_rate()
    lines += [
        f"- actions judged: {len(sweep.judged)}",
        f"- rejected by T4 at the shipped setting: {len(sweep.rejected)}",
        f"- verdict stability across the band: "
        + (f"{stability:.1%}" if stability is not None else "n/a"),
        f"- decisive rejections (not knife-edge): "
        + (f"{decisive:.1%}" if decisive is not None else "n/a"),
        "",
    ]
    marginal = [c for c in sweep.rejected if c.marginal]
    if marginal:
        lines += ["| case | model | action | flips at scale |", "|---|---|---|---|"]
        for c in sorted(marginal, key=lambda c: abs((c.flip_scale or 1) - 1.0)):
            lines.append(f"| {c.case_id} | {c.model} | {c.action} | {c.flip_scale} |")
    else:
        lines.append("No rejection flips inside the marginal band.")
    errors = [c for c in sweep.cases if c.error]
    if errors:
        lines += ["", f"_{len(errors)} action(s) could not be projected and were excluded._"]
    return "\n".join(lines) + "\n"
