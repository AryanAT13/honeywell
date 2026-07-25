"""Turns a telemetry frame into the headline numbers that arms are compared on."""

from __future__ import annotations

import pandas as pd

from .contracts import KpiSummary, RunSpec

JOULES_PER_KWH = 3.6e6

# EnergyPlus counts a setpoint as unmet outside this throttling range.
UNMET_TOLERANCE_C = 0.2


def _unmet_masks(df: pd.DataFrame, zones: list[str]) -> tuple[pd.Series, pd.Series]:
    """Facility unmet time: any occupied zone outside its throttling range, per ASHRAE 90.1."""
    heating = pd.Series(False, index=df.index)
    cooling = pd.Series(False, index=df.index)
    for zone in zones:
        occupied = df[f"{zone}|occupancy"] > 0
        temp = df[f"{zone}|temp"]
        heating |= occupied & (temp < df[f"{zone}|heat_sp"] - UNMET_TOLERANCE_C)
        cooling |= occupied & (temp > df[f"{zone}|cool_sp"] + UNMET_TOLERANCE_C)
    return heating, cooling


def summarize(df: pd.DataFrame, spec: RunSpec, zones: list[str], wall_clock: float) -> KpiSummary:
    hours_per_step = 1 / spec.timesteps_per_hour
    demand_kw = df["electricity_j"] / spec.timestep_seconds / 1000
    heating_unmet, cooling_unmet = _unmet_masks(df, zones)
    hvac_j = df["hvac_electricity_j"].sum()
    gas_j = df["gas_j"].sum()

    return KpiSummary(
        label=spec.label,
        timesteps=len(df),
        simulated_hours=len(df) * hours_per_step,
        wall_clock_seconds=round(wall_clock, 2),
        electricity_kwh=df["electricity_j"].sum() / JOULES_PER_KWH,
        hvac_electricity_kwh=hvac_j / JOULES_PER_KWH if hvac_j > 0 else None,
        gas_kwh=gas_j / JOULES_PER_KWH if gas_j > 0 else None,
        peak_demand_kw=demand_kw.max(),
        peak_demand_at=df.loc[demand_kw.idxmax(), "time"].to_pydatetime(),
        unmet_heating_hours=heating_unmet.sum() * hours_per_step,
        unmet_cooling_hours=cooling_unmet.sum() * hours_per_step,
    )
