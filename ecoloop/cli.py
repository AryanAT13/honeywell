"""Command line entry point."""

from __future__ import annotations

from pathlib import Path

import typer

from . import config, eplus, experiment, runner, tools
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
    weather: str = "chicago",
    period: str = "smoke",
    timesteps_per_hour: int = 6,
) -> None:
    """Run one simulation and write telemetry plus a KPI summary."""
    spec = RunSpec(
        label=label,
        model=model,
        weather=config.CLIMATES.get(weather, Path(weather)),
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


@app.command()
def compare(
    arms: str = "baseline,deadband",
    model: Path = config.DEFAULT_MODEL,
    weather: str = "chicago",
    period: str = "summer",
    timesteps_per_hour: int = 6,
) -> None:
    """Run control arms over identical weather and report paired differences."""
    try:
        selected = [experiment.ARMS[name] for name in arms.split(",")]
    except KeyError as unknown:
        typer.echo(f"unknown arm {unknown}; available: {', '.join(experiment.ARMS)}")
        raise typer.Exit(1) from None

    results = experiment.run_arms(
        selected,
        model=model,
        weather=config.CLIMATES.get(weather, Path(weather)),
        period=RunPeriod.parse(config.PERIODS.get(period, period)),
        timesteps_per_hour=timesteps_per_hour,
    )
    report = experiment.compare(results)
    frame = experiment.paired_frame(results)

    config.RUNS.mkdir(parents=True, exist_ok=True)
    (config.RUNS / "comparison.json").write_text(report.model_dump_json(indent=2))
    frame.to_parquet(config.RUNS / "comparison.parquet", index=False)

    typer.echo(
        f"\n{report.run_period}  {report.timesteps} timesteps  reference {report.reference}\n"
    )
    band = results[0].kpis.comfort_band
    typer.echo(
        f"{'arm':<12}{'kWh':>11}{'%':>9}{'peak kW':>10}{'%':>9}"
        f"{'unmet h':>10}{'outside h':>12}{'K.h':>9}{'Δ':>9}"
    )
    for arm in report.arms:
        is_reference = arm.label == report.reference
        typer.echo(
            f"{arm.label:<12}{arm.electricity_kwh:>11.1f}"
            f"{'-' if is_reference else format(arm.electricity_pct, '+.2f'):>9}"
            f"{arm.peak_demand_kw:>10.1f}"
            f"{'-' if is_reference else format(arm.peak_demand_pct, '+.2f'):>9}"
            f"{arm.unmet_hours:>10.2f}{arm.comfort_exceedance_hours:>12.2f}"
            f"{arm.comfort_degree_hours:>9.1f}"
            f"{'-' if is_reference else format(arm.comfort_degree_hours_change, '+.1f'):>9}"
        )
    typer.echo(
        f"\nunmet h is against each arm's own setpoint; "
        f"outside h and K.h are against the fixed {band.lower}-{band.upper} C band"
    )
    typer.echo(f"\nwrote {config.RUNS / 'comparison.json'}")


@app.command()
def decisions(label: str = typer.Argument("agent")) -> None:
    """Show what the model decided during a run, and what each call cost."""
    records = tools.run_decisions(label)
    if not records:
        typer.echo(f"no recorded decisions for '{label}'")
        raise typer.Exit(1)

    served = sum(1 for d in records if d.cached)
    failed = [d for d in records if not d.plan]
    latencies = sorted(d.latency_s for d in records if not d.cached)
    typer.echo(
        f"{len(records)} decisions, {served} from cache, {len(failed)} without a plan, "
        f"median {latencies[len(latencies) // 2] if latencies else 0:.1f} s"
    )
    for record in records:
        plan = record.plan
        detail = (
            f"{plan.supply_air_ceiling:5.1f} C  {plan.reason}"
            if plan
            else f"   --   {record.error}"
        )
        typer.echo(f"  {record.time:%Y-%m-%d %H:%M}  {detail}")


@app.command()
def serve(http: bool = False) -> None:
    """Serve the capability layer over MCP, on stdio by default.

    Nothing may be written to stdout on stdio transport; it carries the protocol.
    """
    from .mcp_server import server

    server.run(transport="streamable-http" if http else "stdio")


if __name__ == "__main__":
    app()
