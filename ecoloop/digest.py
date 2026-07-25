"""The fixed-size situation report a policy author reasons over.

Its size depends on the number of zones, not on how long the run has been going, which is
what keeps the eventual LLM prompt bounded. The deterministic author consumes the same
digest, so anything an author needs is guaranteed to already be in it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from .contracts import ComfortBand
from .control import Setpoints, ZoneObservation

# How far past its setpoint a zone must drift before it counts as asking for attention.
REQUEST_MARGIN_K = 0.5


class ZoneDigest(BaseModel):
    zone: str
    temperature: float
    occupied: bool
    cooling_request: bool
    heating_request: bool
    excursion_k: float


class StateDigest(BaseModel):
    time: datetime
    outdoor_temperature: float
    demand_kw: float
    cooling_requests: int
    heating_requests: int
    warmest_zone: str
    worst_excursion_k: float
    zones: list[ZoneDigest]


def build(
    time: datetime,
    observations: dict[str, ZoneObservation],
    effective: dict[str, Setpoints],
    demand_kw: float,
    comfort: ComfortBand,
) -> StateDigest:
    zones = []
    for zone, observation in observations.items():
        setpoints = effective[zone]
        occupied = observation.occupancy > 0
        temperature = observation.temperature
        beyond = max(
            comfort.lower - comfort.tolerance - temperature,
            temperature - comfort.upper - comfort.tolerance,
        )
        zones.append(
            ZoneDigest(
                zone=zone,
                temperature=temperature,
                occupied=occupied,
                cooling_request=temperature > setpoints.cooling + REQUEST_MARGIN_K,
                heating_request=temperature < setpoints.heating - REQUEST_MARGIN_K,
                excursion_k=max(0.0, beyond) if occupied else 0.0,
            )
        )

    warmest = max(zones, key=lambda z: z.temperature)
    return StateDigest(
        time=time,
        outdoor_temperature=next(iter(observations.values())).outdoor_temperature,
        demand_kw=demand_kw,
        cooling_requests=sum(z.cooling_request for z in zones),
        heating_requests=sum(z.heating_request for z in zones),
        warmest_zone=warmest.zone,
        worst_excursion_k=max(z.excursion_k for z in zones),
        zones=zones,
    )
