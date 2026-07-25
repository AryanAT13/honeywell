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


def conditioned_zones(model: Model) -> list[str]:
    """Zones with a thermostat. Plenums and unconditioned spaces are excluded."""
    lists = {
        name: [e["zone_name"] for e in f.get("zones", [])]
        for name, f in model.get("ZoneList", {}).items()
    }
    found: set[str] = set()
    for fields in model.get("ZoneControl:Thermostat", {}).values():
        target = fields.get("zone_or_zonelist_name")
        if target in lists:
            found.update(lists[target])
        elif target:
            found.add(target)
    known = {z.upper(): z for z in zones(model)}
    return sorted({known.get(z.upper(), z) for z in found})
