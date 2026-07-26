"""Building model I/O.

epJSON is the mutation substrate: it is a plain dict, it diffs cleanly, and it can be
validated before a run. IDF is produced on the way out because the deliverable asks for it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import eplus
from .contracts import RunPeriod

Model = dict[str, dict]


def convert(source: Path, out_dir: Path, fmt: str) -> Path:
    """Convert between .idf and .epJSON using the bundled ConvertInputFormat."""
    out_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [str(eplus.convert_input_format()), "-f", fmt, "-o", str(out_dir), str(source)],
        capture_output=True,
        text=True,
    )
    suffix = ".epJSON" if fmt.lower() == "epjson" else ".idf"
    produced = out_dir / (source.stem + suffix)
    if result.returncode or not produced.is_file():
        raise RuntimeError(
            f"ConvertInputFormat failed for {source}:\n{result.stdout}{result.stderr}"
        )
    return produced


def load(path: Path) -> Model:
    if path.suffix.lower() == ".idf":
        raise ValueError(f"{path} is IDF; convert() it to epJSON first")
    return json.loads(path.read_text())


def save(model: Model, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model, indent=2, sort_keys=True))
    return path


def apply_run_period(model: Model, period: RunPeriod) -> None:
    """Overwrite every RunPeriod so the model simulates exactly the requested window."""
    periods = model.get("RunPeriod")
    if not periods:
        raise ValueError("model has no RunPeriod object")
    for fields in periods.values():
        fields.update(
            begin_month=period.start_month,
            begin_day_of_month=period.start_day,
            end_month=period.end_month,
            end_day_of_month=period.end_day,
        )
        fields.pop("begin_year", None)
        fields.pop("end_year", None)


def enable_weather_run_period(model: Model) -> None:
    """Several stock example models ship with weather-file run periods switched off."""
    control = model.setdefault("SimulationControl", {"SimulationControl 1": {}})
    for fields in control.values():
        fields["run_simulation_for_weather_file_run_periods"] = "Yes"


def set_timesteps_per_hour(model: Model, n: int) -> None:
    model["Timestep"] = {"Timestep 1": {"number_of_timesteps_per_hour": n}}


def zones(model: Model) -> list[str]:
    return sorted(model.get("Zone", {}))


def air_loop_supply_nodes(model: Model) -> list[str]:
    """Each air loop's supply outlet node, where supply air temperature is set."""
    return sorted(
        fields["supply_side_outlet_node_names"]
        for fields in model.get("AirLoopHVAC", {}).values()
        if fields.get("supply_side_outlet_node_names")
    )


def supply_air_schedules(model: Model) -> list[str]:
    """Schedules driving supply air temperature on the air loops.

    The schedule is the correct handle, not the supply node. Mixed air managers derive the
    cooling coil's setpoint from the supply node during the same timestep, so overriding that
    node afterwards changes the reported setpoint and nothing else.
    """
    nodes = set(air_loop_supply_nodes(model))
    return sorted(
        {
            fields["schedule_name"]
            for fields in model.get("SetpointManager:Scheduled", {}).values()
            if fields.get("control_variable") == "Temperature"
            and fields.get("setpoint_node_or_nodelist_name") in nodes
        }
    )


def hvac_availability_schedules(model: Model) -> list[str]:
    """Schedules gating the air system. Writing zero to these stops the fans."""
    return sorted(
        {
            fields["availability_schedule_name"]
            for kind, objects in model.items()
            if kind.startswith("Fan:")
            for fields in objects.values()
            if fields.get("availability_schedule_name")
        }
    )


def _thermostat_targets(model: Model) -> list[tuple[dict, list[str]]]:
    """Each thermostat paired with the zones it governs, with zone lists expanded."""
    lists = {
        name: [e["zone_name"] for e in f.get("zones", [])]
        for name, f in model.get("ZoneList", {}).items()
    }
    canonical = {z.upper(): z for z in zones(model)}
    resolved = []
    for fields in model.get("ZoneControl:Thermostat", {}).values():
        target = fields.get("zone_or_zonelist_name")
        if not target:
            continue
        governed = lists[target] if target in lists else [target]
        resolved.append((fields, [canonical.get(z.upper(), z) for z in governed]))
    return resolved


def conditioned_zones(model: Model) -> list[str]:
    """Zones with a thermostat. Plenums and unconditioned spaces are excluded."""
    return sorted({z for _, governed in _thermostat_targets(model) for z in governed})


def thermostat_schedules(model: Model) -> dict[str, tuple[str, str]]:
    """Zone -> (heating schedule, cooling schedule), read from the model's thermostat wiring.

    Controllers offset the scheduled setpoint rather than the reported one, which would
    include their own previous override and ratchet away over the run.
    """
    dual = model.get("ThermostatSetpoint:DualSetpoint", {})
    found: dict[str, tuple[str, str]] = {}
    for fields, governed in _thermostat_targets(model):
        setpoint = dual.get(fields.get("control_1_name", ""))
        if fields.get("control_1_object_type") != "ThermostatSetpoint:DualSetpoint" or not setpoint:
            continue
        pair = (
            setpoint["heating_setpoint_temperature_schedule_name"],
            setpoint["cooling_setpoint_temperature_schedule_name"],
        )
        found.update(dict.fromkeys(governed, pair))
    return found
