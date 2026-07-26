"""Deterministic policy authors. The LLM becomes another author of the same object."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from . import llm
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
    requested_ceiling: float | None = field(default=None)

    def ceiling(self, outdoor: float) -> float:
        """Where supply air would sit with no zone complaining.

        A supervisor may ask for a lower ceiling than outdoor temperature alone implies, and
        never a higher one. Anticipating a warm afternoon is a judgement the outdoor curve
        cannot make; overriding the curve upward on a hot day just starves the coils, and a
        supervisor confident enough to try is the case worth being robust to.
        """
        span = (outdoor - self.outdoor_cold) / (self.outdoor_hot - self.outdoor_cold)
        curve = self.maximum - min(max(span, 0.0), 1.0) * (self.maximum - self.minimum)
        if self.requested_ceiling is None:
            return curve
        return min(curve, max(self.requested_ceiling, self.minimum))

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


@dataclass
class Supervised:
    """A local model sets the daily supply air ambition; the reset loop still runs beneath it.

    The split follows what Phase 2 measured. Trim and respond is good at reacting and
    structurally cannot anticipate, because it only sees a zone after it has left the comfort
    band. A day-ahead forecast is exactly the input needed to lower the ceiling before a mild
    afternoon drives the cores into cooling, and choosing that number is a judgement call
    rather than an arithmetic one.

    If the model is slow, unreachable or wrong, the reset loop keeps its previous ceiling, and
    with no plan at all it falls back to its own outdoor-temperature curve.
    """

    planner: llm.Planner
    reset: SupplyAirReset = field(default_factory=SupplyAirReset)
    replan_hours: float = 24.0
    trigger_excursion_k: float = 1.0
    trigger_cooldown_hours: float = 6.0

    _planned_at: datetime | None = field(default=None)
    _scorecard: _Window = field(default_factory=lambda: _Window())

    def _due(self, now: datetime, excursion: float) -> bool:
        if self._planned_at is None:
            return True
        elapsed = (now - self._planned_at).total_seconds() / 3600
        if elapsed >= self.replan_hours:
            return True
        return excursion >= self.trigger_excursion_k and elapsed >= self.trigger_cooldown_hours

    def __call__(self, digest: StateDigest) -> Policy:
        if self._due(digest.time, digest.worst_excursion_k):
            scorecard = self._scorecard.summarise(self.reset.requested_ceiling, digest.time)
            decision = self.planner.plan(digest, scorecard)
            if decision.plan:
                self.reset.requested_ceiling = decision.plan.supply_air_ceiling
            self._planned_at = digest.time
            self._scorecard = _Window(digest.time)

        policy = self.reset(digest)
        self._scorecard.observe(digest, policy.supply_air_temperature)
        return policy

    @property
    def decisions(self) -> list[llm.Decision]:
        return self.planner.decisions


@dataclass
class _Window:
    """What has happened since the current plan took effect."""

    since: datetime | None = None
    supply_air_sum: float = 0.0
    samples: int = 0
    worst_excursion_k: float = 0.0

    def observe(self, digest: StateDigest, supply_air: float | None) -> None:
        self.worst_excursion_k = max(self.worst_excursion_k, digest.worst_excursion_k)
        if supply_air is not None:
            self.supply_air_sum += supply_air
            self.samples += 1

    def summarise(self, ceiling: float | None, now: datetime) -> llm.Scorecard | None:
        if not self.samples or ceiling is None or self.since is None:
            return None
        return llm.Scorecard(
            ceiling=ceiling,
            hours=(now - self.since).total_seconds() / 3600,
            mean_supply_air=self.supply_air_sum / self.samples,
            worst_excursion_k=self.worst_excursion_k,
        )
