"""Deciding what to do with a building nobody has configured.

The deterministic supervisor is not portable. Pointed at a model with no central supply air
schedule it runs every decision and changes nothing, silently, and reports success. That is
the failure a real deployment ships and never notices.

Commissioning is the step that makes that a refusal instead of a no-op: discover what can be
actuated, measure where the energy actually goes, and select measures that are both
applicable and worth deploying.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from . import config, runner
from . import model as model_io
from .contracts import KpiSummary, RunPeriod, RunSpec
from .strategies import AvailabilityTrim, Combined, PolicyAuthor, SupplyAirReset

# A measure has to be aimed at a load big enough to be worth the risk of touching it.
MATERIAL_SHARE = 0.03

# And it then has to earn its place by trial, because applicability does not imply benefit.
MIN_SAVING = 0.005
MAX_COMFORT_LOSS = 0.01

END_USES = ("heating_j", "cooling_j", "fans_j", "lights_j", "equipment_j")


@dataclass(frozen=True)
class Measure:
    name: str
    targets: str
    handle: Callable[[model_io.Model], list[str]]
    build: Callable[[], PolicyAuthor]


CATALOGUE = (
    Measure("supply_air_reset", "heating", model_io.supply_air_schedules, SupplyAirReset),
    Measure("hvac_availability", "fans", model_io.hvac_availability_schedules, AvailabilityTrim),
)


class MeasureFit(BaseModel):
    measure: str
    targets: str
    actuable: bool
    target_share: float
    electricity_pct: float | None = None
    comfort_change: float | None = None
    selected: bool = False
    reason: str = ""


class Commissioning(BaseModel):
    model: str
    conditioned_zones: int
    end_uses: dict[str, float]
    baseline: KpiSummary
    fits: list[MeasureFit]

    @property
    def selected(self) -> list[str]:
        return [fit.measure for fit in self.fits if fit.selected]


class NothingApplies(RuntimeError):
    """No measure in the catalogue can act on this building."""


def _run(model, weather, period, output_dir, label, author=None):
    return runner.run(
        RunSpec(
            label=label,
            model=model,
            weather=weather,
            run_period=period,
            output_dir=output_dir / label,
        ),
        author,
    )


def end_use_shares(telemetry: Path) -> dict[str, float]:
    """Where this building's electricity actually goes."""
    frame = pd.read_parquet(telemetry)
    total = frame["electricity_j"].sum()
    return {use[:-2]: round(frame[use].sum() / total, 4) for use in END_USES}


def commission(
    model: Path,
    weather: Path | None = None,
    period: str = "annual",
    output_dir: Path | None = None,
) -> Commissioning:
    """Work out which measures this building can take, and which are worth taking.

    Surveyed over a full year: a single week misreports the annual mix badly enough to
    change the answer, and the run costs 14 seconds.
    """
    weather = weather or config.DEFAULT_WEATHER
    window = RunPeriod.parse(config.PERIODS.get(period, period))
    root = output_dir or config.RUNS / "_commission"
    building = model_io.load(model_io.convert(model, config.RUNS / "_models", "epJSON"))

    reference = _run(model, weather, window, root, "baseline")
    end_uses = end_use_shares(reference.telemetry)

    fits = []
    for measure in CATALOGUE:
        share = end_uses.get(measure.targets, 0.0)
        fit = MeasureFit(
            measure=measure.name,
            targets=measure.targets,
            actuable=bool(measure.handle(building)),
            target_share=share,
        )
        if not fit.actuable:
            fit.reason = f"nothing to actuate; no {measure.targets} handle in this model"
        elif share < MATERIAL_SHARE:
            fit.reason = (
                f"{measure.targets} is {share:.1%} of electricity, "
                f"below the {MATERIAL_SHARE:.0%} worth acting on"
            )
        else:
            trial = _run(model, weather, window, root, measure.name, measure.build())
            fit.electricity_pct = round(
                100 * (trial.kpis.electricity_kwh / reference.kpis.electricity_kwh - 1), 2
            )
            fit.comfort_change = round(
                trial.kpis.comfort_degree_hours - reference.kpis.comfort_degree_hours, 1
            )
            allowed = MAX_COMFORT_LOSS * reference.kpis.comfort_degree_hours
            if fit.electricity_pct > -100 * MIN_SAVING:
                fit.reason = f"tried it: {fit.electricity_pct:+.2f}% electricity, not worth it"
            elif fit.comfort_change > allowed:
                fit.reason = (
                    f"tried it: saves {-fit.electricity_pct:.2f}% but costs "
                    f"{fit.comfort_change:+.1f} K.h of comfort"
                )
            else:
                fit.selected = True
                fit.reason = (
                    f"tried it: {fit.electricity_pct:+.2f}% electricity, "
                    f"{fit.comfort_change:+.1f} K.h comfort"
                )
        fits.append(fit)

    return Commissioning(
        model=model.name,
        conditioned_zones=len(model_io.conditioned_zones(building)),
        end_uses=end_uses,
        baseline=reference.kpis,
        fits=fits,
    )


def author_for(plan: Commissioning) -> PolicyAuthor:
    """The controller this building was commissioned into."""
    chosen = [m for m in CATALOGUE if m.name in plan.selected]
    if not chosen:
        raise NothingApplies(
            f"{plan.model}: no measure in the catalogue earns its place here. "
            + "; ".join(f"{fit.measure} — {fit.reason}" for fit in plan.fits)
        )
    return chosen[0].build() if len(chosen) == 1 else Combined([m.build() for m in chosen])
