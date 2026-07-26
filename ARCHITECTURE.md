# Eco-Loop — system architecture

How the closed loop is built, what the model is asked to do, and what it costs. Every figure
here is produced by `make evidence` and published in
[docs/evidence/report.html](docs/evidence/report.html).

Contents: [the loop](#1-the-closed-loop) · [tool-calling](#2-tool-calling-architecture) ·
[prompt engineering](#3-prompt-engineering) · [latency](#4-prompt-latency-management) ·
[logs](#5-handling-lengthy-simulation-logs) · [safety](#6-safety-and-the-guardian) ·
[commissioning](#7-commissioning-and-self-repair) ·
[evaluation](#8-evaluation-methodology) · [limits](#9-what-this-does-not-do)

---

## 0. Shape of the system

```
                    ┌──────────────── external MCP client ────────────────┐
                    │  Inspector · Claude Desktop · anything speaking MCP  │
                    └──────────────────────┬───────────────────────────────┘
                                           │  stdio / streamable HTTP
                            ┌──────────────┴──────────────┐
                            │   mcp_server.py             │  registration only
                            └──────────────┬──────────────┘
                                           │
   in-process agent ───────────────►  tools.py  ◄──── 13 capabilities, one implementation
                                           │
   ┌───────────────────────────────────────┴────────────────────────────────────┐
   │  commissioning.py   discover → survey → trial → select, or refuse          │
   │  strategies.py      policy authors: deterministic, model-supervised        │
   │  llm.py             local model, schema-constrained, journalled            │
   │  policy.py          Policy · Guardian · per-timestep application           │
   │  digest.py          fixed-size situation report                            │
   │  repair.py          errors → faults → patched model → rerun                │
   └───────────────────────────────────────┬────────────────────────────────────┘
                                           │
                            ┌──────────────┴──────────────┐
                            │  runner.py — EnergyPlus API │
                            └──────────────┬──────────────┘
                             read + actuate │ record
                            ┌──────────────┴──────────────┐
                            │  live EnergyPlus instance   │
                            └─────────────────────────────┘
```

`Policy` is the contract between everything above the line and everything below it. The
deterministic author and the model-supervised author emit the same object; only the author
changes.

---

## 1. The closed loop

Control is applied to a **running** EnergyPlus instance through the Python API, not by
editing an IDF and running again. Two callbacks per timestep:

| calling point | what happens |
| --- | --- |
| `begin_zone_timestep_after_init_heat_balance` | read zone state, decide if a decision is due, write thermostat and schedule actuators |
| `end_zone_timestep_after_zone_reporting` | record telemetry for the completed timestep |

An annual run is 52,560 timesteps at a 10-minute step and completes in **14 seconds** with
full telemetry capture.

### Four API constraints that fail silently

Each of these produces wrong numbers with a zero exit code rather than an error. All four are
handled in one place, `runner.py`, and covered by a test that reconciles our energy totals
against EnergyPlus's own tabular report.

1. **Variables must be requested before the run starts.** Not requested, not readable.
2. **Handles are invalid until `api_data_fully_ready()`.** Fetch once, cache, never per
   timestep.
3. **Warmup *and* sizing periods must both be discarded.** Filtering `warmup_flag` alone is
   not enough — design days are separate environments, excluded via `kind_of_sim`. Our first
   run reported "48 hours" for a 3-day window because it had simulated two design days and
   never touched the weather file.
4. **`minutes()` and `current_time()` report the *system* timestep**, which subdivides
   adaptively under HVAC convergence pressure. Timestamps built from them are irregular *and
   differ between arms on the same clock*, which silently breaks any paired comparison.
   `zone_time_step_number` is the uniform one.

Two more, found the same way:

- `Electricity:Facility` has no API handle in EnergyPlus 26.1 despite being listed as
  available. Meters resolve from a candidate list and a missing required meter raises rather
  than reporting zero.
- **Supply air temperature must be reset through the schedule the setpoint managers read,
  not the supply node.** `SetpointManager:MixedAir` derives the cooling coil's setpoint from
  the supply node earlier in the same timestep, so overriding that node moves the reported
  setpoint and changes nothing else. Energy came back byte-identical to baseline — an
  override that looks like it worked.

### Model mutation

Models are converted to **epJSON** on entry and mutated as plain dictionaries: diffable,
schema-validatable before a run, no IDF parsing dependency. IDF is emitted on the way out
because the deliverables ask for it. See [ADR 0002](docs/adr/0002-epjson-as-mutation-substrate.md).

---

## 2. Tool-calling architecture

**One capability layer, two surfaces.** All 13 capabilities live in `ecoloop/tools.py` as
plain functions over Pydantic models, with no MCP import anywhere in the module.
`ecoloop/mcp_server.py` is registration and nothing else.

```python
for capability in (tools.list_models, tools.inspect_model, tools.commission_model, ...):
    server.tool()(capability)
```

The in-process agent calls those functions **directly**. Routing each decision out over stdio
and back would add serialisation and IPC latency to the run's bottleneck and cross no trust
boundary — there is no second machine and nothing to isolate. Equally, an MCP server exposing
only a subset of what the agent can do is a demo prop rather than an interface. Defining the
capabilities once means the protocol surface cannot drift from what the agent actually uses.
See [ADR 0006](docs/adr/0006-one-capability-layer-two-surfaces.md).

| tool | purpose |
| --- | --- |
| `list_models`, `list_climates`, `list_runs` | what is available |
| `inspect_model` | control surface: conditioned zones, setpoint schedules, air loops, supply-air schedules |
| `commission_model` | which measures earn their place, each verified by trial |
| `run_kpis`, `run_errors`, `run_decisions` | outcomes, structured faults, the model's decision journal |
| `telemetry` | a downsampled window; the full frame is never returned |
| `state_digest` | the situation report as an author saw it at a given moment |
| `check_policy` | project a policy onto the safe envelope without running anything |
| `evaluate_policy` | run a policy against the simulation and score it against a cached baseline |
| `compare_arms` | run named arms over identical weather and compare pairwise |

**Both transports are verified**: stdio as a subprocess (the desktop-client path), streamable
HTTP on port 8000, and an in-memory client session used by the tests.

`evaluate_policy` is what makes the server more than an inspection panel. An external model
can propose, run, read the score and propose again without touching this codebase. Driven
purely through the protocol over a January week:

| commanded supply air | electricity | peak | comfort | wall clock |
| --- | --- | --- | --- | --- |
| 12.8 °C (= baseline) | **+0.00%** | +0.00% | ±0.0 K·h | 4 s |
| 16.0 °C | −13.91% | −7.72% | −0.8 K·h | 1 s |
| 18.0 °C | −21.32% | −13.49% | −1.7 K·h | 2 s |

Baselines are cached per model, weather and window, so an evaluation costs one simulation.
The 12.8 °C row reproducing baseline to 0.00% is a free correctness check on the harness.

### Structured output, not free text

The model never emits prose that has to be parsed. Ollama is given
`format=Plan.model_json_schema()`, so decoding is constrained to a valid instance of:

```python
class Plan(BaseModel):
    reason: str                  # declared first, deliberately
    supply_air_ceiling: float = Field(ge=12.8, le=18.0)
```

Over 368 annual decisions: **0 failed**, 5 needed the repair retry.

---

## 3. Prompt engineering

### What the model is asked

One number: the supply air temperature ceiling for the next 24 hours. Not per-timestep
setpoints — those belong to the deterministic loop, which is better at them and 40× faster.

The system prompt encodes the physics, the trade-off, the mechanism underneath, and the
specific failure mode, all of which came out of Phase 2 measurement rather than intuition:

> Interior core zones carry lighting and equipment load and need cooling in every season.
> Perimeter zones lose heat outward and need reheat. One supply air temperature serves all of
> them, so the ceiling is a compromise, and the cores are what overheat if you set it too high.
>
> A trim and respond loop runs underneath you every 30 minutes … it can only react once a zone
> is already uncomfortable. Your advantage over it is the forecast.

The user message is the rendered state digest — under 700 characters:

```
Date 2023-10-12 00:10. Outdoor now 9.0 C.
Forecast next 24 h at 3 h steps: 8.0, 11.0, 17.0, 21.0, 19.0, 14.0, 11.0, 9.0
Warmest zone Core_mid at 23.8 C.
Worst occupied excursion now 0.00 K; 0 of 15 zones asking for cooling.
Under the previous ceiling of 16.0 C over 24 h, supply air averaged 15.2 C and the
worst excursion was 0.31 K.
Choose the supply air ceiling for the next 24 hours.
```

### Four things that mattered, in order of surprise

**1. Property order in the schema is a prompt decision.** Constrained decoding emits JSON
properties in schema order. Declaring `supply_air_ceiling` before `reason` made the model
commit to a number before writing a word of justification. Putting `reason` first makes the
reasoning precede the value it explains. Same model, same prompt, better answers.

**2. The seed must be pinned.** Ollama draws a fresh sampler seed per call unless told
otherwise. Because each answer feeds the next prompt through the scorecard, one flipped digit
cascaded into a visibly different year — an annual replay diverged by 0.03%. With
`seed: 0` and `temperature: 0`, five identical prompts return identical ceilings.

**3. Repair beats prevention.** Schema violations are returned to the model with the
validation error and one retry, rather than being prevented by a longer prompt. Five of 368
decisions took the second attempt; none failed outright.

**4. Feedback closes the loop.** Each prompt carries a scorecard of what happened under the
previous plan — the ceiling in force, hours elapsed, mean supply air achieved, worst
excursion. The model is told the consequences of its last decision before making the next.

### And what did not work

Three prompt revisions did not stop the 3B answering **18.0 °C** — the warmest deck
available — for a day forecast to reach 34 °C. The recorded reasoning shows why it is not a
formatting problem. On 1 January it wrote:

> *"a cold day with temperatures dropping below freezing, so … the supply air temperature
> should be set at the minimum allowable value of 12.8 °C"*

Correct premise, inverted conclusion. This is why the model is allowed to **lower** the
ceiling and never raise it above the outdoor-temperature curve — the same principle as the
Guardian one layer down: do not rely on the model being right, make being wrong harmless.

---

## 4. Prompt latency management

An annual simulation is 52,560 control decisions. At 7.6 s per inference that is 111 hours
against 14 seconds for the simulation itself. The architecture is shaped by that ratio.

### Three timescales, one of which involves the model

| loop | period | who | cost |
| --- | --- | --- | --- |
| inner | every timestep (10 min) | deterministic policy execution + Guardian | no inference |
| middle | 30 min | deterministic author re-reads the digest | no inference |
| outer | daily, plus event triggers | **the model** sets the next ceiling | one call |

368 calls over a simulated year: 365 daily plus a handful triggered by comfort excursions.
Median inference **7.6 s**, 47 minutes of inference in total.

### Measured, not assumed

| model | median call | annual arm | notes |
| --- | --- | --- | --- |
| Qwen2.5 3B | ~5 s | ~47 min | default |
| Qwen2.5 7B | ~16 s | ~2 h | better on hot days, worse on cold |

Both measured with the *other model unloaded*. This M1 exposes 5.3 GiB of VRAM; running both
in one session thrashes them and inflates each to nearly a minute. A latency figure taken
while something else was loaded is worth nothing — an earlier measurement of "50–73 s" for
the 7B was entirely this effect.

### Nothing waits on inference

- A slow, unreachable or malformed reply returns a `Decision` carrying an error instead of a
  plan. The previous ceiling stays in force.
- With no plan at all, the deterministic outdoor-temperature curve drives the building.
- `tests/test_llm.py` kills the model partway through a run and asserts the run finishes and
  still saves energy.

### Double buffering was specified, then retired

[ADR 0003](docs/adr/0003-llm-outside-the-inner-loop.md) originally called for computing
horizon *N+1* while *N* executes. Measurement killed it: the simulation takes 14 seconds and
inference takes 47 minutes, so the simulation is never the party that waits. Overlapping them
would save at most 14 seconds. Calling rarely and never depending on the answer arriving is
both cheaper to build and easier to reason about.

### Replay

Every call is journalled and cached by prompt hash. The annual agent arm takes **2,898 s
live and 28 s to replay**, reproducing every KPI exactly. The journal — 390 records — is
committed, so a clean clone reproduces the agent arm with **no model server installed**.
Verified by regenerating all published evidence with `ECOLOOP_LLM_HOST` pointed at a dead
port.

Failed calls are deliberately *not* cached, so a run that hit a transient outage will not
replay identically. That is the honest behaviour rather than baking an outage into the record.

---

## 5. Handling lengthy simulation logs

An annual run produces **52,560 rows × 101 columns**, about 42 MB dense, plus an `.err` file.
None of it reaches the model.

### Telemetry is structured, never textual

Variables come off the API as floats into a frame, not as text to be parsed. The model
receives a **state digest**: a fixed-size situation report assembled from the current state.

| zones | digest size |
| --- | --- |
| 5 | 923 bytes |
| 15 | 2,238 bytes |

**Digest size scales with the number of zones, not with how long the run has been going.** A
3-day run and a 365-day run produce identically sized prompts. One raw telemetry row alone is
~3.9 KB, so the digest for a 15-zone building is smaller than a single unsummarised timestep.

### Errors are resolved to faults, not pasted

`eplusout.err` is parsed into structured records and deduplicated by signature. One mistyped
schedule reference produced:

```
43 log lines → 11 severe + 1 fatal → 1 actionable fault
  ThermostatSetpoint:DualSetpoint.cooling_setpoint_temperature_schedule_name
  = 'CLGSETP_SCH_TYPO', item not found
```

EnergyPlus reports a broken reference once per affected object. The agent sees the one thing
worth acting on.

### Pull, not push

Detail sits behind tools the caller queries when it needs it. `telemetry` defaults to a
whole-building summary — outdoor temperature, demand, supply air setpoint, and zone
temperature min/mean/max — downsampled to a requested point count. A 1,008-timestep window
returns 4 points if 4 are asked for. The full frame is never returned; per-zone columns
require naming them explicitly.

### Hierarchical memory

The scorecard rolls a horizon of timesteps into four numbers carried into the next prompt.
The decision journal keeps every call in full on disk, where it is queryable by
`run_decisions` but never automatically loaded.

---

## 6. Safety and the Guardian

Every policy passes a Guardian before it reaches the instance. Occupied setpoints are clamped
inside a **fixed comfort band** the controller cannot move, deadbands are widened to avoid
simultaneous heating and cooling, setback and supply-air limits are enforced, and every clamp
is counted.

```
proposed: occupied 18.0/29.0 °C, supply air 25.0 °C
accepted: occupied 21.0/24.0 °C, supply air 18.0 °C
clamped : {occupied.heating: 1, occupied.cooling: 1, supply_air: 1}
```

### Why comfort is not scored against the live setpoint

Unmet hours — EnergyPlus's native metric — are measured against whatever setpoint is
currently commanded. A controller that widens its own band improves its own score. A zone
held at 29 °C while commanding a 30 °C setpoint reports **zero unmet hours**;
`tests/test_kpi.py` pins that down.

So comfort is reported twice: unmet hours (did the plant deliver what it was asked for — a
feasibility question) and excursion against a fixed 21–24 °C band taken from the baseline's
own occupied setpoints, as both hours and **degree-hours**, because an hour 0.1 K over the
band and an hour 5 K over are not the same thing. See
[ADR 0004](docs/adr/0004-comfort-scored-against-a-fixed-band.md).

The naive widened-band arm shows the trade the brief asks about: **−0.89% electricity for
+67% comfort degree-hours**, and unmet hours register only a quarter of that relative change.

### One Guardian bug worth naming

`review()` originally **rebuilt** the policy from scratch, so it silently dropped
`hvac_available` — a field it did not know about. The measure was wired correctly end to end
and performed zero actuator writes. Every future policy field would have vanished the same
way. It now copies and updates, and a test pins it.

---

## 7. Commissioning and self-repair

### The deterministic controller is not portable, and fails silently

Pointed at the small office — five packaged single-zone units, no central supply air schedule
— the supervisor ran **336 policy decisions, changed nothing, exited zero and reported
success**. Energy matched baseline to the last digit.

### Discover → survey → trial

1. **Discover** what is actuable from the model's own wiring.
2. **Survey** where the energy goes, from an untouched annual run.
3. **Trial** every candidate that is actuable and aimed at a material load, against that
   baseline, and keep only what actually helps.

| | medium office | small office | medium office, Delhi |
| --- | --- | --- | --- |
| electric reheat | 18.5% | 0.0% | 1.2% |
| fans | 2.6% | 16.6% | 3.2% |
| `supply_air_reset` | tried: −5.33% → **deployed** | no handle | below 3% threshold |
| `hvac_availability` | below 3% threshold | tried: **+2.14%** → rejected | tried: +1.11% → rejected |
| outcome | supply air reset | **deploys nothing** | **deploys nothing** |

**Step 3 is not optional.** Fans dominate the small office, its availability schedule is
actuable, and running fans only when occupied still made the building worse — fan energy
alone rose 12.5%. Releasing the schedule hands the system to its
`AvailabilityManager:NightCycle`, which cycles on any zone 1 K off setpoint for a fixed 30
minutes, harder than the schedule it replaced. Neither the load breakdown nor the control
surface predicts that; it is a property of control objects already in the model. Only a trial
catches it. See [ADR 0008](docs/adr/0008-commissioning-by-trial.md).

The Delhi column also explains an earlier result from first principles: the same controller
returns −0.59% there over a year because heating is 1.2% of electricity against 18.5% in
Chicago. Commissioning declines the deployment in advance rather than discovering it was
pointless afterwards.

### Self-repair

A model EnergyPlus refuses is diagnosed, patched and rerun without a human editing it:

```
11 severe + 1 fatal → 1 fault
  'CLGSETP_SCH_TYPO' → 'CLGSETP_SCH'   (5 objects repointed)
  wrote repaired_v1.idf, run completed
```

The nearest existing name is proposed from the model's own object names, narrowed by field
naming convention. A name with no close match is **not guessed at** — the loop gives up and
says what it could not resolve, rather than retrying forever. Each attempt's model is written
out as epJSON and IDF.

---

## 8. Evaluation methodology

### The ablation ladder

Comparing one agent against one baseline is not evidence. Arms run in separate processes on
identical weather, run period and timestep, and are aligned on a time index that is
**asserted** to be identical — the property a lockstep comparison actually needs.

Full year, Chicago:

| arm | kWh | peak kW | unmet h | degree-hours |
| --- | --- | --- | --- | --- |
| baseline | 767,959 | 344.9 | 56.0 | 1,030 |
| deadband, unguarded | 761,115 (−0.89%) | 340.4 (−1.31%) | 231.3 | 1,715 (+67%) |
| **supervisor** | **727,003 (−5.33%)** | **315.1 (−8.66%)** | **51.5** | 1,037 (+0.7%) |
| foresight bound | 731,092 (−4.80%) | 315.1 (−8.66%) | 50.0 | 1,015 (−1.4%) |
| agent | 756,427 (−1.50%) | 340.4 (−1.31%) | 55.0 | 1,033 (+0.3%) |

`foresight` is the honest bar for the model: the same reset loop given the day's forecast
peak instead of the current reading, on the **same degraded forecast** the model gets. Over a
single season it lands on the reactive controller to within 0.03 points. Over a year it
trades 0.5 points of energy for 22 K·h of comfort — a different point on the same frontier,
not a better one. **Anticipation has no headroom to find on this measure**, which is why the
agent does not beat the deterministic controller.

Against the stock building alone the agent saves 1.5% and could be reported as a success.
That is only visible as a non-result because there is a deterministic bar to measure against.

### Integrity commitments

- **Forecasts are degraded.** The weather file is perfect foresight. Error growing with lead
  time is injected before the model sees it, so nothing that depends on planning ahead is
  flattered.
- **Comfort is scored against a band the controller cannot move.**
- **Where the model does not help, we say so**, and the foresight arm exists to prove it is
  the task and not the model.
- **Delhi results are a climate-sensitivity probe, not a claim about Indian buildings.** The
  US prototype run on Delhi weather shows 540 baseline unmet hours against 56 in Chicago; it
  is undersized for that climate. A real claim needs an ECBC-compliant model.

### Reproducibility

`make evidence` regenerates every published number and renders the report from the same
objects the simulations returned, so a figure in the write-up and a figure in the repository
cannot disagree. `tests/test_evidence.py` reads the published evidence back and asserts the
claims made about it — it caught its first drift on the first run, which is how the
season-versus-year distinction above was found. See
[ADR 0009](docs/adr/0009-evidence-is-generated-not-written.md).

---

## 9. What this does not do

- **No LLM in the per-timestep loop.** Measured and reported, not adopted: 52,560 inferences
  per simulated year, and a worse controller than the one it would replace.
- **No fine-tuning, no reinforcement learning.** Structured decoding and a good prompt are
  sufficient for a one-number decision; RL is sample-inefficient and brittle at this scale.
- **No real BACnet or hardware.** The actuator layer is a documented seam for it.
- **No hosted LLM.** The brief requires open-source models; the client speaks the
  OpenAI-compatible protocol so self-hosted vLLM or Ollama both work.
- **No live dashboard.** The generated report is the data export, self-contained and offline.
- **Two measures in the catalogue.** That is now the binding limit rather than the framework
  — the small office needs one we do not have, most likely fan cycling with load, which is a
  plant change rather than a supervisory one.

---

## Decision records

| | |
| --- | --- |
| [0001](docs/adr/0001-control-via-runtime-api.md) | Control through the EnergyPlus runtime API |
| [0002](docs/adr/0002-epjson-as-mutation-substrate.md) | epJSON as the model mutation substrate |
| [0003](docs/adr/0003-llm-outside-the-inner-loop.md) | The LLM sits above the control loop, not inside it |
| [0004](docs/adr/0004-comfort-scored-against-a-fixed-band.md) | Comfort is scored against a fixed band |
| [0005](docs/adr/0005-supply-air-reset-as-the-first-measure.md) | Supply air reset, and what it cannot do |
| [0006](docs/adr/0006-one-capability-layer-two-surfaces.md) | One capability layer, two surfaces |
| [0007](docs/adr/0007-what-the-model-supervises.md) | What the model supervises, and what that was worth |
| [0008](docs/adr/0008-commissioning-by-trial.md) | A measure earns its place by trial |
| [0009](docs/adr/0009-evidence-is-generated-not-written.md) | Evidence is generated, not written |
