"""Phase 0 gate: a real EnergyPlus run whose telemetry agrees with EnergyPlus itself."""

from __future__ import annotations

import sqlite3

import pytest

from ecoloop import config, runner
from ecoloop.contracts import RunPeriod, RunSpec
from ecoloop.kpi import JOULES_PER_KWH

KWH_TO_GJ = JOULES_PER_KWH / 1e9

# The tabular report prints GJ to two decimals, so agreement is only assertable to half of that.
TABULAR_PRECISION_GJ = 0.005


@pytest.fixture(scope="module")
def result():
    spec = RunSpec(
        label="test_smoke",
        model=config.DEFAULT_MODEL,
        weather=config.DEFAULT_WEATHER,
        run_period=RunPeriod.parse(config.PERIODS["smoke"]),
        output_dir=config.RUNS / "test_smoke",
    )
    return runner.run(spec)


def tabular_gj(sql_path, fuel: str) -> float:
    query = """SELECT Value FROM TabularDataWithStrings
               WHERE ReportName='AnnualBuildingUtilityPerformanceSummary'
               AND TableName='End Uses' AND RowName='Total End Uses' AND ColumnName=?"""
    with sqlite3.connect(sql_path) as db:
        return float(db.execute(query, (fuel,)).fetchone()[0])


@pytest.mark.slow
def test_simulates_the_requested_window(result):
    assert result.kpis.simulated_hours == 72
    assert result.kpis.timesteps == 72 * result.spec.timesteps_per_hour


@pytest.mark.slow
def test_energy_matches_energyplus_tabular_report(result):
    """Guards against silently dropping or double counting timesteps."""
    sql = result.spec.output_dir / "eplusout.sql"
    assert result.kpis.electricity_kwh * KWH_TO_GJ == pytest.approx(
        tabular_gj(sql, "Electricity"), abs=TABULAR_PRECISION_GJ
    )
    assert result.kpis.gas_kwh * KWH_TO_GJ == pytest.approx(
        tabular_gj(sql, "Natural Gas"), abs=TABULAR_PRECISION_GJ
    )


@pytest.mark.slow
def test_run_is_clean_and_physically_plausible(result):
    assert result.severe_errors == 0
    assert 50 < result.kpis.peak_demand_kw < 500
    assert result.kpis.peak_demand_at.hour in range(9, 20)


@pytest.mark.slow
def test_telemetry_excludes_sizing_periods(result):
    import pandas as pd

    df = pd.read_parquet(result.telemetry)
    assert df["time"].dt.month.eq(7).all()
    assert df["time"].is_monotonic_increasing
    assert not df.filter(like="|temp").isna().any().any()
