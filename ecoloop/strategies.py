"""Deterministic policy authors. The LLM becomes another author of the same object."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .digest import StateDigest
from .policy import Policy


class PolicyAuthor(Protocol):
    def __call__(self, digest: StateDigest) -> Policy: ...


@dataclass
class Fixed:
    """Holds one policy for the whole run, whoever wrote it."""

    policy: Policy

    def __call__(self, digest: StateDigest) -> Policy:
        return self.policy


@dataclass
class SupplyAirReset:
    """Supply air temperature reset, after ASHRAE Guideline 36.

    This model holds supply air at a constant 12.8 C all year and reheats at the terminals,
    which costs more electricity than cooling does. Raising supply air as far as conditions
    allow removes that reheat without touching a thermostat.

    Outdoor temperature sets the ambition feedforward, because on a hot day there is no
    reheat to recover and a warm deck only starves the coils.

    The pullback from that ceiling is driven by the single worst zone rather than a count of
    unhappy ones. Two internal-load-dominated core zones in this building need cooling in
    every season while the perimeter needs reheat, and one supply air temperature has to
    serve both. Averaging across fifteen zones lets the cores overheat; letting the worst
    zone veto the elevation does not.

    Zone bands are deliberately left alone so the measured effect is supply air alone.
    """

    minimum: float = 12.8
    maximum: float = 18.0
    outdoor_cold: float = 15.6
    outdoor_hot: float = 21.1
    trim: float = 0.05
    respond: float = 4.0
    correction: float = field(default=0.0)

    def ceiling(self, outdoor: float) -> float:
        span = (outdoor - self.outdoor_cold) / (self.outdoor_hot - self.outdoor_cold)
        return self.maximum - min(max(span, 0.0), 1.0) * (self.maximum - self.minimum)

    def __call__(self, digest: StateDigest) -> Policy:
        excursion = digest.worst_excursion_k
        if excursion > 0:
            self.correction += self.respond * excursion
        else:
            self.correction = max(0.0, self.correction - self.trim)
        # Anti-windup. Without this the correction accumulates all summer, where it cannot
        # help because the outdoor cap already holds supply air at the minimum, and then
        # takes months to decay back through the shoulder season.
        self.correction = min(self.correction, self.maximum - self.minimum)

        ceiling = self.ceiling(digest.outdoor_temperature)
        supply_air = min(max(ceiling - self.correction, self.minimum), self.maximum)
        return Policy(
            supply_air_temperature=supply_air,
            reason=f"outdoor {digest.outdoor_temperature:.1f}C caps supply air at "
            f"{ceiling:.1f}C; worst zone {excursion:.2f}K outside band",
        )
