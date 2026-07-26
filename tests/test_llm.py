"""The simulation must survive whatever the model does, including not being there."""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest

from ecoloop import digest, llm
from ecoloop.contracts import ComfortBand
from ecoloop.control import Setpoints, ZoneObservation
from ecoloop.strategies import Supervised, SupplyAirReset

COMFORT = ComfortBand()
UNREACHABLE = "http://127.0.0.1:9"


def state(time: datetime, outdoor: float = 5.0, temperature: float = 22.0):
    observation = ZoneObservation(
        zone="Core",
        temperature=temperature,
        occupancy=5.0,
        scheduled=Setpoints(21.0, 24.0),
        outdoor_temperature=outdoor,
    )
    return digest.build(
        time,
        {"Core": observation},
        {"Core": observation.scheduled},
        120.0,
        COMFORT,
        forecast=[4.0, 6.0, 11.0, 14.0, 12.0, 8.0, 5.0, 3.0],
    )


class StubPlanner:
    """A planner whose answers, and failures, are scripted."""

    def __init__(self, ceilings):
        self.ceilings = list(ceilings)
        self.decisions = []
        self.scorecards = []

    def plan(self, digest_, scorecard=None):
        self.scorecards.append(scorecard)
        ceiling = self.ceilings.pop(0) if self.ceilings else None
        decision = llm.Decision(time=digest_.time, prompt_hash="stub")
        if ceiling is None:
            decision.error = "model went away"
        else:
            decision.plan = llm.Plan(supply_air_ceiling=ceiling, reason="scripted")
        self.decisions.append(decision)
        return decision


def test_forecast_error_grows_with_lead_time_and_is_reproducible():
    near = [abs(digest.forecast_error(0, day, 6, 3)) for day in range(200)]
    far = [abs(digest.forecast_error(0, day, 6, 24)) for day in range(200)]
    assert sum(far) / len(far) > 2 * sum(near) / len(near)
    assert digest.forecast_error(0, 40, 6, 12) == digest.forecast_error(0, 40, 6, 12)
    assert digest.forecast_error(1, 40, 6, 12) != digest.forecast_error(0, 40, 6, 12)


def test_the_prompt_carries_the_forecast_and_stays_small():
    prompt = llm.render(state(datetime(2023, 4, 15, 4)), None)
    assert "Forecast next 24 h" in prompt
    assert "14.0" in prompt
    assert len(prompt) < 700


def test_an_unreachable_model_is_reported_not_raised():
    planner = llm.Planner(host=UNREACHABLE, timeout=1.0, attempts=1)
    decision = planner.plan(state(datetime(2023, 1, 15, 4)))
    assert decision.plan is None
    assert decision.error
    assert planner.decisions == [decision]


def test_a_cached_decision_is_replayed_without_a_model(tmp_path):
    planner = llm.Planner(host=UNREACHABLE, timeout=1.0, cache_dir=tmp_path)
    digest_ = state(datetime(2023, 1, 15, 4))
    saved = llm.Decision(
        time=digest_.time,
        prompt_hash="x",
        plan=llm.Plan(supply_air_ceiling=17.5, reason="from cache"),
    )
    prompt_hash = planner.plan(digest_).prompt_hash
    (tmp_path / f"{prompt_hash}.json").write_text(saved.model_dump_json())

    replayed = llm.Planner(host=UNREACHABLE, timeout=1.0, cache_dir=tmp_path).plan(digest_)
    assert replayed.cached
    assert replayed.plan.supply_air_ceiling == 17.5


def test_without_a_plan_the_deterministic_curve_still_drives_the_building():
    reset = SupplyAirReset()
    supervised = Supervised(llm.Planner(host=UNREACHABLE, timeout=1.0, attempts=1), reset=reset)
    digest_ = state(datetime(2023, 1, 15, 4), outdoor=-10.0)

    policy = supervised(digest_)
    assert reset.requested_ceiling is None
    assert policy.supply_air_temperature == pytest.approx(reset.ceiling(-10.0))


def test_a_model_that_stops_answering_leaves_the_last_good_ceiling_in_place():
    reset = SupplyAirReset()
    supervised = Supervised(StubPlanner([16.5]), reset=reset, replan_hours=1.0)

    start = datetime(2023, 4, 15, 4)
    first = supervised(state(start))
    assert reset.requested_ceiling == 16.5
    assert first.supply_air_temperature == pytest.approx(16.5)

    later = supervised(state(start + timedelta(hours=2)))
    assert reset.requested_ceiling == 16.5
    assert later.supply_air_temperature == pytest.approx(16.5)


def test_the_scorecard_reports_the_previous_plan_back_to_the_model():
    planner = StubPlanner([16.0, 15.0])
    supervised = Supervised(planner, replan_hours=1.0)
    start = datetime(2023, 4, 15, 4)

    supervised(state(start))
    supervised(state(start + timedelta(minutes=30), temperature=25.5))
    supervised(state(start + timedelta(hours=2)))

    first, second = planner.scorecards
    assert first is None
    assert second.ceiling == 16.0
    assert second.hours == pytest.approx(2.0)
    assert second.worst_excursion_k == pytest.approx(25.5 - 24.2)


@pytest.mark.needs_ollama
def test_a_real_model_answers_with_a_valid_plan():
    try:
        installed = httpx.get(f"{llm.DEFAULT_HOST}/api/tags", timeout=2.0).json()
    except httpx.HTTPError:
        pytest.skip("no model server on this machine")
    if not any(m["name"].startswith(llm.DEFAULT_MODEL) for m in installed.get("models", [])):
        pytest.skip(f"{llm.DEFAULT_MODEL} not pulled")

    decision = llm.Planner(timeout=120.0).plan(state(datetime(2023, 1, 15, 4), outdoor=-12.0))
    assert decision.plan, decision.error
    assert 12.8 <= decision.plan.supply_air_ceiling <= 18.0


@pytest.mark.slow
def test_a_run_survives_the_model_dying_partway_through(tmp_path):
    """One plan lands, every later call fails, and the simulation finishes regardless."""
    from ecoloop import config, runner
    from ecoloop.contracts import RunPeriod, RunSpec

    def spec(label):
        return RunSpec(
            label=label,
            model=config.DEFAULT_MODEL,
            weather=config.DEFAULT_WEATHER,
            run_period=RunPeriod.parse("01-15:01-17"),
            output_dir=tmp_path / label,
        )

    planner = StubPlanner([17.0])
    supervised = Supervised(planner, replan_hours=12.0)
    controlled = runner.run(spec("agent"), supervised)
    baseline = runner.run(spec("baseline"))

    assert len(planner.decisions) > 1
    assert sum(1 for d in planner.decisions if d.error) > 0
    assert controlled.severe_errors == 0
    assert controlled.kpis.electricity_kwh < baseline.kpis.electricity_kwh
    assert (tmp_path / "agent" / "decisions.json").is_file()


def test_a_supervisor_may_lower_the_ceiling_but_never_raise_it():
    """A model that asks for a warm deck on a hot day would starve the coils."""
    reset = SupplyAirReset()
    hot, cold = 30.0, -10.0
    assert reset.ceiling(hot) == pytest.approx(reset.minimum)
    assert reset.ceiling(cold) == pytest.approx(reset.maximum)

    reset.requested_ceiling = 18.0
    assert reset.ceiling(hot) == pytest.approx(reset.minimum)

    reset.requested_ceiling = 15.0
    assert reset.ceiling(cold) == pytest.approx(15.0)


@pytest.mark.needs_ollama
def test_the_same_prompt_gives_the_same_plan():
    """Without a pinned seed, replaying a run diverges: each answer feeds the next prompt."""
    try:
        installed = httpx.get(f"{llm.DEFAULT_HOST}/api/tags", timeout=2.0).json()
    except httpx.HTTPError:
        pytest.skip("no model server on this machine")
    if not any(m["name"].startswith(llm.DEFAULT_MODEL) for m in installed.get("models", [])):
        pytest.skip(f"{llm.DEFAULT_MODEL} not pulled")

    planner = llm.Planner(timeout=180.0)
    digest_ = state(datetime(2023, 10, 12, 4), outdoor=11.0)
    answers = {planner.plan(digest_).plan.supply_air_ceiling for _ in range(3)}
    assert len(answers) == 1
