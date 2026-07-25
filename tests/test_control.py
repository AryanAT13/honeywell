"""Closed loop gates: commands reach the running instance, and arms compare pairwise."""

from __future__ import annotations

import pandas as pd
import pytest

from ecoloop import config, experiment
from ecoloop.contracts import RunPeriod

ZONE = "Core_bottom"


def _arms(names, period, tmp_path_factory):
    return experiment.run_arms(
        [experiment.ARMS[name] for name in names],
        model=config.DEFAULT_MODEL,
        weather=config.DEFAULT_WEATHER,
        period=RunPeriod.parse(period),
        output_root=tmp_path_factory.mktemp("arms"),
    )


@pytest.fixture(scope="module")
def summer(tmp_path_factory):
    """A July window, where supply air reset is correctly inactive."""
    return _arms(["baseline", "deadband"], config.PERIODS["smoke"], tmp_path_factory)


@pytest.fixture(scope="module")
def winter(tmp_path_factory):
    """A January window, where terminal reheat dominates and reset has something to do."""
    return _arms(["baseline", "supervisor"], "01-15:01-17", tmp_path_factory)


@pytest.mark.slow
def test_commands_reach_the_running_instance(summer):
    baseline, controlled = (pd.read_parquet(r.telemetry) for r in summer)
    occupied = controlled[f"{ZONE}|occupancy"] > 0
    assert controlled.loc[occupied, f"{ZONE}|cool_sp"].to_numpy() == pytest.approx(
        controlled.loc[occupied, f"{ZONE}|cmd_cool_sp"].to_numpy()
    )
    was, now = (frame.loc[occupied, f"{ZONE}|cool_sp"] for frame in (baseline, controlled))
    assert (now > was).any()


@pytest.mark.slow
def test_arms_share_one_uniform_clock(summer):
    """The system timestep subdivides adaptively, so an arm-dependent clock is a real risk."""
    baseline, controlled = (pd.read_parquet(r.telemetry) for r in summer)
    assert baseline["time"].equals(controlled["time"])
    assert baseline["time"].diff().dropna().nunique() == 1


@pytest.mark.slow
def test_widening_the_band_buys_energy_from_occupants(summer):
    baseline, controlled = (result.kpis for result in summer)
    assert controlled.electricity_kwh < baseline.electricity_kwh
    assert controlled.comfort_exceedance_hours > 4 * baseline.comfort_exceedance_hours
    assert controlled.comfort_degree_hours > baseline.comfort_degree_hours


@pytest.mark.slow
def test_supply_air_reset_moves_the_setpoint_and_saves_energy(winter):
    baseline, supervisor = (pd.read_parquet(r.telemetry) for r in winter)
    assert baseline["supply_air_setpoint"].nunique() == 1
    assert supervisor["supply_air_setpoint"].max() > baseline["supply_air_setpoint"].max() + 1.0

    report = experiment.compare(winter)
    assert report.arms[1].electricity_pct < -5.0


@pytest.mark.slow
def test_supply_air_reset_leaves_thermostats_untouched(winter):
    """The arm changes supply air only, so its zone setpoints must match the baseline."""
    baseline, supervisor = (pd.read_parquet(r.telemetry) for r in winter)
    for field in ("heat_sp", "cool_sp"):
        assert supervisor[f"{ZONE}|{field}"].to_numpy() == pytest.approx(
            baseline[f"{ZONE}|{field}"].to_numpy()
        )
    assert supervisor.filter(like="cmd_").isna().all().all()
