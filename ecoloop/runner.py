"""Runs one EnergyPlus simulation and records per-timestep telemetry via the runtime API.

Three API constraints drive the shape of this module: variables must be requested before
the run starts, handles are only valid once api_data_fully_ready() returns true, and
warmup timesteps must be discarded.
"""

from __future__ import annotations

import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from . import eplus, errors, kpi
from . import model as model_io
from .contracts import NOMINAL_YEAR, RunResult, RunSpec

SITE_VARIABLE = ("Site Outdoor Air Drybulb Temperature", "Environment")

ZONE_VARIABLES = {
    "temp": "Zone Mean Air Temperature",
    "heat_sp": "Zone Thermostat Heating Setpoint Temperature",
    "cool_sp": "Zone Thermostat Cooling Setpoint Temperature",
    "occupancy": "Zone People Occupant Count",
}

# Meter names vary across EnergyPlus versions and model features; first that resolves wins.
# Electricity:Facility has no API handle in 26.1, but the net meter is identical without
# on-site generation.
METERS = {
    "electricity_j": ("Electricity:Facility", "ElectricityNet:Facility"),
    "hvac_electricity_j": ("Electricity:HVAC",),
    "gas_j": ("NaturalGas:Facility",),
}
REQUIRED_METERS = ("electricity_j",)

# KindOfSim: 1 = design day, 3 = weather-file run period. Sizing runs must not be recorded.
WEATHER_RUN_PERIOD = 3


class _Recorder:
    """Appends one telemetry row per zone timestep. Runs inside the EnergyPlus callback."""

    def __init__(self, exchange, zones: list[str]):
        self.ex = exchange
        self.zones = zones
        self.rows: list[dict] = []
        self.handles: dict[str, int] = {}
        self.meters_used: dict[str, str] = {}

    def _resolve_handles(self, state) -> None:
        self.handles["outdoor_temp"] = self.ex.get_variable_handle(state, *SITE_VARIABLE)
        for zone in self.zones:
            for field, name in ZONE_VARIABLES.items():
                self.handles[f"{zone}|{field}"] = self.ex.get_variable_handle(state, name, zone)

        for field, candidates in METERS.items():
            for name in candidates:
                handle = self.ex.get_meter_handle(state, name)
                if handle >= 0:
                    self.handles[field] = handle
                    self.meters_used[field] = name
                    break
            else:
                if field in REQUIRED_METERS:
                    raise RuntimeError(f"no meter handle for {field}; tried {candidates}")
                self.handles[field] = -1

        missing = [k for k, v in self.handles.items() if v < 0 and k not in METERS]
        if missing:
            raise RuntimeError(f"unresolved output variables: {missing}")

    def __call__(self, state) -> None:
        ex = self.ex
        if ex.kind_of_sim(state) != WEATHER_RUN_PERIOD:
            return
        if not ex.api_data_fully_ready(state) or ex.warmup_flag(state):
            return
        if not self.handles:
            self._resolve_handles(state)

        row: dict = {
            "time": datetime(NOMINAL_YEAR, ex.month(state), ex.day_of_month(state))
            + timedelta(hours=ex.hour(state), minutes=ex.minutes(state))
        }
        for field, handle in self.handles.items():
            if field in METERS:
                row[field] = ex.get_meter_value(state, handle) if handle >= 0 else 0.0
            else:
                row[field] = ex.get_variable_value(state, handle) if handle >= 0 else float("nan")
        self.rows.append(row)


def prepare_model(spec: RunSpec, out_dir: Path) -> Path:
    """Materialise the run's epJSON with the requested run period and timestep applied."""
    source = spec.model
    if source.suffix.lower() == ".idf":
        source = model_io.convert(source, out_dir, "epJSON")
    building = model_io.load(source)
    model_io.apply_run_period(building, spec.run_period)
    model_io.set_timesteps_per_hour(building, spec.timesteps_per_hour)
    model_io.enable_weather_run_period(building)
    return model_io.save(building, out_dir / "in.epJSON")


def run(spec: RunSpec) -> RunResult:
    out_dir = spec.output_dir
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    epjson = prepare_model(spec, out_dir)
    zones = model_io.conditioned_zones(model_io.load(epjson))
    if not zones:
        raise ValueError(f"{spec.model} has no thermostatically controlled zones")

    api = eplus.api()
    state = api.state_manager.new_state()
    api.runtime.set_console_output_status(state, False)

    for name, key in [SITE_VARIABLE, *((v, z) for z in zones for v in ZONE_VARIABLES.values())]:
        api.exchange.request_variable(state, name, key)

    recorder = _Recorder(api.exchange, zones)
    api.runtime.callback_end_zone_timestep_after_zone_reporting(state, recorder)

    started = time.perf_counter()
    exit_code = api.runtime.run_energyplus(
        state, ["-d", str(out_dir), "-w", str(spec.weather), str(epjson)]
    )
    wall_clock = time.perf_counter() - started
    api.state_manager.delete_state(state)

    report = errors.parse(out_dir / "eplusout.err")
    if exit_code != 0:
        raise RuntimeError(
            f"EnergyPlus exited {exit_code} for '{spec.label}':\n" + "\n".join(report.blocking)
        )
    if not recorder.rows:
        raise RuntimeError(f"'{spec.label}' produced no telemetry; check {out_dir}/eplusout.err")

    telemetry = pd.DataFrame(recorder.rows)
    telemetry_path = out_dir / "telemetry.parquet"
    telemetry.to_parquet(telemetry_path, index=False)

    result = RunResult(
        spec=spec,
        kpis=kpi.summarize(telemetry, spec, zones, wall_clock),
        telemetry=telemetry_path,
        severe_errors=report.severe,
    )
    (out_dir / "kpis.json").write_text(result.kpis.model_dump_json(indent=2))
    return result
