"""The capability layer, exercised without a transport."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from ecoloop import config, runner, tools
from ecoloop.contracts import RunPeriod, RunSpec
from ecoloop.policy import Band, Policy

WINDOW = "01-15:01-16"


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """A real completed run, in a runs directory of our own."""
    original = config.RUNS
    config.RUNS = tmp_path_factory.mktemp("runs")
    runner.run(
        RunSpec(
            label="probe",
            model=config.DEFAULT_MODEL,
            weather=config.DEFAULT_WEATHER,
            run_period=RunPeriod.parse(WINDOW),
            output_dir=config.RUNS / "probe",
        )
    )
    yield config.RUNS
    config.RUNS = original


def test_inspect_model_reports_the_control_surface():
    surface = tools.inspect_model("RefBldgMediumOfficeNew2004_Chicago.idf")
    assert surface.zones == 18
    assert len(surface.conditioned_zones) == 15
    assert surface.air_loops == ["VAV_1", "VAV_2", "VAV_3"]
    assert surface.supply_air_schedules == ["Seasonal-Reset-Supply-Air-Temp-Sch"]


def test_unknown_names_say_what_is_available():
    with pytest.raises(ValueError, match="available"):
        tools.inspect_model("no-such-building")


def test_check_policy_clamps_without_running_anything():
    review = tools.check_policy(
        Policy(occupied=Band(heating=18.0, cooling=29.0), supply_air_temperature=25.0)
    )
    assert review.accepted.occupied == Band(heating=21.0, cooling=24.0)
    assert review.accepted.supply_air_temperature == 18.0
    assert set(review.clamped) == {"occupied.heating", "occupied.cooling", "supply_air"}


@pytest.mark.slow
def test_telemetry_is_downsampled_not_dumped(runs):
    window = tools.telemetry("probe", points=10)
    assert window.timesteps == 288
    assert window.returned <= 10
    assert set(window.data[0]) == {
        "time",
        "outdoor_temp",
        "demand_kw",
        "supply_air_setpoint",
        "zone_temp_min",
        "zone_temp_mean",
        "zone_temp_max",
    }


@pytest.mark.slow
def test_telemetry_respects_a_time_window(runs):
    window = tools.telemetry("probe", start=datetime(2023, 1, 16), points=1000)
    assert window.timesteps == 145
    assert window.start >= datetime(2023, 1, 16)


@pytest.mark.slow
def test_digest_size_does_not_track_run_length(runs):
    """Prompt cost is bounded by the number of zones, not by how long the run has been going."""
    digest = tools.state_digest("probe", datetime(2023, 1, 16, 7))
    assert len(digest.zones) == 15
    assert len(json.dumps(digest.model_dump(mode="json"))) < 4000


@pytest.mark.slow
def test_errors_are_returned_as_counts(runs):
    report = tools.run_errors("probe")
    assert report.severe == 0
    assert not report.failed


@pytest.mark.slow
def test_evaluate_policy_scores_against_the_untouched_baseline(runs):
    neutral = tools.evaluate_policy(Policy(supply_air_temperature=12.8), period=WINDOW)
    assert neutral.electricity_pct == pytest.approx(0.0, abs=0.01)

    raised = tools.evaluate_policy(Policy(supply_air_temperature=18.0), period=WINDOW)
    assert raised.electricity_pct < -5.0
    assert raised.baseline.electricity_kwh == neutral.baseline.electricity_kwh
