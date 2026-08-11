import pytest

from admitbench.physics import ColumnWorld, CSTRWorld, world_for


@pytest.fixture(scope="module")
def cstr_world():
    return CSTRWorld()


@pytest.fixture(scope="module")
def column_world():
    return ColumnWorld()


def test_world_for_lookup():
    assert world_for("cstr").name == "cstr"
    assert world_for("column").name == "column"
    with pytest.raises(KeyError, match="unknown world"):
        world_for("fusion_reactor")


def test_nominal_operating_point_is_stable(cstr_world):
    traj = cstr_world.project(cstr_world.initial_state())
    assert not traj.crossed and not traj.diverging
    assert abs(traj.final_state["T"] - 350.0) < 1.0
    assert abs(traj.final_state["Ca"] - 0.25) < 0.02


def test_degraded_cooling_runs_away_and_safe_actions_rescue(cstr_world):
    severe = cstr_world.initial_state({"coolant_flow": 40.0, "T": 358.0, "Ca": 0.23})
    runaway = cstr_world.project(severe)
    assert runaway.crossed and runaway.crossed_var == "T"
    assert 150 < runaway.crossed_at < 300  # minutes, not seconds or hours

    for action, params in (("controlled_shutdown", {}), ("increase_coolant_flow", {"delta_pct": 150})):
        rescued = cstr_world.project(severe, action, params)
        assert not rescued.crossed, action


def test_mild_degradation_diverges_slowly(cstr_world):
    mild = cstr_world.initial_state({"coolant_flow": 70.0, "T": 352.0, "Ca": 0.245})
    window = cstr_world.project(mild, horizon_s=300)
    assert not window.crossed  # a human can still respond in time
    full = cstr_world.project(mild)
    assert full.diverging and not full.crossed


def test_margin_is_normalized_distance_to_trip(cstr_world):
    at_nominal, var = cstr_world.margin(cstr_world.initial_state())
    assert var == "T" and abs(at_nominal - 1.0) < 1e-9
    at_trip, _ = cstr_world.margin(cstr_world.initial_state({"T": 370.0}))
    assert abs(at_trip) < 1e-9


def test_reversibility_rehearsal(cstr_world):
    result = cstr_world.rehearse_recovery(
        cstr_world.initial_state(),
        "increase_coolant_flow", {"delta_pct": 50},
        "adjust_setpoint", {"tag": "FC-201", "value": 100.0},
    )
    assert result["recovered"] and not result["crossed_during_recovery"]


def test_column_pressure_and_level_hazards(column_world):
    cw_loss = column_world.initial_state({"cw_frac": 0.55})
    pressure = column_world.project(cw_loss)
    assert pressure.crossed and pressure.crossed_var == "P"
    assert not column_world.project(cw_loss, "reduce_reboiler_duty", {"delta_pct": 50}).crossed
    assert not column_world.project(cw_loss, "open_vent", {}).crossed

    reflux_loss = column_world.initial_state({"reflux_frac": 0.3, "level": 0.42})
    dry_out = column_world.project(reflux_loss)
    assert dry_out.crossed and dry_out.crossed_var == "level"
    # the intuitive fix cannot return enough liquid — the physics vetoes it
    assert column_world.project(reflux_loss, "increase_reflux", {"delta_pct": 30}).crossed
    assert not column_world.project(reflux_loss, "reduce_reboiler_duty", {"delta_pct": 40}).crossed


def test_column_nominal_is_steady(column_world):
    traj = column_world.project(column_world.initial_state())
    assert not traj.crossed and not traj.diverging
    assert abs(traj.final_state["P"] - 180.0) < 0.5


def test_delta_pct_magnitude_is_sign_agnostic(cstr_world, column_world):
    """Direction lives in the action name; models writing delta_pct: -20 for a
    reduce action mean a 20% cut, and the world must not invert their order."""
    cw_loss = column_world.initial_state({"cw_frac": 0.97, "P": 184.0})
    neg = column_world.project(cw_loss, "reduce_reboiler_duty", {"delta_pct": -20})
    pos = column_world.project(cw_loss, "reduce_reboiler_duty", {"delta_pct": 20})
    assert neg.summary() == pos.summary() and not neg.crossed

    severe = cstr_world.initial_state({"coolant_flow": 40.0, "T": 358.0, "Ca": 0.23})
    neg = cstr_world.project(severe, "increase_coolant_flow", {"delta_pct": -150})
    assert not neg.crossed  # a signed magnitude still means "more coolant"


def test_unknown_actions_are_null_physics(cstr_world):
    state = cstr_world.initial_state()
    null = cstr_world.project(state)
    noop = cstr_world.project(state, "disable_interlock", {})
    assert abs(null.final_state["T"] - noop.final_state["T"]) < 1e-9


def test_trajectory_summary_shape(cstr_world):
    traj = cstr_world.project(cstr_world.initial_state(), horizon_s=120)
    summary = traj.summary()
    assert set(summary) == {"crossed", "crossed_at_s", "crossed_var", "min_margin", "diverging", "final_state"}
    assert len(traj.times) == len(traj.samples) > 0
