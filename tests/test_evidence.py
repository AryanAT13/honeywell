"""The report is rendered from the run objects, so it cannot disagree with them."""

from __future__ import annotations

from datetime import datetime

import pytest

from ecoloop import commissioning, config, evidence
from ecoloop.contracts import ComfortBand, KpiSummary
from ecoloop.experiment import ArmComparison, Comparison

COMMITTED = config.ROOT / "docs" / "evidence" / "evidence.json"


def arm(label, kwh, pct, degree_hours, delta):
    return ArmComparison(
        label=label,
        electricity_kwh=kwh,
        electricity_pct=pct,
        peak_demand_kw=300.0,
        peak_demand_pct=pct,
        unmet_hours=50.0,
        comfort_exceedance_hours=500.0,
        comfort_degree_hours=degree_hours,
        comfort_degree_hours_change=delta,
        wall_clock_seconds=1.0,
    )


def kpis():
    return KpiSummary(
        label="baseline",
        timesteps=1,
        simulated_hours=1.0,
        wall_clock_seconds=0.0,
        electricity_kwh=1000.0,
        peak_demand_kw=1.0,
        unmet_heating_hours=0.0,
        unmet_cooling_hours=0.0,
        comfort_band=ComfortBand(),
        comfort_exceedance_hours=0.0,
        comfort_degree_hours=0.0,
    )


def synthetic():
    comparison = Comparison(
        reference="baseline",
        run_period="01-01:12-31",
        timesteps=52560,
        arms=[
            arm("baseline", 767959, 0.0, 1029.6, 0.0),
            arm("supervisor", 727003, -5.33, 1036.5, 7.0),
        ],
    )
    plan = commissioning.Commissioning(
        model="small.idf",
        conditioned_zones=5,
        end_uses={"fans": 0.166},
        baseline=kpis(),
        fits=[
            commissioning.MeasureFit(
                measure="hvac_availability",
                targets="fans",
                actuable=True,
                target_share=0.166,
                selected=False,
                reason="tried it: +2.14% electricity, not worth it",
            )
        ],
    )
    return evidence.Evidence(
        generated=datetime(2026, 1, 1),
        energyplus="26.1.0",
        model="medium.idf",
        llm_model="qwen2.5:3b-instruct",
        scenarios=[evidence.Scenario(climate="chicago", period="annual", comparison=comparison)],
        commissioning=[plan],
    )


def test_the_report_states_the_numbers_it_was_given(tmp_path):
    page = evidence.render(synthetic(), root=tmp_path)
    assert "727,003" in page
    assert "-5.33%" in page
    assert "tried it: +2.14% electricity, not worth it" in page
    assert "no measure earns its place" in page


def test_a_missing_scenario_does_not_break_rendering(tmp_path):
    page = evidence.render(synthetic(), root=tmp_path)
    assert "Delhi" in page
    assert page.strip().startswith("<!doctype html>")


@pytest.mark.skipif(not COMMITTED.is_file(), reason="run `make evidence` first")
def test_the_committed_evidence_still_says_what_the_readme_says():
    published = evidence.Evidence.model_validate_json(COMMITTED.read_text())
    annual = published.scenario("chicago", "annual")
    assert annual is not None

    arms = {entry.label: entry for entry in annual.arms}
    assert arms["supervisor"].electricity_pct < -5.0
    assert arms["supervisor"].peak_demand_pct < -8.0
    assert arms["supervisor"].unmet_hours <= arms["baseline"].unmet_hours

    # Anticipation buys a different point on the frontier, not a better one. Over a single
    # season the two land together; over a year foresight saves less and is more comfortable,
    # and neither dominates.
    winter = {entry.label: entry for entry in published.scenario("chicago", "winter").arms}
    assert abs(winter["foresight"].electricity_pct - winter["supervisor"].electricity_pct) < 0.1

    assert arms["foresight"].electricity_pct > arms["supervisor"].electricity_pct
    assert arms["foresight"].comfort_degree_hours < arms["supervisor"].comfort_degree_hours

    small = next(p for p in published.commissioning if "Small" in p.model)
    assert small.selected == []
