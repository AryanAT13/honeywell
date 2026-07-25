"""Runs one EnergyPlus simulation, optionally under supervisory control.

Three API constraints drive the shape of this module: variables must be requested before
the run starts, handles are only valid once api_data_fully_ready() returns true, and both
warmup timesteps and sizing-period environments must be discarded.
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
from .control import Controller, SetpointActuators, Setpoints, ZoneObservation

SITE_VARIABLE = ("Site Outdoor Air Drybulb Temperature", "Environment")
SCHEDULE_VARIABLE = "Schedule Value"

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


class _Loop:
    """Both halves of a timestep: control on the way in, telemetry on the way out.

    One object so the two callbacks share a single handle cache.
    """

    def __init__(self, exchange, zones, schedules, controller):
        self.ex = exchange
        self.zones = zones
        self.schedules: dict[str, tuple[str, str]] = schedules
        self.controller: Controller | None = controller
        self.actuators = SetpointActuators(exchange, sorted(schedules)) if controller else None
        self.rows: list[dict] = []
        self.handles: dict[str, int] = {}
        self.commanded: dict[str, Setpoints | None] = {}

    def _resolve(self, state) -> None:
        self.handles["outdoor_temp"] = self.ex.get_variable_handle(state, *SITE_VARIABLE)
        for zone in self.zones:
            for field, name in ZONE_VARIABLES.items():
                self.handles[f"{zone}|{field}"] = self.ex.get_variable_handle(state, name, zone)
        for schedule in {s for pair in self.schedules.values() for s in pair}:
            self.handles[f"sched|{schedule}"] = self.ex.get_variable_handle(
                state, SCHEDULE_VARIABLE, schedule
            )

        for field, candidates in METERS.items():
            for name in candidates:
                handle = self.ex.get_meter_handle(state, name)
                if handle >= 0:
                    self.handles[field] = handle
                    break
            else:
                if field in REQUIRED_METERS:
                    raise RuntimeError(f"no meter handle for {field}; tried {candidates}")
                self.handles[field] = -1

        missing = [k for k, v in self.handles.items() if v < 0 and k not in METERS]
        if missing:
            raise RuntimeError(f"unresolved output variables: {missing}")

        if self.actuators:
            self.actuators.resolve(state)

    def _live(self, state) -> bool:
        ex = self.ex
        if ex.kind_of_sim(state) != WEATHER_RUN_PERIOD or not ex.api_data_fully_ready(state):
            return False
        if ex.warmup_flag(state):
            return False
        if not self.handles:
            self._resolve(state)
        return True

    def _value(self, state, key: str) -> float:
        return self.ex.get_variable_value(state, self.handles[key])

    def control(self, state) -> None:
        if not self._live(state) or self.controller is None:
            return
        outdoor = self._value(state, "outdoor_temp")
        for zone, (heating_sched, cooling_sched) in self.schedules.items():
            observation = ZoneObservation(
                zone=zone,
                temperature=self._value(state, f"{zone}|temp"),
                occupancy=self._value(state, f"{zone}|occupancy"),
                scheduled=Setpoints(
                    self._value(state, f"sched|{heating_sched}"),
                    self._value(state, f"sched|{cooling_sched}"),
                ),
                outdoor_temperature=outdoor,
            )
            command = self.controller(observation)
            self.commanded[zone] = command
            if command:
                self.actuators.write(state, zone, command)
            else:
                self.actuators.release(state, zone)

    def _timestamp(self, state) -> datetime:
        """Zone timestep boundary.

        minutes() and current_time() report the system timestep, which subdivides adaptively
        when HVAC struggles to converge, so they differ between arms on the same clock.
        """
        ex = self.ex
        step = ex.zone_time_step_number(state) * 60 / ex.num_time_steps_in_hour(state)
        return datetime(NOMINAL_YEAR, ex.month(state), ex.day_of_month(state)) + timedelta(
            minutes=ex.hour(state) * 60 + step
        )

    def observe(self, state) -> None:
        if not self._live(state):
            return
        ex = self.ex
        row: dict = {"time": self._timestamp(state)}
        for field, handle in self.handles.items():
            if field in METERS:
                row[field] = ex.get_meter_value(state, handle) if handle >= 0 else 0.0
            elif not field.startswith("sched|"):
                row[field] = ex.get_variable_value(state, handle)
        for zone in self.zones:
            command = self.commanded.get(zone)
            row[f"{zone}|cmd_heat_sp"] = command.heating if command else float("nan")
            row[f"{zone}|cmd_cool_sp"] = command.cooling if command else float("nan")
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


def run(spec: RunSpec, controller: Controller | None = None) -> RunResult:
    out_dir = spec.output_dir
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    epjson = prepare_model(spec, out_dir)
    building = model_io.load(epjson)
    zones = model_io.conditioned_zones(building)
    schedules = model_io.thermostat_schedules(building)
    if not zones:
        raise ValueError(f"{spec.model} has no thermostatically controlled zones")
    if controller and not schedules:
        raise ValueError(f"{spec.model} has no dual setpoint thermostats to control")

    api = eplus.api()
    state = api.state_manager.new_state()
    api.runtime.set_console_output_status(state, False)

    requests = [SITE_VARIABLE, *((v, z) for z in zones for v in ZONE_VARIABLES.values())]
    requests += [(SCHEDULE_VARIABLE, s) for pair in schedules.values() for s in pair]
    for name, key in requests:
        api.exchange.request_variable(state, name, key)

    loop = _Loop(api.exchange, zones, schedules, controller)
    api.runtime.callback_begin_zone_timestep_after_init_heat_balance(state, loop.control)
    api.runtime.callback_end_zone_timestep_after_zone_reporting(state, loop.observe)

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
    if not loop.rows:
        raise RuntimeError(f"'{spec.label}' produced no telemetry; check {out_dir}/eplusout.err")

    telemetry = pd.DataFrame(loop.rows)
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
