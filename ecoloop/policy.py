"""The control plan, the limits it must respect, and how it becomes per-timestep commands.

Policy is the contract between whoever decides strategy and the machinery that executes it.
Phase 2 authors it deterministically; the LLM authors the same object from Phase 4, so only
the author changes.
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel

from .contracts import ComfortBand
from .control import Setpoints, ZoneObservation


class Band(BaseModel):
    heating: float
    cooling: float

    @property
    def width(self) -> float:
        return self.cooling - self.heating


class Policy(BaseModel):
    """The plan for one horizon. A band left unset leaves those zones on their own schedule."""

    occupied: Band | None = None
    unoccupied: Band | None = None
    supply_air_temperature: float | None = None
    hvac_available: bool | None = None
    reason: str = ""


class Limits(BaseModel):
    """The feasible set. Nothing reaches the instance from outside it."""

    min_deadband: float = 1.0
    setback_heating_min: float = 12.0
    setback_cooling_max: float = 32.0
    supply_air_min: float = 12.0
    supply_air_max: float = 18.0


class Guardian:
    """Projects a policy onto the feasible set and records everything it had to change.

    Occupied setpoints are held inside the comfort contract, so a controller cannot buy
    energy by moving the band it is scored against.
    """

    def __init__(self, comfort: ComfortBand, limits: Limits | None = None):
        self.comfort = comfort
        self.limits = limits or Limits()
        self.clamps: Counter[str] = Counter()

    def _clamp(self, name: str, value: float, low: float, high: float) -> float:
        bounded = min(max(value, low), high)
        if bounded != value:
            self.clamps[name] += 1
        return bounded

    def review(self, proposed: Policy) -> Policy:
        occupied, unoccupied = proposed.occupied, proposed.unoccupied
        limits = self.limits

        if occupied:
            heating = self._clamp("occupied.heating", occupied.heating, self.comfort.lower, 1e3)
            cooling = self._clamp("occupied.cooling", occupied.cooling, -1e3, self.comfort.upper)
            if cooling - heating < limits.min_deadband:
                heating = self._clamp(
                    "occupied.deadband", heating, self.comfort.lower, cooling - limits.min_deadband
                )
            occupied = Band(heating=heating, cooling=cooling)

        if unoccupied:
            heating = self._clamp(
                "unoccupied.heating", unoccupied.heating, limits.setback_heating_min, 1e3
            )
            cooling = self._clamp(
                "unoccupied.cooling", unoccupied.cooling, -1e3, limits.setback_cooling_max
            )
            if cooling - heating < limits.min_deadband:
                heating = cooling - limits.min_deadband
                self.clamps["unoccupied.deadband"] += 1
            unoccupied = Band(heating=heating, cooling=cooling)

        supply_air = proposed.supply_air_temperature
        if supply_air is not None:
            supply_air = self._clamp(
                "supply_air", supply_air, limits.supply_air_min, limits.supply_air_max
            )

        # Copied and updated rather than rebuilt: a rebuild silently drops any field the
        # guardian does not know about, so adding one to Policy would quietly disable it.
        return proposed.model_copy(
            update={
                "occupied": occupied,
                "unoccupied": unoccupied,
                "supply_air_temperature": supply_air,
            }
        )


def apply(policy: Policy | None, observation: ZoneObservation) -> Setpoints | None:
    """The zone's commanded setpoints, or None to leave it on its own schedule."""
    if policy is None:
        return None
    band = policy.occupied if observation.occupancy > 0 else policy.unoccupied
    return Setpoints(band.heating, band.cooling) if band else None
