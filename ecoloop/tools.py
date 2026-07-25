"""What anything reasoning about this building is allowed to do.

Plain functions over pydantic models, with no MCP import. The in-process agent calls these
directly during a run; mcp_server.py exposes the same functions over the protocol.

Telemetry is downsampled and errors are summarised on the way out. Nothing here returns a
raw log or a full timeseries, because the caller is usually paying by the token.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from . import config, digest, errors, experiment, runner
from . import model as model_io
from .contracts import ComfortBand, KpiSummary, RunPeriod, RunSpec
from .control import Setpoints, ZoneObservation
from .digest import StateDigest
from .policy import Guardian, Limits, Policy
from .strategies import Fixed

DEFAULT_TELEMETRY_POINTS = 200


class ControlSurface(BaseModel):
    """What a model exposes to a controller."""

    model: str
    zones: int
    conditioned_zones: list[str]
    setpoint_schedules: list[str]
    air_loops: list[str]
    supply_air_schedules: list[str]


class RunSummary(BaseModel):
    label: str
    run_period: str
    weather: str
    kpis: KpiSummary


class TelemetryWindow(BaseModel):
    label: str
    start: datetime
    end: datetime
    timesteps: int
    returned: int
    data: list[dict]


class PolicyReview(BaseModel):
    proposed: Policy
    accepted: Policy
    clamped: dict[str, int]
    comfort_band: ComfortBand
    limits: Limits


class PolicyEvaluation(BaseModel):
    review: PolicyReview
    period: str
    weather: str
    kpis: KpiSummary
    baseline: KpiSummary
    electricity_pct: float
    peak_demand_pct: float
    comfort_degree_hours_change: float


def _model_path(name: str) -> Path:
    for candidate in (config.MODELS / name, config.MODELS / f"{name}.idf"):
        if candidate.is_file():
            return candidate
    raise ValueError(f"unknown model '{name}'; available: {', '.join(list_models())}")


def _weather_path(name: str) -> Path:
    if name in config.CLIMATES:
        return config.CLIMATES[name]
    path = Path(name)
    if path.is_file():
        return path
    raise ValueError(f"unknown climate '{name}'; available: {', '.join(config.CLIMATES)}")


def _load_epjson(model: Path) -> dict:
    return model_io.load(model_io.convert(model, config.RUNS / "_models", "epJSON"))


def _run_dir(label: str) -> Path:
    path = config.RUNS / label
    if not (path / "kpis.json").is_file():
        raise ValueError(f"no completed run '{label}'; available: {', '.join(list_runs_labels())}")
    return path


def list_models() -> list[str]:
    """Building models available to simulate."""
    return sorted(p.name for p in config.MODELS.glob("*.idf"))


def list_climates() -> list[str]:
    """Named weather files available to simulate against."""
    return sorted(config.CLIMATES)


def inspect_model(model: str) -> ControlSurface:
    """Report a model's control surface: its conditioned zones and what can be actuated."""
    path = _model_path(model)
    building = _load_epjson(path)
    schedules = model_io.thermostat_schedules(building)
    return ControlSurface(
        model=path.name,
        zones=len(model_io.zones(building)),
        conditioned_zones=model_io.conditioned_zones(building),
        setpoint_schedules=sorted({s for pair in schedules.values() for s in pair}),
        air_loops=sorted(building.get("AirLoopHVAC", {})),
        supply_air_schedules=model_io.supply_air_schedules(building),
    )


def list_runs_labels() -> list[str]:
    return sorted(p.parent.name for p in config.RUNS.glob("*/kpis.json"))


def list_runs() -> list[RunSummary]:
    """Completed simulation runs and their headline numbers."""
    summaries = []
    for label in list_runs_labels():
        directory = config.RUNS / label
        spec = RunSpec.model_validate_json((directory / "spec.json").read_text())
        summaries.append(
            RunSummary(
                label=label,
                run_period=str(spec.run_period),
                weather=spec.weather.name,
                kpis=KpiSummary.model_validate_json((directory / "kpis.json").read_text()),
            )
        )
    return summaries


def run_kpis(label: str) -> KpiSummary:
    """Headline numbers for one completed run."""
    return KpiSummary.model_validate_json((_run_dir(label) / "kpis.json").read_text())


def run_errors(label: str) -> errors.ErrorReport:
    """Warning and error counts from a run, with the blocking messages deduplicated."""
    return errors.parse(_run_dir(label) / "eplusout.err")


def _summary_frame(frame: pd.DataFrame, seconds: int) -> pd.DataFrame:
    temps = frame.filter(like="|temp")
    return pd.DataFrame(
        {
            "time": frame["time"],
            "outdoor_temp": frame["outdoor_temp"].round(2),
            "demand_kw": (frame["electricity_j"] / seconds / 1000).round(1),
            "supply_air_setpoint": frame.get("supply_air_setpoint", pd.Series(dtype=float)),
            "zone_temp_min": temps.min(axis=1).round(2),
            "zone_temp_mean": temps.mean(axis=1).round(2),
            "zone_temp_max": temps.max(axis=1).round(2),
        }
    )


def telemetry(
    label: str,
    start: datetime | None = None,
    end: datetime | None = None,
    points: int = DEFAULT_TELEMETRY_POINTS,
    fields: list[str] | None = None,
) -> TelemetryWindow:
    """A downsampled window of a run's telemetry.

    Defaults to a whole-building summary. Pass explicit column names for per-zone detail;
    the full frame is never returned, because it is tens of thousands of rows.
    """
    directory = _run_dir(label)
    frame = pd.read_parquet(directory / "telemetry.parquet")
    spec = RunSpec.model_validate_json((directory / "spec.json").read_text())

    window = frame
    if start is not None:
        window = window[window["time"] >= start]
    if end is not None:
        window = window[window["time"] <= end]
    if window.empty:
        raise ValueError(f"no telemetry for '{label}' between {start} and {end}")

    selected = (
        window[["time", *fields]] if fields else _summary_frame(window, spec.timestep_seconds)
    )
    stride = max(1, math.ceil(len(selected) / points))
    sampled = selected.iloc[::stride]

    return TelemetryWindow(
        label=label,
        start=window["time"].iloc[0],
        end=window["time"].iloc[-1],
        timesteps=len(window),
        returned=len(sampled),
        data=sampled.to_dict(orient="records"),
    )


def state_digest(label: str, at: datetime) -> StateDigest:
    """The situation report as a policy author would have seen it at a given moment."""
    directory = _run_dir(label)
    frame = pd.read_parquet(directory / "telemetry.parquet")
    spec = RunSpec.model_validate_json((directory / "spec.json").read_text())

    row = frame.iloc[(frame["time"] - at).abs().argmin()]
    zones = sorted({c.split("|")[0] for c in frame.columns if c.endswith("|temp")})
    observations, effective = {}, {}
    for zone in zones:
        observations[zone] = ZoneObservation(
            zone=zone,
            temperature=row[f"{zone}|temp"],
            occupancy=row[f"{zone}|occupancy"],
            scheduled=Setpoints(row[f"{zone}|heat_sp"], row[f"{zone}|cool_sp"]),
            outdoor_temperature=row["outdoor_temp"],
        )
        effective[zone] = observations[zone].scheduled

    return digest.build(
        row["time"].to_pydatetime(),
        observations,
        effective,
        row["electricity_j"] / spec.timestep_seconds / 1000,
        spec.comfort_band,
    )


def check_policy(policy: Policy, comfort_band: ComfortBand | None = None) -> PolicyReview:
    """Project a policy onto the feasible set without running anything."""
    guardian = Guardian(comfort_band or ComfortBand())
    accepted = guardian.review(policy)
    return PolicyReview(
        proposed=policy,
        accepted=accepted,
        clamped=dict(guardian.clamps),
        comfort_band=guardian.comfort,
        limits=guardian.limits,
    )


def _baseline(model: Path, weather: Path, period: RunPeriod, timesteps_per_hour: int) -> KpiSummary:
    """Baselines are deterministic for a given model, weather and window, so cache them."""
    key = f"{model.stem}_{weather.stem}_{period}_{timesteps_per_hour}".replace(":", "")
    output = config.RUNS / "_baseline" / key
    if (output / "kpis.json").is_file():
        return KpiSummary.model_validate_json((output / "kpis.json").read_text())
    return runner.run(
        RunSpec(
            label="baseline",
            model=model,
            weather=weather,
            run_period=period,
            timesteps_per_hour=timesteps_per_hour,
            output_dir=output,
        )
    ).kpis


def evaluate_policy(
    policy: Policy,
    period: str = "summer",
    weather: str = "chicago",
    model: str | None = None,
    guarded: bool = True,
    timesteps_per_hour: int = 6,
) -> PolicyEvaluation:
    """Run a policy against the simulation and score it against the untouched baseline.

    This is the closed loop: propose, run, read the numbers, propose again.
    """
    model_file = _model_path(model) if model else config.DEFAULT_MODEL
    weather_file = _weather_path(weather)
    window = RunPeriod.parse(config.PERIODS.get(period, period))

    review = check_policy(policy)
    baseline = _baseline(model_file, weather_file, window, timesteps_per_hour)
    result = runner.run(
        RunSpec(
            label="evaluate_policy",
            model=model_file,
            weather=weather_file,
            run_period=window,
            timesteps_per_hour=timesteps_per_hour,
            output_dir=config.RUNS / "evaluate_policy",
        ),
        Fixed(policy),
        guarded=guarded,
    )
    kpis = result.kpis

    return PolicyEvaluation(
        review=review,
        period=str(window),
        weather=weather_file.name,
        kpis=kpis,
        baseline=baseline,
        electricity_pct=100 * (kpis.electricity_kwh / baseline.electricity_kwh - 1),
        peak_demand_pct=100 * (kpis.peak_demand_kw / baseline.peak_demand_kw - 1),
        comfort_degree_hours_change=kpis.comfort_degree_hours - baseline.comfort_degree_hours,
    )


def compare_arms(
    arms: list[str],
    period: str = "summer",
    weather: str = "chicago",
    model: str | None = None,
) -> experiment.Comparison:
    """Run named control arms over identical weather and compare them pairwise."""
    unknown = [name for name in arms if name not in experiment.ARMS]
    if unknown:
        raise ValueError(f"unknown arms {unknown}; available: {', '.join(experiment.ARMS)}")
    results = experiment.run_arms(
        [experiment.ARMS[name] for name in arms],
        model=_model_path(model) if model else config.DEFAULT_MODEL,
        weather=_weather_path(weather),
        period=RunPeriod.parse(config.PERIODS.get(period, period)),
    )
    return experiment.compare(results)
