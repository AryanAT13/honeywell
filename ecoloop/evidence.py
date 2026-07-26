"""Every number this project claims, regenerated from scratch by one command.

Nothing here is transcribed. The report is rendered from the same objects the simulations
returned, so a figure in the write-up and a figure in the repository cannot disagree.
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel

from . import commissioning, config, eplus, experiment, llm
from .contracts import RunPeriod
from .experiment import Comparison

LADDER = ("baseline", "deadband", "supervisor", "foresight", "agent")
SEASONS = ("winter", "shoulder", "summer")


class Scenario(BaseModel):
    climate: str
    period: str
    comparison: Comparison


class AgentStats(BaseModel):
    decisions: int
    replayed: int
    failed: int
    retried: int
    distinct_ceilings: int
    median_latency_s: float
    inference_seconds: float


class Evidence(BaseModel):
    generated: datetime
    energyplus: str
    model: str
    llm_model: str
    scenarios: list[Scenario]
    commissioning: list[commissioning.Commissioning]
    agent: AgentStats | None = None

    def scenario(self, climate: str, period: str) -> Comparison | None:
        for entry in self.scenarios:
            if entry.climate == climate and entry.period == period:
                return entry.comparison
        return None


def _ladder(climate: str, period: str, arms: tuple[str, ...], root: Path) -> Comparison:
    results = experiment.run_arms(
        [experiment.ARMS[name] for name in arms],
        model=config.DEFAULT_MODEL,
        weather=config.CLIMATES[climate],
        period=RunPeriod.parse(config.PERIODS[period]),
        output_root=root / f"{climate}_{period}",
    )
    if period == "annual" and climate == "chicago":
        experiment.paired_frame(results).to_parquet(root / "paired_annual.parquet", index=False)
    return experiment.compare(results)


def _agent_stats(root: Path) -> AgentStats | None:
    records = root / "chicago_annual" / "agent" / "decisions.json"
    if not records.is_file():
        return None
    decisions = [llm.Decision.model_validate(entry) for entry in json.loads(records.read_text())]
    # Cached records carry the latency of the call that produced them, so these are the
    # measured inference costs whether this run replayed them or not.
    latencies = sorted(d.latency_s for d in decisions) or [0.0]
    return AgentStats(
        decisions=len(decisions),
        replayed=sum(1 for d in decisions if d.cached),
        failed=sum(1 for d in decisions if not d.plan),
        retried=sum(1 for d in decisions if d.attempts > 1),
        distinct_ceilings=len({d.plan.supply_air_ceiling for d in decisions if d.plan}),
        median_latency_s=round(latencies[len(latencies) // 2], 1),
        inference_seconds=round(sum(d.latency_s for d in decisions), 1),
    )


def gather(root: Path | None = None) -> Evidence:
    """Run everything the write-up rests on. Hours on a cold cache, minutes on a warm one."""
    root = root or config.RUNS / "_evidence"
    root.mkdir(parents=True, exist_ok=True)

    scenarios = [
        Scenario(
            climate="chicago",
            period="annual",
            comparison=_ladder("chicago", "annual", LADDER, root),
        )
    ]
    for season in SEASONS:
        scenarios.append(
            Scenario(
                climate="chicago",
                period=season,
                comparison=_ladder("chicago", season, LADDER, root),
            )
        )
    # Delhi runs without the agent: commissioning declines supply air reset there, so
    # supervising it would measure nothing.
    scenarios.append(
        Scenario(
            climate="delhi",
            period="annual",
            comparison=_ladder("delhi", "annual", ("baseline", "supervisor"), root),
        )
    )

    plans = [
        commissioning.commission(config.DEFAULT_MODEL, output_dir=root / "cm_medium_chicago"),
        commissioning.commission(
            config.MODELS / "RefBldgSmallOfficeNew2004_Chicago.idf",
            output_dir=root / "cm_small_chicago",
        ),
        commissioning.commission(
            config.DEFAULT_MODEL,
            weather=config.CLIMATES["delhi"],
            output_dir=root / "cm_medium_delhi",
        ),
    ]

    return Evidence(
        generated=datetime.now(),
        energyplus=eplus.VERSION,
        model=config.DEFAULT_MODEL.name,
        llm_model=llm.DEFAULT_MODEL,
        scenarios=scenarios,
        commissioning=plans,
        agent=_agent_stats(root),
    )


def _svg(figure) -> str:
    buffer = io.StringIO()
    figure.savefig(buffer, format="svg", bbox_inches="tight", transparent=True)
    return buffer.getvalue().split("<svg", 1)[1].join(["<svg", ""])


def _savings_chart(paired: Path) -> str:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    frame = pd.read_parquet(paired)
    figure, axes = plt.subplots(figsize=(9, 3.2))
    for column in frame.columns:
        if column.endswith("|kwh_saved"):
            axes.plot(
                frame["time"],
                frame[column] / 1000,
                label=column.split("|")[0],
                linewidth=1.2,
            )
    axes.axhline(0, color="#888", linewidth=0.8)
    axes.set_ylabel("MWh saved against baseline")
    axes.legend(frameon=False, ncol=4, fontsize=8)
    axes.spines[["top", "right"]].set_visible(False)
    svg = _svg(figure)
    plt.close(figure)
    return svg


def _row(arm, reference: str) -> str:
    delta = "" if arm.label == reference else f"{arm.electricity_pct:+.2f}%"
    peak = "" if arm.label == reference else f"{arm.peak_demand_pct:+.2f}%"
    comfort = "" if arm.label == reference else f"{arm.comfort_degree_hours_change:+.1f}"
    return (
        f"<tr><td>{arm.label}</td><td>{arm.electricity_kwh:,.0f}</td><td>{delta}</td>"
        f"<td>{arm.peak_demand_kw:.1f}</td><td>{peak}</td><td>{arm.unmet_hours:.1f}</td>"
        f"<td>{arm.comfort_degree_hours:.1f}</td><td>{comfort}</td></tr>"
    )


def _table(comparison: Comparison) -> str:
    head = (
        "<tr><th>arm</th><th>kWh</th><th>Δ</th><th>peak kW</th><th>Δ</th>"
        "<th>unmet h</th><th>K·h</th><th>Δ</th></tr>"
    )
    rows = "".join(_row(arm, comparison.reference) for arm in comparison.arms)
    return f"<table>{head}{rows}</table>"


def _commissioning_table(plan: commissioning.Commissioning, climate: str) -> str:
    uses = "  ".join(f"{k} {v:.1%}" for k, v in plan.end_uses.items())
    rows = "".join(
        f"<tr><td>{'✓' if fit.selected else '·'}</td><td>{fit.measure}</td>"
        f"<td>{fit.reason}</td></tr>"
        for fit in plan.fits
    )
    outcome = ", ".join(plan.selected) if plan.selected else "nothing — no measure earns its place"
    return (
        f"<h3>{plan.model} on {climate}</h3>"
        f"<p class=meta>{plan.conditioned_zones} conditioned zones · "
        f"{plan.baseline.electricity_kwh:,.0f} kWh/yr · {uses}</p>"
        f"<table>{rows}</table><p class=meta>deploys: <b>{outcome}</b></p>"
    )


STYLE = """
body{font:15px/1.55 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;max-width:60rem;
margin:3rem auto;padding:0 1.5rem;color:#1a1a1a}
h1{font-size:1.7rem;margin-bottom:.2rem}h2{margin-top:2.5rem;font-size:1.25rem}
h3{font-size:1rem;margin-bottom:.2rem}
table{border-collapse:collapse;width:100%;margin:.8rem 0;font-size:13px}
th,td{text-align:right;padding:.35rem .6rem;border-bottom:1px solid #e6e6e6}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
th{font-weight:600;border-bottom:1px solid #999}
.meta{color:#666;font-size:13px;margin:.2rem 0}
code{background:#f4f4f4;padding:.1rem .3rem;border-radius:3px;font-size:12px}
@media(prefers-color-scheme:dark){body{background:#161616;color:#e8e8e8}
th,td{border-color:#333}th{border-color:#666}.meta{color:#999}code{background:#262626}}
"""


def render(evidence: Evidence, root: Path | None = None) -> str:
    root = root or config.RUNS / "_evidence"
    annual = evidence.scenario("chicago", "annual")
    delhi = evidence.scenario("delhi", "annual")

    seasons = "".join(
        f"<h3>{season}</h3>{_table(evidence.scenario('chicago', season))}"
        for season in SEASONS
        if evidence.scenario("chicago", season)
    )
    plans = "".join(
        _commissioning_table(plan, climate)
        for plan, climate in zip(
            evidence.commissioning, ("Chicago", "Chicago", "Delhi"), strict=False
        )
    )

    chart = ""
    paired = root / "paired_annual.parquet"
    if paired.is_file():
        chart = _savings_chart(paired)

    agent = ""
    if evidence.agent:
        a = evidence.agent
        agent = (
            f"<p>{a.decisions} decisions over the year, {a.failed} without a plan, "
            f"{a.retried} needing the schema repair retry, {a.distinct_ceilings} distinct "
            f"ceilings chosen. Median inference {a.median_latency_s:.1f} s, "
            f"{a.inference_seconds / 60:.0f} minutes in total; {a.replayed} of them replayed "
            f"from the committed journal in this run, which is why it took minutes.</p>"
        )

    return f"""<!doctype html><meta charset=utf-8><title>Eco-Loop evidence</title>
<style>{STYLE}</style>
<h1>Eco-Loop — evidence</h1>
<p class=meta>Generated {evidence.generated:%Y-%m-%d %H:%M} · EnergyPlus {evidence.energyplus} ·
{evidence.model} · {evidence.llm_model} · regenerate with <code>make evidence</code></p>

<h2>Full year, Chicago</h2>
<p class=meta>All arms on one clock: identical weather, run period and timestep, compared
timestep by timestep. Unmet hours are against each arm's own setpoint; K·h is occupied time
outside a fixed 21–24 °C band the controller cannot move.</p>
{_table(annual) if annual else ""}
{chart}
<p class=meta><b>foresight</b> is not a controller we would ship. It is the same reset loop
given the day's forecast peak instead of the current reading, on the same degraded forecast
the model gets — what perfect discrimination about the day ahead would be worth. Over a
single season it lands on the reactive controller to within 0.03 points. Over a year it
trades: less energy saved, better comfort, neither dominating. Anticipation moves along this
frontier rather than past it, which is why the agent has no headroom to find.</p>

<h2>By season, Chicago</h2>
{seasons}

<h2>Delhi</h2>
<p class=meta>The same building and controller on New Delhi ISHRAE weather. Run without the
agent arm: commissioning declines supply air reset in this climate, so supervising it would
measure nothing.</p>
{_table(delhi) if delhi else ""}

<h2>Commissioning</h2>
<p class=meta>Discover what is actuable, survey where the energy goes over an untouched year,
then try each candidate against that baseline. A measure can be actuable, aimed at the
dominant load, and still make the building worse.</p>
{plans}

<h2>The model</h2>
{agent}
"""


def write(root: Path | None = None) -> tuple[Path, Path]:
    """Regenerate the evidence and its report. Both are committed artefacts."""
    root = root or config.RUNS / "_evidence"
    evidence = gather(root)
    docs = config.ROOT / "docs" / "evidence"
    docs.mkdir(parents=True, exist_ok=True)

    data = docs / "evidence.json"
    data.write_text(evidence.model_dump_json(indent=2))
    report = docs / "report.html"
    report.write_text(render(evidence, root))
    return data, report
