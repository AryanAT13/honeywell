"""Supervisory control: what a controller sees, what it may command, and how it is written.

Commands go to the live EnergyPlus instance through zone thermostat actuators. No controller
here reasons about the model; that arrives in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

HEATING_ACTUATOR = ("Zone Temperature Control", "Heating Setpoint")
COOLING_ACTUATOR = ("Zone Temperature Control", "Cooling Setpoint")


@dataclass(frozen=True)
class Setpoints:
    heating: float
    cooling: float


@dataclass(frozen=True)
class ZoneObservation:
    """What a controller sees for one zone at one timestep."""

    zone: str
    temperature: float
    occupancy: float
    scheduled: Setpoints
    outdoor_temperature: float


class Controller(Protocol):
    def __call__(self, observation: ZoneObservation) -> Setpoints | None:
        """Return None to leave the zone on its native schedule."""


@dataclass(frozen=True)
class DeadbandOffset:
    """Widens the deadband while a zone is occupied, and leaves setback periods alone.

    The Phase 1 reference controller: enough to move energy measurably and to prove the
    write path, deliberately not enough to be clever.
    """

    heating: float = -0.5
    cooling: float = 0.5

    def __call__(self, observation: ZoneObservation) -> Setpoints | None:
        if observation.occupancy <= 0:
            return None
        scheduled = observation.scheduled
        return Setpoints(scheduled.heating + self.heating, scheduled.cooling + self.cooling)


class SetpointActuators:
    """Caches thermostat actuator handles and writes commands to the running instance."""

    def __init__(self, exchange, zones: list[str]):
        self.ex = exchange
        self.zones = zones
        self.handles: dict[tuple[str, str], int] = {}

    def resolve(self, state) -> None:
        for zone in self.zones:
            for field, (component, control) in (
                ("heating", HEATING_ACTUATOR),
                ("cooling", COOLING_ACTUATOR),
            ):
                handle = self.ex.get_actuator_handle(state, component, control, zone)
                if handle < 0:
                    raise RuntimeError(f"no {control} actuator for zone {zone}")
                self.handles[(zone, field)] = handle

    def write(self, state, zone: str, setpoints: Setpoints) -> None:
        self.ex.set_actuator_value(state, self.handles[(zone, "heating")], setpoints.heating)
        self.ex.set_actuator_value(state, self.handles[(zone, "cooling")], setpoints.cooling)

    def release(self, state, zone: str) -> None:
        """Hand a zone back to its schedule. Overrides otherwise risk latching."""
        self.ex.reset_actuator(state, self.handles[(zone, "heating")])
        self.ex.reset_actuator(state, self.handles[(zone, "cooling")])
