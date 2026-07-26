"""Runs control arms over identical weather and compares them timestep by timestep.

Arms never interact, so they run in separate processes and are aligned on their shared time
index afterwards. That index is asserted to be identical, which is the property a lockstep
comparison actually needs; running them concurrently would only change the wall clock.
Separate processes also keep EnergyPlus state from leaking between arms.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from . import config, llm, runner
from .contracts import RunPeriod, RunResult, RunSpec
from .policy import Band, Policy
from .strategies import Fixed, PolicyAuthor, Supervised, SupplyAirReset


@dataclass(frozen=True)
class Arm:
    label: str
    author: PolicyAuthor | None = None
    guarded: bool = True


ARMS: dict[str, Arm] = {
    # B0: the stock model, untouched.
    "baseline": Arm("baseline"),
    # B1: the obvious move, widening the occupied band. Unguarded, or there would be nothing
    # left of it to measure.
    "deadband": Arm(
        "deadband",
        Fixed(
            Policy(
                occupied=Band(heating=20.5, cooling=24.5),
                unoccupied=Band(heating=15.6, cooling=26.7),
                reason="widen the occupied band by 0.5 K",
            )
        ),
        guarded=False,
    ),
    # B2: supervisory supply air reset, thermostats left alone.
    "supervisor": Arm("supervisor", SupplyAirReset()),
    # B3: a local model sets the daily ceiling; the same reset loop runs beneath it.
    "agent": Arm(
        "agent",
        Supervised(llm.Planner(cache_dir=config.RUNS / "_llm_cache")),
    ),
}


class ArmComparison(BaseModel):
    label: str
    electricity_kwh: float
    electricity_pct: float
    peak_demand_kw: float
    peak_demand_pct: float
    unmet_hours: float
    comfort_exceedance_hours: float
    comfort_degree_hours: float
    comfort_degree_hours_change: float
    wall_clock_seconds: float


class Comparison(BaseModel):
    reference: str
    run_period: str
    timesteps: int
    arms: list[ArmComparison]


def _execute(job: tuple[RunSpec, Arm]) -> RunResult:
    spec, arm = job
    return runner.run(spec, arm.author, guarded=arm.guarded)


def run_arms(
    arms: list[Arm],
    model: Path,
    weather: Path,
    period: RunPeriod,
    timesteps_per_hour: int = 6,
    workers: int | None = None,
    output_root: Path | None = None,
) -> list[RunResult]:
    root = output_root or config.RUNS
    jobs = [
        (
            RunSpec(
                label=arm.label,
                model=model,
                weather=weather,
                run_period=period,
                timesteps_per_hour=timesteps_per_hour,
                output_dir=root / arm.label,
            ),
            arm,
        )
        for arm in arms
    ]
    with ProcessPoolExecutor(max_workers=workers or min(len(jobs), os.cpu_count() or 1)) as pool:
        return list(pool.map(_execute, jobs))


def _aligned_times(results: list[RunResult]) -> pd.Series:
    reference, *rest = results
    times = pd.read_parquet(reference.telemetry, columns=["time"])["time"]
    for result in rest:
        other = pd.read_parquet(result.telemetry, columns=["time"])["time"]
        if not other.equals(times):
            raise ValueError(
                f"arm '{result.kpis.label}' is not aligned with '{reference.kpis.label}'; "
                "arms must share weather, run period and timestep"
            )
    return times


def compare(results: list[RunResult]) -> Comparison:
    """Differences against the first arm, which is treated as the reference."""
    times = _aligned_times(results)
    base = results[0].kpis
    return Comparison(
        reference=base.label,
        run_period=str(results[0].spec.run_period),
        timesteps=len(times),
        arms=[
            ArmComparison(
                label=r.kpis.label,
                electricity_kwh=r.kpis.electricity_kwh,
                electricity_pct=100 * (r.kpis.electricity_kwh / base.electricity_kwh - 1),
                peak_demand_kw=r.kpis.peak_demand_kw,
                peak_demand_pct=100 * (r.kpis.peak_demand_kw / base.peak_demand_kw - 1),
                unmet_hours=r.kpis.unmet_hours,
                comfort_exceedance_hours=r.kpis.comfort_exceedance_hours,
                comfort_degree_hours=r.kpis.comfort_degree_hours,
                comfort_degree_hours_change=(
                    r.kpis.comfort_degree_hours - base.comfort_degree_hours
                ),
                wall_clock_seconds=r.kpis.wall_clock_seconds,
            )
            for r in results
        ],
    )


def paired_frame(results: list[RunResult]) -> pd.DataFrame:
    """Per-timestep demand for every arm plus cumulative energy saved against the reference."""
    times = _aligned_times(results)
    seconds = results[0].spec.timestep_seconds
    frame = pd.DataFrame({"time": times})
    for result in results:
        joules = pd.read_parquet(result.telemetry, columns=["electricity_j"])["electricity_j"]
        label = result.kpis.label
        frame[f"{label}|kw"] = joules / seconds / 1000
        frame[f"{label}|cumulative_kwh"] = joules.cumsum() / 3.6e6

    reference = results[0].kpis.label
    for result in results[1:]:
        label = result.kpis.label
        frame[f"{label}|kwh_saved"] = (
            frame[f"{reference}|cumulative_kwh"] - frame[f"{label}|cumulative_kwh"]
        )
    return frame
