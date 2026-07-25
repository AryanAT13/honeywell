"""Supervisory control: what a controller sees, what it may command, and how it is written.

Commands go to the live EnergyPlus instance through zone thermostat actuators. No controller
here reasons about the model; that arrives in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass

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


class SupplyAirActuators:
    """Resets supply air temperature by overriding the schedules the setpoint managers read.

    Written before the managers run, so the model's own manager chain propagates the change
    down to the coils.
    """

    def __init__(self, exchange, schedules: list[str]):
        self.ex = exchange
        self.schedules = schedules
        self.handles: dict[str, int] = {}

    def resolve(self, state) -> None:
        for schedule in self.schedules:
            handle = self.ex.get_actuator_handle(
                state, "Schedule:Compact", "Schedule Value", schedule
            )
            if handle < 0:
                raise RuntimeError(f"no schedule actuator for {schedule}")
            self.handles[schedule] = handle

    def write(self, state, temperature: float) -> None:
        for handle in self.handles.values():
            self.ex.set_actuator_value(state, handle, temperature)
