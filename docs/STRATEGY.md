# Strategy

The reference document for what Eco-Loop is trying to be and how it will be judged.

## Thesis

Advanced supervisory control — optimal start, demand limiting, supply-air temperature reset,
demand-controlled ventilation — is documented at 15–30% savings and decades old. It reaches
under 5% of buildings. The blocker is not the algorithm; it is that every deployment needs
weeks of engineering time to map points, build a model, and tune a strategy.

So the LLM's job is not to be a better controller than a model-predictive one. It will not
be. Its job is to remove the per-building engineering cost: read an unfamiliar model,
discover its own sensors and actuators, write the control strategy, validate it, repair it
when it breaks, and re-commission as the building drifts.

We use the LLM where it beats code — contextual arbitration, exception handling, model
repair, explanation — and physics and optimisation where they beat the LLM.

## Architecture

Five layers. Only two of them ever call a model.

| Layer | Contents |
| --- | --- |
| Observation | Dashboard over WebSocket, report generation, MCP chat client |
| Cognition | Commissioner, Strategist, Reflector, Repairer — open-source LLM |
| Protocol | MCP server: `sim`, `telemetry`, `policy`, `constraints`, `idf`, `logs`, `weather`, `grid`, `kpi` |
| Deterministic core | Guardian, policy execution engine, surrogate and optimiser, state digest compiler |
| Simulation | N EnergyPlus instances in lockstep on identical weather |

Control timescales and the reasoning behind them are in
[ADR 0003](adr/0003-llm-outside-the-inner-loop.md).

### Lockstep twin comparison

Baseline and agent run as parallel instances on the same weather, timestep and seed. Savings
are a paired same-instant comparison rather than two runs differenced after the fact. It also
gives the demonstration its clearest visual: several arms racing on one chart.

### Guardian

Every policy the LLM emits is schema-validated and then projected onto a feasible set —
comfort bounds, minimum deadband, ramp limits, occupied and unoccupied bands, actuator
limits. Clamps are logged and displayed. Trading comfort for energy is made structurally
impossible rather than discouraged by prompt.

### Handling long simulation logs

Raw log text never reaches the model.

1. Telemetry is structured. The model receives a fixed-size state digest — current values,
   deltas against target, trend slopes, exceedance counters. Prompt size is constant in
   simulation length.
2. `eplusout.err` is parsed into records, deduplicated by signature and ranked by severity.
3. Detail sits behind MCP tools the agent queries when it needs them, rather than being
   pushed into context.
4. Rollups are hierarchical, with a small episodic memory of validated lessons carried
   across horizons.

## Evaluation

Comparing one agent against one baseline is not evidence. Six arms:

| Arm | |
| --- | --- |
| B0 | Stock model, unmodified schedules — the required reference |
| B1 | Hand-tuned static rules: setback and widened deadband |
| B2 | Deterministic MPC-lite, no LLM — the honest bar |
| B3 | Eco-Loop |
| B4 | Eco-Loop with the guardian disabled |
| B5 | Perfect-foresight oracle — the remaining headroom |

B2 is the arm that matters. Beating B0 is easy; beating a competent deterministic controller
is the claim worth making, and if B3 only matches B2 in steady conditions we report that and
locate the LLM's value where it actually is — commissioning an unseen building, anomalous
conditions, and self-repair.

Metrics: electricity and EUI, peak demand, carbon against time-varying grid intensity, cost
under a commercial tariff with demand charges, unmet occupied hours, PMV and PPD
distribution. Agent metrics: tool-call success, schema validity, hallucinated actuators,
clamp rate, decision latency, tokens per simulated day.

Three commitments that make the numbers defensible:

- Forecasts are noised. The EPW is perfect foresight and using it directly would be cheating.
- Multiple periods and climates, so results are not a single-week artefact.
- Where the LLM does not help, we say so.

## Not doing

- LLM inference in the per-timestep loop — measured and reported, not adopted.
- Fine-tuning. Structured decoding and good prompts are sufficient and cheaper.
- Reinforcement learning. Sample-inefficient and brittle at this scale.
- Real BACnet or hardware. The actuator layer keeps a documented seam for it.
- Heavy agent frameworks. Pydantic contracts and a small explicit loop instead.
- Hosted LLM APIs. The brief requires open-source models; the client speaks the
  OpenAI-compatible protocol so self-hosted vLLM or Ollama both work.
- Multi-building portfolios, occupant applications, geometry work.

## Phases

Each phase ends at a gate. The next does not start until the gate is green and committed,
and `main` stays demonstrable from Phase 1 onward.

| Phase | Gate |
| --- | --- |
| 0 Foundation | A real simulation runs and its energy totals reconcile with EnergyPlus's own report |
| 1 Thin closed loop | 7-day run writing setpoints to the live instance, paired against a baseline twin |
| 2 Deterministic core | B2 beats B0 on energy with unmet hours no worse, over a full year |
| 3 MCP server | An external MCP client can inspect, read telemetry, propose a policy and read errors |
| 4 Cognition | Annual run, no unhandled exceptions, beats B2; killing the LLM mid-run does not stop it |
| 5 Commissioning and chaos | Controls an unseen model with no code changes; fault matrix green |
| 6 Evidence | Every number in the deck regenerable by one command from a clean clone |
| 7 Delivery | A stranger clones the repository and reproduces the headline number |

Phases 5 and 6 are the compressible ones. If time runs short we cut the cadence sweep and
the additional climates. We do not cut the guardian, the ablation ladder, or the fault suite.
