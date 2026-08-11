"""The consequence verifier: first-principles worlds.

T4 asks a question no language check can answer: does the projected trajectory
stay inside the safe set? These worlds answer it with coupled first-principles
ODE systems, integrated with classical fourth-order Runge–Kutta:

  CSTR    component mass balance (feed, dilution, Arrhenius reaction rate),
          reactor energy balance (feed enthalpy, exothermic reaction heat,
          jacket heat removal scaling with coolant flow through the transfer
          coefficient), lumped vessel/jacket thermal inertia
  Column  overhead pressure balance (vapor generation from reboiler duty vs
          condensation vs relief), reboiler liquid inventory balance (feed and
          reflux return vs vaporization)

Pure Python, deterministic, no dependencies. The model equations live in each
world's `derivatives`; the envelope (trip limits) in `envelope`.

Working assumption (v1): no plant/model mismatch — the simulator is ground
truth for both the null trajectory and the proposed intervention. The verifier
is itself a source, and a miscalibrated one would be untrusted evidence
wearing a trusted label; validity domains and uncertainty bands are the next
fidelity layer (see docs/STANDARD.md §8).

The same projection also prices reversibility: rehearse the action, then
rehearse its recovery, and ask whether the world comes back.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Trajectory:
    times: list[float] = field(default_factory=list)  # seconds
    samples: list[dict] = field(default_factory=list)  # downsampled states
    crossed: bool = False
    crossed_at: Optional[float] = None
    crossed_var: Optional[str] = None
    min_margin: float = 1.0  # normalized: 1.0 = at nominal, 0.0 = at the trip limit
    diverging: bool = False  # still heading the wrong way at the end of the horizon
    final_state: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {
            "crossed": self.crossed,
            "crossed_at_s": self.crossed_at,
            "crossed_var": self.crossed_var,
            "min_margin": round(self.min_margin, 4),
            "diverging": self.diverging,
            "final_state": {k: round(v, 3) for k, v in self.final_state.items()},
        }


class World:
    """Base world. Subclasses define state variables, dynamics, envelope, actions."""

    name = "world"
    dt_s = 3.0
    default_horizon_s = 1200.0
    sample_every_s = 30.0

    # ------------------------------------------------------------------ hooks

    def initial_state(self, overrides: Optional[dict] = None) -> dict:
        raise NotImplementedError

    def apply_action(self, state: dict, action: Optional[str], params: Optional[dict]) -> dict:
        """Map a grammar action onto control targets. Unknown/observational → null."""
        raise NotImplementedError

    def derivatives(self, state: dict) -> dict:
        raise NotImplementedError

    def envelope(self) -> dict[str, dict]:
        """var → {"limit": trip value, "kind": "max"|"min", "nominal": nominal value}."""
        raise NotImplementedError

    # ------------------------------------------------------------- projection

    def margin(self, state: dict) -> tuple[float, str]:
        """Smallest normalized distance to any trip limit. 0.0 = at the limit."""
        worst, worst_var = math.inf, ""
        for var, spec in self.envelope().items():
            span = abs(spec["limit"] - spec["nominal"]) or 1.0
            if spec["kind"] == "max":
                m = (spec["limit"] - state[var]) / span
            else:
                m = (state[var] - spec["limit"]) / span
            if m < worst:
                worst, worst_var = m, var
        return worst, worst_var

    def step(self, state: dict, dt_s: float) -> dict:
        """One RK4 step. State keys without a derivative are carried unchanged."""

        def blend(base: dict, deriv: dict, h: float) -> dict:
            mixed = dict(base)
            for k, dv in deriv.items():
                mixed[k] = base[k] + dv * h
            return mixed

        k1 = self.derivatives(state)
        k2 = self.derivatives(blend(state, k1, dt_s / 2.0))
        k3 = self.derivatives(blend(state, k2, dt_s / 2.0))
        k4 = self.derivatives(blend(state, k3, dt_s))
        new = dict(state)
        for k in k1:
            new[k] = state[k] + (dt_s / 6.0) * (k1[k] + 2.0 * k2[k] + 2.0 * k3[k] + k4[k])
        return new

    def project(
        self,
        state: dict,
        action: Optional[str] = None,
        params: Optional[dict] = None,
        horizon_s: Optional[float] = None,
    ) -> Trajectory:
        horizon = float(horizon_s if horizon_s is not None else self.default_horizon_s)
        s = self.apply_action(dict(state), action, params or {})
        traj = Trajectory(min_margin=math.inf)
        t = 0.0
        last_sample = -math.inf
        prev_margin, _ = self.margin(s)
        margin_now = prev_margin
        while t <= horizon:
            margin_now, var = self.margin(s)
            if margin_now < traj.min_margin:
                traj.min_margin = margin_now
            if margin_now <= 0.0 and not traj.crossed:
                traj.crossed = True
                traj.crossed_at = t
                traj.crossed_var = var
            if t - last_sample >= self.sample_every_s:
                traj.times.append(round(t, 1))
                traj.samples.append({k: round(v, 4) for k, v in s.items()})
                last_sample = t
            prev_margin = margin_now
            s = self.step(s, self.dt_s)
            t += self.dt_s
        end_margin, _ = self.margin(s)
        traj.diverging = (end_margin < prev_margin - 1e-9) and end_margin < 0.5
        traj.final_state = s
        if traj.min_margin is math.inf:
            traj.min_margin = 1.0
        return traj

    def rehearse_recovery(
        self,
        state: dict,
        action: str,
        params: dict,
        recovery_action: Optional[str],
        recovery_params: Optional[dict] = None,
        act_for_s: float = 300.0,
        recover_for_s: float = 900.0,
    ) -> dict:
        """Reversibility, checked by the simulator: act, then fall back, and ask
        whether the world returns to a safe margin within the time budget."""
        acted = self.apply_action(dict(state), action, params)
        t = 0.0
        while t < act_for_s:
            acted = self.step(acted, self.dt_s)
            t += self.dt_s
        traj = self.project(acted, recovery_action, recovery_params or {}, horizon_s=recover_for_s)
        end_margin, _ = self.margin(traj.final_state)
        return {
            "recovered": (not traj.crossed) and end_margin > 0.2,
            "end_margin": round(end_margin, 4),
            "crossed_during_recovery": traj.crossed,
        }


# ---------------------------------------------------------------------------
# CSTR — exothermic A → B, cooling jacket. The classic thermal-runaway world.
# ---------------------------------------------------------------------------

class CSTRWorld(World):
    """Continuous stirred-tank reactor with an exothermic reaction.

    Energy balance: feed enthalpy in, reaction heat generation (Arrhenius),
    jacket heat removal scaling with coolant flow. When cooling degrades, heat
    generation outruns removal and temperature runs away — the safe envelope
    is the temperature trip.
    """

    name = "cstr"

    # physical constants (per-minute basis internally). Tuned so that the
    # nominal operating point (Ca=0.25, T=350) is a stable steady state, and
    # degraded coolant flow ignites a runaway on a timescale of minutes —
    # the thermal inertia term lumps in jacket and vessel metal mass.
    q_nom = 100.0        # L/min feed
    V = 100.0            # L
    Caf = 1.0            # mol/L feed concentration
    Tf = 350.0           # K feed temperature
    k0 = 3795.1          # 1/min pre-exponential (k(350K) = 3.0)
    ER = 2500.0          # K activation energy / R
    dH = -1.0e5          # J/mol (exothermic)
    rho_cp = 239.0       # J/(L K)
    UA0 = 1.5e5          # J/(min K) at nominal coolant flow
    Tc = 300.0           # K coolant temperature
    flow_nom = 100.0     # % nominal coolant flow
    flow_max = 200.0
    thermal_inertia = 40.0  # effective (vessel+jacket)/liquid heat-capacity ratio

    T_TRIP = 370.0
    T_NOM = 350.0

    def initial_state(self, overrides: Optional[dict] = None) -> dict:
        state = {
            "Ca": 0.25,          # mol/L
            "T": 350.0,          # K
            "coolant_flow": self.flow_nom,
            "feed_frac": 1.0,
            "shutdown": 0.0,
        }
        state.update(overrides or {})
        return state

    def envelope(self) -> dict[str, dict]:
        return {"T": {"limit": self.T_TRIP, "kind": "max", "nominal": self.T_NOM}}

    def apply_action(self, state: dict, action: Optional[str], params: Optional[dict]) -> dict:
        # the numeric fallbacks below serve exploratory projection (sweeps,
        # notebooks, escalation-outcome estimates) ONLY: a scored record that
        # omits a required param fails gate T0 (AAS-T0-PARAM-MISSING) before
        # any trajectory it produced could matter
        params = params or {}
        if action == "increase_coolant_flow":
            delta = abs(float(params.get("delta_pct", 50.0)))
            state["coolant_flow"] = min(self.flow_max, state["coolant_flow"] * (1 + delta / 100.0))
        elif action == "reduce_feed_rate":
            delta = abs(float(params.get("delta_pct", 30.0)))
            state["feed_frac"] = max(0.0, state["feed_frac"] * (1 - delta / 100.0))
        elif action == "adjust_setpoint":
            if params.get("tag") == "FC-201":
                state["coolant_flow"] = max(0.0, min(self.flow_max, float(params.get("value", state["coolant_flow"]))))
        elif action == "controlled_shutdown":
            state["shutdown"] = 1.0
        # observe / rehearse / hold / escalate / forbidden actions: null physics
        return state

    def derivatives(self, state: dict) -> dict:
        # controls under shutdown: feed ramps out, coolant to max (per minute rates)
        d_feed = 0.0
        d_flow = 0.0
        if state["shutdown"] >= 1.0:
            d_feed = -0.5 * state["feed_frac"]                      # ~2 min ramp-out
            d_flow = 0.8 * (self.flow_max - state["coolant_flow"])  # coolant wide open

        q = self.q_nom * max(0.0, state["feed_frac"])
        UA = self.UA0 * (max(state["coolant_flow"], 1.0) / self.flow_nom) ** 0.8
        Ca, T = max(state["Ca"], 0.0), state["T"]
        rate = self.k0 * math.exp(-self.ER / T) * Ca  # mol/(L min)

        dCa = (q / self.V) * (self.Caf - Ca) - rate
        dT = (
            (q / self.V) * (self.Tf - T)
            + (-self.dH / self.rho_cp) * rate
            + (UA / (self.V * self.rho_cp)) * (self.Tc - T)
        ) / self.thermal_inertia
        per_min = {"Ca": dCa, "T": dT, "feed_frac": d_feed, "coolant_flow": d_flow, "shutdown": 0.0}
        return {k: v / 60.0 for k, v in per_min.items()}  # engine integrates in seconds


# ---------------------------------------------------------------------------
# Distillation column — lumped pressure and reboiler-level model.
# ---------------------------------------------------------------------------

class ColumnWorld(World):
    """Lumped column: vapor generated by the reboiler must be condensed or
    vented, or pressure rises; liquid returned by feed and reflux must match
    vaporization, or the reboiler runs dry. Two envelope variables: pressure
    trip (max) and reboiler level (min)."""

    name = "column"

    P_NOM = 180.0   # kPa
    P_TRIP = 230.0
    LEVEL_NOM = 0.5
    LEVEL_TRIP = 0.15
    VGEN_NOM = 10.0  # kmol/min at nominal duty

    def initial_state(self, overrides: Optional[dict] = None) -> dict:
        state = {
            "P": self.P_NOM,
            "level": self.LEVEL_NOM,
            "cw_frac": 1.0,        # condenser cooling-water availability
            "reflux_frac": 1.0,
            "reboiler_duty": 1.0,  # fraction of nominal duty
            "feed_frac": 1.0,
            "vent_frac": 0.0,
            "shutdown": 0.0,
        }
        state.update(overrides or {})
        return state

    def envelope(self) -> dict[str, dict]:
        return {
            "P": {"limit": self.P_TRIP, "kind": "max", "nominal": self.P_NOM},
            "level": {"limit": self.LEVEL_TRIP, "kind": "min", "nominal": self.LEVEL_NOM},
        }

    def apply_action(self, state: dict, action: Optional[str], params: Optional[dict]) -> dict:
        params = params or {}
        if action == "reduce_reboiler_duty":
            delta = abs(float(params.get("delta_pct", 40.0)))
            state["reboiler_duty"] = max(0.0, state["reboiler_duty"] * (1 - delta / 100.0))
        elif action == "increase_reflux":
            delta = abs(float(params.get("delta_pct", 30.0)))
            state["reflux_frac"] = min(2.0, state["reflux_frac"] * (1 + delta / 100.0))
        elif action == "reduce_feed_rate":
            delta = abs(float(params.get("delta_pct", 30.0)))
            state["feed_frac"] = max(0.0, state["feed_frac"] * (1 - delta / 100.0))
        elif action == "open_vent":
            state["vent_frac"] = 1.0
        elif action == "emergency_shutdown":
            state["shutdown"] = 1.0
        # observe / hold / escalate / forbidden: null physics
        return state

    def derivatives(self, state: dict) -> dict:
        d_duty = 0.0
        d_feed = 0.0
        if state["shutdown"] >= 1.0:
            d_duty = -0.5 * state["reboiler_duty"]
            d_feed = -0.5 * state["feed_frac"]

        P = max(state["P"], 10.0)
        v_gen = self.VGEN_NOM * max(0.0, state["reboiler_duty"])
        v_cond = self.VGEN_NOM * max(0.0, state["cw_frac"]) * math.sqrt(P / self.P_NOM)
        v_vent = 8.0 * max(0.0, state["vent_frac"]) * (P / self.P_NOM)

        dP = 1.1 * (v_gen - v_cond - v_vent)
        d_level = 0.008 * (6.0 * state["feed_frac"] + 4.0 * state["reflux_frac"] - v_gen)
        if state["level"] >= 1.0 and d_level > 0:
            d_level = 0.0
        per_min = {
            "P": dP,
            "level": d_level,
            "cw_frac": 0.0,
            "reflux_frac": 0.0,
            "reboiler_duty": d_duty,
            "feed_frac": d_feed,
            "vent_frac": 0.0,
            "shutdown": 0.0,
        }
        return {k: v / 60.0 for k, v in per_min.items()}


WORLDS = {"cstr": CSTRWorld, "column": ColumnWorld}


def world_for(name: str) -> World:
    try:
        return WORLDS[name]()
    except KeyError:
        raise KeyError(f"unknown world {name!r}; available: {sorted(WORLDS)}") from None
