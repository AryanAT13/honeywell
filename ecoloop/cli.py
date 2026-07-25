"""Command line entry point."""

from __future__ import annotations

from pathlib import Path

import typer

from . import config, eplus, runner
from . import model as model_io
from .contracts import RunPeriod, RunSpec

app = typer.Typer(add_completion=False, help="Eco-Loop building agent")


@app.command()
def info(model: Path = config.DEFAULT_MODEL) -> None:
    """Report the EnergyPlus install and the control surface of a model."""
    typer.echo(f"EnergyPlus {eplus.VERSION} at {eplus.root()}")
    if not model.is_file():
        typer.echo(f"model missing: {model}")
        raise typer.Exit(1)

    source = (
        model_io.convert(model, config.RUNS / "_info", "epJSON")
        if model.suffix == ".idf"
        else model
    )
    building = model_io.load(source)
    conditioned = model_io.conditioned_zones(building)
    typer.echo(
        f"model {model.name}: {len(model_io.zones(building))} zones, {len(conditioned)} conditioned"
    )
    for zone in conditioned:
        typer.echo(f"  {zone}")


@app.command()
def run(
    label: str = "baseline",
    model: Path = config.DEFAULT_MODEL,
    weather: Path = config.DEFAULT_WEATHER,
    period: str = "smoke",
    timesteps_per_hour: int = 6,
) -> None:
    """Run one simulation and write telemetry plus a KPI summary."""
    spec = RunSpec(
        label=label,
        model=model,
        weather=weather,
        run_period=RunPeriod.parse(config.PERIODS.get(period, period)),
        timesteps_per_hour=timesteps_per_hour,
        output_dir=config.RUNS / label,
    )
    result = runner.run(spec)
    k = result.kpis

    typer.echo(
        f"\n{k.label}  [{spec.run_period}]  {k.simulated_hours:.0f} h "
        f"in {k.wall_clock_seconds:.1f} s wall clock"
    )
    typer.echo(f"  electricity     {k.electricity_kwh:10.1f} kWh")
    if k.gas_kwh:
        typer.echo(f"  natural gas     {k.gas_kwh:10.1f} kWh")
    typer.echo(f"  peak demand     {k.peak_demand_kw:10.1f} kW  at {k.peak_demand_at}")
    typer.echo(
        f"  unmet occupied  {k.unmet_hours:10.1f} h  "
        f"(heating {k.unmet_heating_hours:.1f} / cooling {k.unmet_cooling_hours:.1f})"
    )
    typer.echo(f"  severe errors   {result.severe_errors:10d}")
    typer.echo(f"\nwrote {result.telemetry}")


if __name__ == "__main__":
    app()
