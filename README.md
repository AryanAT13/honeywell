# Eco-Loop

A self-commissioning supervisory controller for building HVAC, built against EnergyPlus.

Point it at a building model it has never seen. It discovers the model's sensors and
actuators, derives a control policy, runs that policy in closed loop against the live
simulation, and proves the savings against a lockstep baseline twin running the same weather.

The premise: advanced supervisory control (optimal start, demand limiting, supply-air reset)
is a solved problem that reaches under 5% of buildings, because every deployment costs weeks
of engineering time to map points, build a model and tune a strategy. The LLM's job here is to
remove that cost — not to replace the controller.

**Status: Phase 1 complete.** The loop is closed — a controller reads zone state and writes
thermostat setpoints into the running instance every timestep, and arms are compared against
a paired baseline on an identical clock. The controller is a fixed rule; no agent yet.

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
| `make compare` | Baseline against a controlled arm over a summer week |
| `make test` | Test suite, including real simulations |
| `make lint` | ruff check and format |

`ecoloop run` takes `--model`, `--weather`, `--period` and `--timesteps-per-hour`.
`--period` accepts a named window (`smoke`, `summer`, `winter`, `shoulder`, `annual`)
or an explicit `MM-DD:MM-DD` range. `ecoloop compare` additionally takes `--arms`.

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

The difference is not theoretical. Widening the occupied deadband by 0.5 K over a summer week:

| | kWh | peak kW | unmet h | outside h | degree-hours |
| --- | --- | --- | --- | --- | --- |
| baseline | 17,108.7 | 219.4 | 6.00 | 10.83 | 25.7 |
| +0.5 K deadband | 16,853.8 (−1.49%) | 212.9 (−2.95%) | 4.50 | 99.00 | 55.3 |

Unmet hours *improved*. Occupants were worse off for twice as long. This is the trade the
brief is asking whether the agent makes, and it is why the Phase 2 guardian clamps against
the fixed band. See [ADR 0004](docs/adr/0004-comfort-scored-against-a-fixed-band.md).

It is also the second piece of evidence that deadband widening is the wrong lever here: 1.5%
energy for double the discomfort, against reheat at 18.5% of building electricity.

## Layout

```
ecoloop/
  eplus.py       locate the pinned EnergyPlus install, expose its Python API
  model.py       epJSON load/save/mutate; IDF conversion; control surface discovery
  runner.py      run a simulation under optional control, record per-timestep telemetry
  control.py     what a controller sees, what it may command, how it is written
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

Arms run in separate processes and are aligned on their shared time index, which is asserted
to be identical. Since arms never interact, that gives the property a lockstep comparison
needs; running them concurrently would only change the wall clock.

Provenance: the baseline models and weather file are copied unmodified from the EnergyPlus
26.1.0 distribution (`ExampleFiles/`, `WeatherData/`) and are committed so runs are
reproducible without a matching local install.

## Roadmap

| Phase | | |
| --- | --- | --- |
| 0 | Simulation harness, telemetry, KPIs, CI | done |
| 1 | Closed loop: live actuator writes, paired baseline twin | done |
| 2 | Deterministic controller, safety guardian, state digest | next |
| 3 | MCP server | |
| 4 | LLM cognition: strategy, reflection, self-repair | |
| 5 | Self-commissioning onto unseen models, fault injection | |
| 6 | Ablation ladder, dashboard, report | |
| 7 | Packaging and delivery | |
