# Eco-Loop

A self-commissioning supervisory controller for building HVAC, built against EnergyPlus.

Point it at a building model it has never seen. It discovers the model's sensors and
actuators, derives a control policy, runs that policy in closed loop against the live
simulation, and proves the savings against a lockstep baseline twin running the same weather.

The premise: advanced supervisory control (optimal start, demand limiting, supply-air reset)
is a solved problem that reaches under 5% of buildings, because every deployment costs weeks
of engineering time to map points, build a model and tune a strategy. The LLM's job here is to
remove that cost — not to replace the controller.

**Status: Phase 3 complete.** A deterministic supervisory controller runs in closed loop
against the live simulation and saves 5.3% of annual electricity and 8.7% of peak with
comfort held, and the whole capability layer is exposed over MCP, so an external client can
propose a policy and get it scored. No LLM yet — this is the bar the agent has to beat.

## Requirements

- Python 3.11+
- macOS (arm64/x86_64) or Linux (x86_64/arm64)
- ~1 GB disk for EnergyPlus

## Quick start

```bash
make setup
```

Installs EnergyPlus 26.1.0 to `~/opt/EnergyPlus-26-1-0` (no sudo) and creates a virtualenv.
Set `ECOLOOP_EPLUS_DIR` if you already have that version installed elsewhere.

```bash
make smoke
```

Runs a 3-day simulation of the baseline model and prints its KPIs.

## Commands

| Command | Purpose |
| --- | --- |
| `make setup` | Install EnergyPlus and the Python environment |
| `make info` | Report the EnergyPlus install and a model's conditioned zones |
| `make smoke` | 3-day baseline run (~1 s) |
| `make baseline` | Annual baseline run (~14 s) |
| `make compare` | All three arms over a full year (~35 s) |
| `make serve` | Serve the capability layer over MCP on stdio |
| `make inspector` | Open the MCP Inspector against the server |
| `make test` | Test suite, including real simulations |
| `make lint` | ruff check and format |

`ecoloop run` and `ecoloop compare` take `--model`, `--weather`, `--period` and
`--timesteps-per-hour`. `--period` accepts a named window (`smoke`, `summer`, `winter`,
`shoulder`, `annual`) or an explicit `MM-DD:MM-DD` range. `--weather` accepts `chicago`,
`delhi`, or a path to any EPW. `ecoloop compare` additionally takes `--arms`, chosen from
`baseline`, `deadband` and `supervisor`.

## Baseline

DOE reference medium office, 4,982 m², 15 conditioned zones over 3 floors, VAV with reheat.
Chicago O'Hare TMY3. Measured over a full year at a 10-minute timestep:

| | |
| --- | --- |
| Electricity | 767,959 kWh (154.1 kWh/m²) |
| Natural gas | 60,361 kWh |
| Peak demand | 344.9 kW, 06:12 on 6 February |
| Unmet occupied hours | 56.0 (ASHRAE 90.1 allows 300) |
| Wall clock | 13.5 s |

Two facts from this baseline shape the control strategy. The annual peak is a winter
morning warm-up spike, which is what optimal start exists to remove. And electric terminal
reheat is 18.5% of building electricity — larger than cooling — so supply-air temperature
reset and VAV minimum-flow reduction are the largest addressable loads, ahead of any
thermostat deadband change.

The baseline already meets its setpoints, so 56 unmet hours is a ceiling the agent must not
exceed, not a budget to spend.

## Measuring comfort

Comfort is reported two ways, because the obvious metric is one the controller can move.

*Unmet hours* are occupied time outside the thermostat's throttling range — measured against
whatever setpoint is currently commanded. It answers whether the plant delivered what it was
asked for, and a controller that widens its own deadband improves its own score.

*Comfort excursion* is occupied time outside a fixed 21–24 °C band taken from the baseline's
occupied setpoints, reported as hours and as degree-hours. The controller cannot move it.

The failure mode is not subtle: a controller holding a zone at 29 °C while commanding a 30 °C
setpoint reports *zero* unmet hours. `tests/test_kpi.py` pins that down.

Every policy therefore passes a guardian that clamps occupied setpoints back inside the
contract before they reach the instance, so widening the band is not available as a way to
buy energy. See [ADR 0004](docs/adr/0004-comfort-scored-against-a-fixed-band.md).

## Results

Full year, Chicago, against the stock model on an identical clock:

| arm | kWh | peak kW | unmet h | outside band h | degree-hours |
| --- | --- | --- | --- | --- | --- |
| baseline | 767,959 | 344.9 | 56.0 | 507 | 1,030 |
| deadband, unguarded | 761,115 (−0.89%) | 340.4 (−1.31%) | 231.3 | 4,891 | 1,715 (+67%) |
| **supervisor** | **727,003 (−5.33%)** | **315.1 (−8.66%)** | **51.5** | 514 | 1,037 (+0.7%) |

The supervisor resets supply air temperature and never touches a thermostat. It returns six
times the energy of the naive deadband arm for a hundredth of the comfort cost, and lowers
unmet hours below baseline while doing it.

Two findings shaped that controller, and both were measured rather than assumed. The
correction has to be driven by the single worst zone — two core zones caused 115 of the 128
degree-hours of shoulder-season damage against ≤1.8 for any perimeter zone. And it has to be
clamped against integral windup, which otherwise turned an 11.02% saving into 0.17%.

What it cannot do is also recorded. Strict comfort non-degradation is unreachable reactively,
because shoulder-season core overheating is driven by gains that are predictable hours ahead;
the energy-comfort frontier is mapped in
[ADR 0005](docs/adr/0005-supply-air-reset-as-the-first-measure.md). And run unchanged on New
Delhi weather the same controller returns 0.59% over a year, against 5.33% in Chicago,
because Delhi sits above its outdoor ceiling almost all year. Choosing the right measure per
building and per climate is the engineering cost the agent exists to remove.

## MCP interface

Capabilities are defined once in `ecoloop/tools.py` as plain functions over pydantic models,
with no MCP import. The in-process agent calls them directly — routing per-decision calls
back out over stdio would add IPC latency to the run's bottleneck and cross no trust
boundary. `ecoloop/mcp_server.py` registers the same functions for external clients, so the
protocol surface cannot drift from what the agent uses.
See [ADR 0006](docs/adr/0006-one-capability-layer-two-surfaces.md).

| tool | |
| --- | --- |
| `list_models`, `list_climates`, `list_runs` | what is available |
| `inspect_model` | a model's control surface: conditioned zones, schedules, air loops |
| `run_kpis`, `run_errors` | headline numbers, and warning/severe/fatal counts |
| `telemetry` | a downsampled window; the full frame is never returned |
| `state_digest` | the situation report as a policy author saw it at a given moment |
| `check_policy` | project a policy onto the safe envelope without running anything |
| `evaluate_policy` | run a policy against the simulation and score it against the baseline |
| `compare_arms` | run named arms over identical weather and compare pairwise |

`evaluate_policy` is what makes the server more than an inspection panel: an external model
can propose, run, read the score and propose again without touching this codebase. Baselines
are cached per model, weather and window, so an evaluation costs one simulation — 1–2 s for a
week, 14 s for a year.

Both transports work. `ecoloop serve` speaks stdio; `ecoloop serve --http` speaks streamable
HTTP on port 8000. To attach a desktop client, add to its MCP config:

```json
{
  "mcpServers": {
    "ecoloop": {
      "command": "/absolute/path/to/repo/.venv/bin/ecoloop",
      "args": ["serve"]
    }
  }
}
```

## Layout

```
ecoloop/
  tools.py       the capability layer: what anything reasoning about the building may do
  mcp_server.py  MCP registration over tools.py, and nothing else
  eplus.py       locate the pinned EnergyPlus install, expose its Python API
  model.py       epJSON load/save/mutate; IDF conversion; control surface discovery
  runner.py      run a simulation under optional control, record per-timestep telemetry
  control.py     what a controller sees, what it may command, how it is written
  policy.py      the control plan, the guardian that bounds it, how it becomes commands
  digest.py      the fixed-size situation report a policy author reasons over
  strategies.py  policy authors; the LLM becomes another one in Phase 4
  experiment.py  run arms over identical weather and compare them pairwise
  kpi.py         telemetry frame -> comparable headline numbers
  errors.py      structured reading of eplusout.err
  contracts.py   pydantic types spoken at every module boundary
  config.py      repository paths and pinned defaults
  cli.py         command line entry point
models/          baseline building models
weather/         EPW weather files
runs/            simulation outputs (regenerable, not committed)
docs/adr/        architecture decision records
```

## Notes

Telemetry is captured through the EnergyPlus runtime API rather than by parsing output
files. Three constraints of that API are handled explicitly in `runner.py`, and each one
produces silently wrong results rather than an error if you miss it:

- Output variables must be requested before the run starts.
- Handles are only valid once `api_data_fully_ready()` returns true.
- Warmup timesteps and sizing-period environments must both be discarded. Filtering on
  `warmup_flag` alone is not enough; design days are separate environments and are
  excluded via `kind_of_sim`.

Two more that are silent rather than loud:

- `minutes()` and `current_time()` report the *system* timestep, which subdivides adaptively
  when the HVAC struggles to converge. Timestamps built from them are irregular and differ
  between arms on the same clock, which breaks any paired comparison. `zone_time_step_number`
  is the uniform one.
- `Electricity:Facility` has no API handle in 26.1 despite being listed as available. Meters
  resolve from a candidate list, and a missing required meter raises rather than reporting
  zero.

Setpoint overrides are released explicitly when a controller declines to command a zone,
rather than relying on EnergyPlus to revert them.

Nothing may be written to stdout while serving MCP over stdio, since that stream carries the
protocol. EnergyPlus console output is disabled on every run for this reason among others.

Arms run in separate processes and are aligned on their shared time index, which is asserted
to be identical. Since arms never interact, that gives the property a lockstep comparison
needs; running them concurrently would only change the wall clock.

Supply air temperature is reset by overriding the schedule the setpoint managers read, not
the supply node. Mixed air managers derive the cooling coil's setpoint from the supply node
within the same timestep, so overriding that node afterwards moves the reported setpoint and
changes nothing else — an override that looks like it worked and does not.

Provenance: the baseline models and the Chicago weather file are copied unmodified from the
EnergyPlus 26.1.0 distribution (`ExampleFiles/`, `WeatherData/`) and are committed so runs
are reproducible without a matching local install. The New Delhi file is ISHRAE 2014 data
from climate.onebuilding.org; its licence is committed alongside it.

## Roadmap

| Phase | | |
| --- | --- | --- |
| 0 | Simulation harness, telemetry, KPIs, CI | done |
| 1 | Closed loop: live actuator writes, paired baseline twin | done |
| 2 | Deterministic controller, safety guardian, state digest | done |
| 3 | MCP server | done |
| 4 | LLM cognition: strategy, reflection, self-repair | next |
| 5 | Self-commissioning onto unseen models, fault injection | |
| 6 | Ablation ladder, dashboard, report | |
| 7 | Packaging and delivery | |
