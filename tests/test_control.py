"""Phase 1 gate: commands reach the running instance, and arms compare pairwise."""

from __future__ import annotations

import pandas as pd
import pytest

from ecoloop import config, experiment
from ecoloop.contracts import RunPeriod

ZONE = "Core_bottom"


@pytest.fixture(scope="module")
def arms(tmp_path_factory):
    return experiment.run_arms(
        [experiment.ARMS["baseline"], experiment.ARMS["deadband"]],
        model=config.DEFAULT_MODEL,
        weather=config.DEFAULT_WEATHER,
        period=RunPeriod.parse(config.PERIODS["smoke"]),
        output_root=tmp_path_factory.mktemp("arms"),
    )


@pytest.fixture(scope="module")
def telemetry(arms):
    return [pd.read_parquet(result.telemetry) for result in arms]


@pytest.mark.slow
def test_commands_reach_the_running_instance(telemetry):
    _, controlled = telemetry
    commanded = controlled[f"{ZONE}|cmd_cool_sp"].notna()
    assert commanded.any(), "controller never issued a command"
    assert controlled.loc[commanded, f"{ZONE}|cool_sp"].to_numpy() == pytest.approx(
        controlled.loc[commanded, f"{ZONE}|cmd_cool_sp"].to_numpy()
    )


@pytest.mark.slow
def test_released_zones_revert_to_their_schedule(telemetry):
    """Actuator overrides can latch, leaving a zone stuck on the last commanded value."""
    baseline, controlled = telemetry
    released = controlled[f"{ZONE}|cmd_cool_sp"].isna()
    assert released.any()
    assert controlled.loc[released, f"{ZONE}|cool_sp"].to_numpy() == pytest.approx(
        baseline.loc[released, f"{ZONE}|cool_sp"].to_numpy()
    )


@pytest.mark.slow
def test_arms_share_one_uniform_clock(telemetry):
    """The system timestep subdivides adaptively, so an arm-dependent clock is a real risk."""
    baseline, controlled = telemetry
    assert baseline["time"].equals(controlled["time"])
    assert baseline["time"].diff().dropna().nunique() == 1


@pytest.mark.slow
def test_control_moves_energy_against_the_paired_baseline(arms):
    report = experiment.compare(arms)
    controlled = report.arms[1]
    assert controlled.electricity_pct < -0.5
    assert experiment.paired_frame(arms)["deadband|kwh_saved"].iloc[-1] > 0


@pytest.mark.slow
def test_unmet_hours_alone_would_hide_the_comfort_cost(arms):
    """Occupants get materially worse off while unmet hours fail to register it.

    This is why comfort is scored against a fixed band rather than the live setpoint.
    """
    baseline, controlled = (result.kpis for result in arms)
    assert controlled.comfort_degree_hours > 1.5 * baseline.comfort_degree_hours
    assert controlled.unmet_hours <= baseline.unmet_hours
