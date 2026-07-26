"""Pointing the system at a building nobody configured it for."""

from __future__ import annotations

import pytest

from ecoloop import commissioning, config, runner
from ecoloop.contracts import RunPeriod, RunSpec
from ecoloop.strategies import AvailabilityTrim, SupplyAirReset

SMALL = config.MODELS / "RefBldgSmallOfficeNew2004_Chicago.idf"
WINDOW = "01-15:01-17"


@pytest.mark.slow
def test_a_measure_with_no_handle_is_a_silent_no_op_without_commissioning(tmp_path):
    """The reason commissioning exists: the wrong controller reports success and does nothing."""

    def spec(label):
        return RunSpec(
            label=label,
            model=SMALL,
            weather=config.DEFAULT_WEATHER,
            run_period=RunPeriod.parse(WINDOW),
            output_dir=tmp_path / label,
        )

    baseline = runner.run(spec("baseline"))
    misapplied = runner.run(spec("misapplied"), SupplyAirReset())

    assert misapplied.policy_decisions > 100
    assert misapplied.kpis.electricity_kwh == pytest.approx(baseline.kpis.electricity_kwh)


@pytest.mark.slow
def test_commissioning_refuses_rather_than_deploying_something_useless(tmp_path):
    plan = commissioning.commission(SMALL, period=WINDOW, output_dir=tmp_path)
    fits = {fit.measure: fit for fit in plan.fits}

    assert not fits["supply_air_reset"].actuable
    assert "no heating handle" in fits["supply_air_reset"].reason

    with pytest.raises(commissioning.NothingApplies, match="earns its place"):
        commissioning.author_for(plan)


@pytest.mark.slow
def test_commissioning_picks_the_measure_the_building_can_use(tmp_path):
    plan = commissioning.commission(config.DEFAULT_MODEL, period=WINDOW, output_dir=tmp_path)
    fits = {fit.measure: fit for fit in plan.fits}

    assert fits["supply_air_reset"].actuable
    assert plan.end_uses["heating"] > plan.end_uses["fans"]
    if plan.selected:
        assert isinstance(commissioning.author_for(plan), SupplyAirReset | object)


def test_applicability_alone_does_not_select_a_measure():
    """Fans dominate this building and the measure is actuable; it still made things worse."""
    plan = commissioning.Commissioning(
        model="small.idf",
        conditioned_zones=5,
        end_uses={"fans": 0.166, "heating": 0.0},
        baseline=_kpis(),
        fits=[
            commissioning.MeasureFit(
                measure="hvac_availability",
                targets="fans",
                actuable=True,
                target_share=0.166,
                electricity_pct=2.14,
                selected=False,
                reason="tried it: +2.14% electricity, not worth it",
            )
        ],
    )
    assert plan.selected == []
    with pytest.raises(commissioning.NothingApplies):
        commissioning.author_for(plan)


def test_the_two_buildings_need_different_measures():
    catalogue = {m.name: m for m in commissioning.CATALOGUE}
    assert catalogue["supply_air_reset"].build().__class__ is SupplyAirReset
    assert catalogue["hvac_availability"].build().__class__ is AvailabilityTrim


def _kpis():
    from ecoloop.contracts import ComfortBand, KpiSummary

    return KpiSummary(
        label="baseline",
        timesteps=1,
        simulated_hours=1.0,
        wall_clock_seconds=0.0,
        electricity_kwh=1.0,
        peak_demand_kw=1.0,
        unmet_heating_hours=0.0,
        unmet_cooling_hours=0.0,
        comfort_band=ComfortBand(),
        comfort_exceedance_hours=0.0,
        comfort_degree_hours=0.0,
    )
