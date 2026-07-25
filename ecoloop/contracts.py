"""Data contracts shared across the pipeline. Every module boundary speaks these types."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

# TMY weather is non-leap; simulation timestamps are stamped with this year.
NOMINAL_YEAR = 2023


class RunPeriod(BaseModel):
    start_month: int = Field(ge=1, le=12)
    start_day: int = Field(ge=1, le=31)
    end_month: int = Field(ge=1, le=12)
    end_day: int = Field(ge=1, le=31)

    @classmethod
    def parse(cls, text: str) -> RunPeriod:
        """Accepts 'MM-DD:MM-DD', e.g. '07-01:07-03'."""
        start, end = text.split(":")
        sm, sd = (int(p) for p in start.split("-"))
        em, ed = (int(p) for p in end.split("-"))
        return cls(start_month=sm, start_day=sd, end_month=em, end_day=ed)

    def __str__(self) -> str:
        return (
            f"{self.start_month:02d}-{self.start_day:02d}:{self.end_month:02d}-{self.end_day:02d}"
        )


class RunSpec(BaseModel):
    """Everything needed to reproduce one simulation."""

    label: str
    model: Path
    weather: Path
    run_period: RunPeriod
    timesteps_per_hour: int = 6
    output_dir: Path

    @field_validator("timesteps_per_hour")
    @classmethod
    def _divides_an_hour(cls, v: int) -> int:
        if 60 % v or not 1 <= v <= 60:
            raise ValueError("timesteps_per_hour must divide 60")
        return v

    @property
    def timestep_seconds(self) -> int:
        return 3600 // self.timesteps_per_hour


class KpiSummary(BaseModel):
    """Headline outcome of a run. Comparisons between arms are made on these fields."""

    label: str
    timesteps: int
    simulated_hours: float
    wall_clock_seconds: float

    electricity_kwh: float
    hvac_electricity_kwh: float | None = None
    gas_kwh: float | None = None
    peak_demand_kw: float
    peak_demand_at: datetime | None = None

    unmet_heating_hours: float
    unmet_cooling_hours: float

    @property
    def unmet_hours(self) -> float:
        return self.unmet_heating_hours + self.unmet_cooling_hours


class RunResult(BaseModel):
    spec: RunSpec
    kpis: KpiSummary
    telemetry: Path
    severe_errors: int = 0
