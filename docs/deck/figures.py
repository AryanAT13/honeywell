"""Deck figures, drawn from the published evidence rather than retyped."""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
EV = json.loads(Path("/Users/aryan/Downloads/honeywell/docs/evidence/evidence.json").read_text())

INK = "#1F3864"
NAVY = "#1F497D"
BLUE = "#4F81BD"
GOOD = "#2E7D32"
BAD = "#C0504D"
MUTED = "#6B7280"
CARD = "#EEF2F7"

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})


def annual():
    for s in EV["scenarios"]:
        if s["climate"] == "chicago" and s["period"] == "annual":
            return {a["label"]: a for a in s["comparison"]["arms"]}
    raise SystemExit("no annual scenario")


# ---------------------------------------------------------------- ladder
arms = annual()
order = ["deadband", "agent", "foresight", "supervisor"]
labels = {
    "deadband": "Widen thermostat band\n(the obvious move)",
    "agent": "LLM-supervised",
    "foresight": "Perfect foresight\n(the bound)",
    "supervisor": "Eco-Loop supervisor\n(deployed)",
}
saving = [-arms[a]["electricity_pct"] for a in order]
comfort = [arms[a]["comfort_degree_hours_change"] for a in order]

fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11.5, 3.9), gridspec_kw={"width_ratios": [1.55, 1]})

colors = [BAD, MUTED, BLUE, GOOD]
bars = ax.barh(range(4), saving, color=colors, height=0.62)
ax.set_yticks(range(4))
ax.set_yticklabels([labels[a] for a in order], fontsize=11)
for i, (bar, value) in enumerate(zip(bars, saving, strict=True)):
    ax.text(value + 0.12, i, f"{value:.2f}%", va="center", fontsize=12,
            fontweight="bold", color=colors[i])
ax.set_xlim(0, 6.6)
ax.set_xlabel("electricity saved vs untouched building, full year", fontsize=10, color=MUTED)
ax.set_title("Energy", fontsize=13, fontweight="bold", color=INK, loc="left")
ax.spines[["top", "right", "left"]].set_visible(False)
ax.tick_params(axis="y", length=0)
ax.xaxis.grid(True, color="#E3E8EF")
ax.set_axisbelow(True)

c = ax2.barh(range(4), comfort, color=[BAD if v > 30 else (GOOD if v <= 0 else BLUE) for v in comfort],
             height=0.62)
ax2.set_yticks(range(4))
ax2.set_yticklabels([])
for i, (bar, value) in enumerate(zip(c, comfort, strict=True)):
    ax2.text(value + (18 if value > 0 else -18), i, f"{value:+.0f}", va="center",
             ha="left" if value > 0 else "right", fontsize=12, fontweight="bold")
ax2.axvline(0, color="#9AA5B1", linewidth=1)
ax2.set_xlim(-120, 780)
ax2.set_xlabel("comfort cost, degree-hours (lower is better)", fontsize=10, color=MUTED)
ax2.set_title("Comfort", fontsize=13, fontweight="bold", color=INK, loc="left")
ax2.spines[["top", "right", "left"]].set_visible(False)
ax2.tick_params(axis="y", length=0)
ax2.xaxis.grid(True, color="#E3E8EF")
ax2.set_axisbelow(True)

fig.tight_layout()
fig.savefig(OUT / "ladder.png", dpi=200, bbox_inches="tight", facecolor="white")
plt.close(fig)


# ------------------------------------------------------- commissioning
fig, axes = plt.subplots(1, 3, figsize=(11.5, 3.5))
cases = [
    ("Medium office\nChicago", {"reheat": 18.5, "fans": 2.6},
     [("supply air reset", "tried: -5.33%", GOOD), ("fan availability", "fans only 2.6%", MUTED)],
     "DEPLOYS\nsupply air reset", GOOD),
    ("Small office\nChicago", {"reheat": 0.0, "fans": 16.6},
     [("supply air reset", "no handle here", MUTED), ("fan availability", "tried: +2.14%", BAD)],
     "DEPLOYS\nNOTHING", BAD),
    ("Medium office\nNew Delhi", {"reheat": 1.2, "fans": 3.2},
     [("supply air reset", "reheat only 1.2%", MUTED), ("fan availability", "tried: +1.11%", BAD)],
     "DEPLOYS\nNOTHING", BAD),
]
for ax, (name, uses, fits, verdict, vcolor) in zip(axes, cases, strict=True):
    ax.axis("off")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.text(0.2, 9.4, name, fontsize=13, fontweight="bold", color=INK, va="top")
    ax.text(0.2, 7.5, f"electric reheat  {uses['reheat']:.1f}%\nfans  {uses['fans']:.1f}%",
            fontsize=11, color=MUTED, va="top", linespacing=1.6)
    y = 5.4
    for measure, outcome, colour in fits:
        ax.text(0.2, y, measure, fontsize=10.5, color=INK, va="top")
        ax.text(0.2, y - 0.85, outcome, fontsize=10.5, color=colour, va="top", style="italic")
        y -= 2.0
    ax.add_patch(FancyBboxPatch((0.1, 0.1), 9.4, 1.35, boxstyle="round,pad=0.08",
                                facecolor=vcolor, edgecolor="none", alpha=0.12))
    ax.text(4.8, 0.78, verdict, fontsize=12, fontweight="bold", color=vcolor,
            ha="center", va="center", linespacing=1.4)
fig.tight_layout()
fig.savefig(OUT / "commission.png", dpi=200, bbox_inches="tight", facecolor="white")
plt.close(fig)


# ---------------------------------------------------------- architecture
fig, ax = plt.subplots(figsize=(11.5, 4.3))
ax.set_xlim(0, 100)
ax.set_ylim(4, 51.6)
ax.axis("off")

def band(y, h, colour, title, body, alpha=0.13):
    ax.add_patch(FancyBboxPatch((2, y), 96, h, boxstyle="round,pad=0.3",
                                facecolor=colour, edgecolor=colour, alpha=alpha, linewidth=1.4))
    ax.text(4.2, y + h - 1.6, title, fontsize=11.5, fontweight="bold", color=INK, va="top")
    ax.text(4.2, y + h - 4.4, body, fontsize=10, color="#33415C", va="top", linespacing=1.55)

band(41, 10, BLUE, "OPEN-SOURCE LLM   Qwen2.5-3B via Ollama, local",
     "sets one number a day: the supply air ceiling, from a degraded 24 h forecast\n"
     "schema-constrained JSON  ·  368 decisions/yr  ·  0 failed  ·  median 7.6 s")
band(29, 10, NAVY, "MCP SERVER   13 tools, stdio + streamable HTTP",
     "one capability layer, two surfaces: the agent calls it in-process, external clients over MCP\n"
     "evaluate_policy lets any client close the loop without touching this codebase")
band(17, 10, GOOD, "DETERMINISTIC CORE   runs every timestep, no inference",
     "Guardian clamps every policy into a comfort band the controller cannot move\n"
     "Guideline 36 supply-air reset  ·  commissioning by trial  ·  self-repair of broken models")
band(5, 10, "#8A6D3B", "ENERGYPLUS 26.1   live instance, runtime Python API",
     "2 callbacks per timestep: read state and write actuators in, record telemetry out\n"
     "52,560 timesteps a year in 14 s  ·  epJSON mutation  ·  paired baseline on one clock")

for y in (39.6, 27.6, 15.6):
    ax.add_patch(FancyArrowPatch((28, y + 1.6), (28, y - 1.1), arrowstyle="-|>",
                                 mutation_scale=15, color="#7C8AA0", linewidth=1.6))
    ax.add_patch(FancyArrowPatch((72, y - 1.1), (72, y + 1.6), arrowstyle="-|>",
                                 mutation_scale=15, color="#7C8AA0", linewidth=1.6))
ax.text(26.4, 28.0, "policy down", fontsize=9, color=MUTED, ha="right", va="center", style="italic")
ax.text(73.6, 28.0, "telemetry up", fontsize=9, color=MUTED, ha="left", va="center", style="italic")

fig.tight_layout()
fig.savefig(OUT / "architecture.png", dpi=200, bbox_inches="tight", facecolor="white")
plt.close(fig)

print("wrote", *(p.name for p in sorted(OUT.glob("*.png"))))


# ------------------------------------------------- wide ladder + terminal snap
fig, (ax, ax2) = plt.subplots(1, 2, figsize=(13.2, 2.9), gridspec_kw={"width_ratios": [1.6, 1]})
bars = ax.barh(range(4), saving, color=colors, height=0.6)
ax.set_yticks(range(4))
ax.set_yticklabels([labels[a].replace("\n", " ") for a in order], fontsize=10.5)
for i, value in enumerate(saving):
    ax.text(value + 0.1, i, f"{value:.2f}%", va="center", fontsize=11.5,
            fontweight="bold", color=colors[i])
ax.set_xlim(0, 6.8)
ax.set_xlabel("electricity saved vs untouched building, full year", fontsize=9.5, color=MUTED)
ax.set_title("Energy", fontsize=12, fontweight="bold", color=INK, loc="left")
ax.spines[["top", "right", "left"]].set_visible(False)
ax.tick_params(axis="y", length=0)
ax.xaxis.grid(True, color="#E3E8EF")
ax.set_axisbelow(True)

ax2.barh(range(4), comfort, height=0.6,
         color=[BAD if v > 30 else (GOOD if v <= 0 else BLUE) for v in comfort])
ax2.set_yticks(range(4)); ax2.set_yticklabels([])
for i, value in enumerate(comfort):
    ax2.text(value + (20 if value > 0 else -20), i, f"{value:+.0f}", va="center",
             ha="left" if value > 0 else "right", fontsize=11.5, fontweight="bold")
ax2.axvline(0, color="#9AA5B1", linewidth=1)
ax2.set_xlim(-130, 820)
ax2.set_xlabel("comfort cost, degree-hours (lower is better)", fontsize=9.5, color=MUTED)
ax2.set_title("Comfort", fontsize=12, fontweight="bold", color=INK, loc="left")
ax2.spines[["top", "right", "left"]].set_visible(False)
ax2.tick_params(axis="y", length=0)
ax2.xaxis.grid(True, color="#E3E8EF")
ax2.set_axisbelow(True)
fig.tight_layout()
fig.savefig(OUT / "ladder_wide.png", dpi=200, bbox_inches="tight", facecolor="white")
plt.close(fig)

term = Path(__file__).parent / "term.txt"
lines = ["$ ecoloop commission --model RefBldgSmallOfficeNew2004_Chicago.idf"]
lines += [ln.rstrip() for ln in term.read_text().splitlines() if ln.strip()]
fig, ax = plt.subplots(figsize=(13.2, 2.1))
fig.patch.set_facecolor("#12161C")
ax.set_facecolor("#12161C")
ax.axis("off")
y = 0.93
for ln in lines:
    colour, weight = "#C9D4E3", "normal"
    if ln.startswith("$"):
        colour, weight = "#7FD1A6", "bold"
    elif "deploys nothing" in ln:
        colour, weight = "#F08A80", "bold"
    elif "+2.14%" in ln or "no heating handle" in ln:
        colour = "#E8C07D"
    ax.text(0.018, y, ln or " ", transform=ax.transAxes, fontsize=11.5, color=colour,
            family="monospace", va="top", fontweight=weight)
    y -= 0.175
fig.savefig(OUT / "terminal.png", dpi=200, bbox_inches="tight", facecolor="#12161C")
plt.close(fig)
print("wrote ladder_wide.png terminal.png")
