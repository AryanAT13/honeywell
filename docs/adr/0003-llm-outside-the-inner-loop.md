# 3. The LLM sits above the control loop, not inside it

Status: accepted

## Context

An annual simulation at a 10-minute timestep is 52,560 control decisions. At two seconds per
inference that is 29 hours of wall clock, against 13.5 s for the simulation itself. Beyond
cost, an LLM asked to emit raw numeric setpoints every timestep is a worse controller than
the physics-based optimizer it would replace, and a stochastic one.

The brief nevertheless asks for continuous forward injection of control actions, and names
prompt latency management as a deliverable topic.

## Decision

Three timescales, and only the outer two involve the LLM.

- Every timestep, a deterministic policy execution engine applies the active policy and a
  guardian clamps it to a safe envelope. No inference.
- Roughly hourly and on triggering events, the LLM emits the next horizon's policy as
  validated JSON: setpoint trajectories, deadbands, precool windows, demand caps, objective
  weights.
- Daily and weekly, the LLM scores what it did, updates memory, and proposes structural
  measures.

If a response is late, malformed or absent, the previous validated plan stays active and the
deterministic controller keeps running underneath it.

This originally also called for double buffering, computing horizon N+1 while horizon N
executes. Phase 4 measurement retired that. An annual simulation takes 14 seconds and a local
inference takes seconds, so the simulation is never the party that waits; overlapping the two
would save at most the simulation's own runtime. What actually matters is calling the model
rarely enough and never depending on the answer arriving, both of which are cheaper to build
than a buffered pipeline and easier to reason about.

Control actions are still injected into the live instance every timestep, and the LLM runs
continuously throughout the run rather than post-processing it.

## Consequences

Annual closed-loop runs are feasible and the pipeline cannot be brought down by an LLM
failure. The tradeoff is a design choice a reviewer could question, so it is treated as a
measurement rather than an assertion: a `--strict-realtime` mode calls the LLM every control
timestep, and a cadence sweep reports savings against wall clock and token cost. If the
per-timestep arm wins on quality, the evidence will say so.
