# Eco-Loop

A self-commissioning supervisory controller for building HVAC, built against EnergyPlus.

Point it at a building model it has never seen. It discovers the model's sensors and
actuators, derives a control policy, runs that policy in closed loop against the live
simulation, and proves the savings against a lockstep baseline twin running the same weather.

The premise: advanced supervisory control (optimal start, demand limiting, supply-air reset)
is a solved problem that reaches under 5% of buildings, because every deployment costs weeks
of engineering time to map points, build a model and tune a strategy. The LLM's job here is to
remove that cost — not to replace the controller.

**Status: Phase 5 complete.** A deterministic supervisory controller runs in closed loop and
saves 5.3% of annual electricity and 8.7% of peak with comfort held. The capability layer is
exposed over MCP. A local model now supervises that controller — and measurably does not
improve it, for a reason worth reading: the task has no headroom left. See
[the model section](#the-model-and-what-it-turned-out-to-be-worth). Pointed at a building it
was not built for, it now refuses to deploy rather than silently doing nothing, and it
repairs a model EnergyPlus will not run.

## Requirements

- Python 3.11+
- macOS (arm64/x86_64) or Linux (x86_64/arm64)
- ~1 GB disk for EnergyPlus
- [Ollama](https://ollama.com) with `qwen2.5:3b-instruct`, for the agent arm only. Everything
  else runs without it, and the agent arm degrades to the deterministic controller rather
  than failing. Override with `ECOLOOP_LLM_MODEL` and `ECOLOOP_LLM_HOST`.

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

```bash
ollama pull qwen2.5:3b-instruct
```

Only needed for `make agent`.

## Commands

| Command | Purpose |
| --- | --- |
| `make setup` | Install EnergyPlus and the Python environment |
| `make info` | Report the EnergyPlus install and a model's conditioned zones |
| `make smoke` | 3-day baseline run (~1 s) |
| `make baseline` | Annual baseline run (~14 s) |
| `make compare` | All three arms over a full year (~35 s) |
| `make agent` | Model-supervised arm against the deterministic one |
| `ecoloop commission --model <idf>` | Which measures earn their place on a building |
| `ecoloop decisions agent` | What the model decided, and what each call cost |
| `make serve` | Serve the capability layer over MCP on stdio |
| `make inspector` | Open the MCP Inspector against the server |
| `make test` | Test suite, including real simulations |
| `make lint` | ruff check and format |

`ecoloop run` and `ecoloop compare` take `--model`, `--weather`, `--period` and
`--timesteps-per-hour`. `--period` accepts a named window (`smoke`, `summer`, `winter`,
`shoulder`, `annual`) or an explicit `MM-DD:MM-DD` range. `--weather` accepts `chicago`,
`delhi`, or a path to any EPW. `ecoloop compare` additionally takes `--arms`, chosen from
`baseline`, `deadband`, `supervisor` and `agent`.

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
| agent, model-supervised | 756,427 (−1.50%) | 340.4 (−1.31%) | 55.0 | 509 | 1,033 (+0.3%) |

The supervisor resets supply air temperature and never touches a thermostat. It returns six
times the energy of the naive deadband arm for a hundredth of the comfort cost, and lowers
unmet hours below baseline while doing it.

Two findings shaped that controller, and both were measured rather than assumed. The
correction has to be driven by the single worst zone — two core zones caused 115 of the 128
degree-hours of shoulder-season damage against ≤1.8 for any perimeter zone. And it has to be
clamped against integral windup, which otherwise turned an 11.02% saving into 0.17%.

What it cannot do is also recorded. Strict comfort non-degradation is not reachable at all
here — the energy-comfort frontier is mapped in
[ADR 0005](docs/adr/0005-supply-air-reset-as-the-first-measure.md), and Phase 4 showed that
anticipating the shoulder-season afternoon does not move it either. And run unchanged on New
Delhi weather the same controller returns 0.59% over a year, against 5.33% in Chicago,
because Delhi sits above its outdoor ceiling almost all year. Choosing the right measure per
building and per climate is the engineering cost the agent exists to remove.

## An unfamiliar building

Everything above is one building. Pointed at the small office — five packaged single-zone
units instead of three VAV loops — the supervisor ran 336 policy decisions over a week,
changed nothing, exited zero and reported success. There is no central supply air schedule to
actuate, so the measure had nothing to act on. Energy matched the baseline to the last digit.

Commissioning makes that a refusal instead. It discovers what can be actuated, surveys where
the energy goes over an untouched year, and then **tries** each candidate against that
baseline:

| | medium office | small office |
| --- | --- | --- |
| electric reheat | 18.5% | 0.0% |
| fans | 2.6% | 16.6% |
| `supply_air_reset` | tried: −5.33%, +7.0 K·h → **deployed** | no handle in this model |
| `hvac_availability` | fans below the 3% worth touching | tried: **+2.14%** → rejected |
| outcome | supply air reset | **deploys nothing** |

The third row is the point. Fans dominate the small office, its availability schedule is
actuable, and running the fans only when occupied still made the building worse — fan energy
alone rose 12.5%. Releasing the schedule hands the system to its
`AvailabilityManager:NightCycle`, which cycles on any zone 1 K off setpoint for a fixed 30
minutes, and that cycles harder than the schedule it replaced. Neither the load breakdown nor
the control surface predicts that; it is a property of control objects already in the model.
Only a trial catches it. See [ADR 0008](docs/adr/0008-commissioning-by-trial.md).

So the system deploys nothing on that building and says why. Refusing is the correct outcome,
and a framework that deployed the fan measure because it was applicable and aimed at the
dominant load would have made things worse and reported success.

## Repairing a model that will not run

One mistyped schedule reference produces eleven severe errors, one fatal, and no simulation.
Those resolve to a single fault — object type, field, and the value that could not be
resolved — and the repair loop proposes the nearest existing name, repoints every object
carrying it, writes the patched model out as both epJSON and IDF, and runs again.

```
11 severe -> 1 fault: ThermostatSetpoint:DualSetpoint.cooling_setpoint_temperature_schedule_name
   'CLGSETP_SCH_TYPO' -> 'CLGSETP_SCH'   (5 objects)
   wrote repaired_v1.idf, run completed
```

A name with no close match is not guessed at: the loop gives up and says what it could not
resolve, rather than retrying forever.

## The model, and what it turned out to be worth

A local model (Qwen2.5 via Ollama) sets one number: the supply air ceiling for the next 24
hours, chosen from a day-ahead forecast. The Guideline 36 loop keeps running underneath it.
The forecast is deliberately degraded with error that grows with lead time, because the
weather file is perfect foresight and reading it straight out would flatter anything that
depends on planning ahead.

It does not beat the deterministic controller. Over a full year it returns −1.50% against the
supervisor's −5.33%, for a comfort figure 3.7 K·h better out of 1,030. Per season:

| period | deterministic | agent, Qwen2.5 3B |
| --- | --- | --- |
| winter | **−13.25%**, 21.7 K·h | −6.05%, 22.2 K·h |
| shoulder | **−1.43%**, 17.4 K·h | −0.03%, 17.3 K·h |
| summer | 0.00%, 25.7 K·h | 0.00%, 25.7 K·h |

Before concluding the model is at fault, we bounded the task. A perfect-discrimination
supervisor — the same outdoor curve applied to the *forecast peak* rather than the current
reading, which is exactly the anticipation the model was asked for — returns −13.24%, −1.40%
and 0.00%. That matches the deterministic controller to within 0.03 percentage points.

**There is no anticipation headroom left on this measure.** The gap Phase 2 identified was
closed by Phase 2 itself, when the correction was retuned to respond to the worst zone's
excursion. That reacts inside a single 30-minute decision, which is fast enough that seeing
the afternoon coming adds nothing. The ceiling on this task is a tie, and a model can only
lose.

That is invisible without a deterministic bar to measure against. Compared only to the stock
building, this agent saves 6% and looks like a success.

Neither local model performs the task reliably in any case. The 3B answers 18.0 °C — the
warmest available deck — for a day forecast to reach 34 °C, and three prompt revisions did not
fix it. The 7B gets that case right and then asks for 13.5 °C on a day that never rises above
3 °C, forfeiting the reheat saving; it is also three times slower here, ~16 s a call against
~5 s, measured with the other model unloaded.

The recorded reasoning shows why, and it is not a formatting problem. On 1 January the 3B
wrote that it was *"a cold day with temperatures dropping below freezing, so … the supply air
temperature should be set at the minimum allowable value of 12.8 °C"* — the correct premise
and the inverted conclusion. It does vary its answer across the year, using 32 distinct
ceilings over 368 decisions, but the variation does not track what the building needs. Full
detail in [ADR 0007](docs/adr/0007-what-the-model-supervises.md).

So the model is bounded to *lowering* the ceiling, never raising it — the same principle as
the guardian one layer down: do not rely on the model being right, make being wrong harmless.
With that bound the summer arm is byte-identical to the untouched baseline.

Every call is recorded whole — prompt hash, plan, latency, retries, any error — and cached by
prompt, so a completed run replays from disk without a model. The annual agent arm takes
2,898 s to run live and **28 s to replay**, reproducing every KPI exactly. Two things are
needed for that exactness, and the first was found the hard way. Ollama picks a fresh sampler seed
per call unless told otherwise, so identical prompts returned different ceilings, each answer
fed the next prompt through the scorecard, and the trajectory diverged; the seed is now
pinned. The second is that failed calls are deliberately *not* cached, so a run that hit a
transient outage will not replay identically, which is the honest behaviour rather than baking
an outage into the record.

Nothing here can stop the simulation. A slow, unreachable or malformed response returns a
decision carrying an error instead of a plan, the previous ceiling stays in force, and with no
plan at all the deterministic curve drives the building. `tests/test_llm.py` kills the model
partway through a run and asserts the run finishes and still saves energy.

None of this contradicts the premise at the top of this file; it is the first hard evidence
for it. Tuning a measure an engineer has already tuned has no room in it. The measures this
building has never had — optimal start against its February morning peak, terminal minimum
flow, economiser operation — are worth more than perfecting the one it has, and choosing
between them for an unfamiliar building is the judgement a curve cannot make.

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
| `commission_model` | which measures a building can take, each verified by trial |
| `run_kpis`, `run_errors` | headline numbers, and warning/severe/fatal counts |
| `run_decisions` | every plan the model was asked for, with latency and any failure |
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
  commissioning.py  discover, survey and trial measures on an unfamiliar building
  repair.py      turn a model EnergyPlus refuses into one it will run
  llm.py         the local model client, and the record of everything it was asked
  tools.py       the capability layer: what anything reasoning about the building may do
  mcp_server.py  MCP registration over tools.py, and nothing else
  eplus.py       locate the pinned EnergyPlus install, expose its Python API
  model.py       epJSON load/save/mutate; IDF conversion; control surface discovery
  runner.py      run a simulation under optional control, record per-timestep telemetry
  control.py     what a controller sees, what it may command, how it is written
  policy.py      the control plan, the guardian that bounds it, how it becomes commands
  digest.py      the fixed-size situation report a policy author reasons over
  strategies.py  policy authors, deterministic and model-supervised
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
| 4 | Model supervision, guarded and measured | done |
| 5 | Self-commissioning onto unseen models, fault injection | next |
| 6 | Ablation ladder, dashboard, report | |
| 7 | Packaging and delivery | |
