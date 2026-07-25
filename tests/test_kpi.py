"""Why comfort is not scored against the setpoint the controller chose."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from ecoloop import kpi
from ecoloop.contracts import RunPeriod, RunSpec

ZONE = "Zone1"


def telemetry(cooling_setpoint: float, temperature: float, steps: int = 144) -> pd.DataFrame:
    start = datetime(2023, 7, 1)
    return pd.DataFrame(
        {
            "time": [start + timedelta(minutes=10 * i) for i in range(steps)],
            "electricity_j": [1e8] * steps,
            "hvac_electricity_j": [0.0] * steps,
            "gas_j": [0.0] * steps,
            f"{ZONE}|temp": [temperature] * steps,
            f"{ZONE}|occupancy": [5.0] * steps,
            f"{ZONE}|heat_sp": [21.0] * steps,
            f"{ZONE}|cool_sp": [cooling_setpoint] * steps,
        }
    )


def summarize(cooling_setpoint: float, temperature: float):
    spec = RunSpec(
        label="synthetic",
        model=Path("unused.idf"),
        weather=Path("unused.epw"),
        run_period=RunPeriod.parse("07-01:07-01"),
        output_dir=Path("unused"),
    )
    frame = telemetry(cooling_setpoint, temperature)
    return kpi.summarize(frame, spec, [ZONE], wall_clock=0.0)


def test_a_controller_can_zero_its_own_unmet_hours_by_giving_up():
    """A zone held at 29 C is uninhabitable, and reports perfectly met setpoints.

    Unmet hours are measured against whatever the controller commands, so they cannot bound
    comfort. The fixed band is what actually registers the damage.
    """
    honest = summarize(cooling_setpoint=24.0, temperature=29.0)
    gamed = summarize(cooling_setpoint=30.0, temperature=29.0)

    assert honest.unmet_hours == 24.0
    assert gamed.unmet_hours == 0.0

    assert gamed.comfort_degree_hours == honest.comfort_degree_hours
    assert gamed.comfort_degree_hours > 0
