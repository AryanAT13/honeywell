# 8. A measure earns its place by trial, not by matching

Status: accepted

## Context

The premise of this project is that advanced control does not scale because every building
needs bespoke engineering. Phase 5 pointed the system at a building it had not been built
for, and found three things in order.

The deterministic supervisor is not portable, and fails silently. The small office has five
packaged single-zone units and no central supply air schedule, so there is nothing for supply
air reset to actuate. Pointed at it, the controller ran 336 policy decisions over a week,
changed nothing, exited zero, and reported success. Energy matched the baseline to the last
digit. That is the failure a deployment ships and nobody notices.

The right measure is different per building, and the load breakdown says which. Electric
reheat is 18.5% of the medium office and fans are 2.6%; the small office has no electric
heating at all and fans are 16.6%. Supply air reset attacks the first and cannot help the
second.

And then the interesting one. Fans dominate the small office, its fan availability schedule
is actuable, and running the fans only when the building is occupied or drifting still made
things **worse**: 2.14% more electricity, with fan energy itself up 12.5%. Releasing the
schedule hands the system to its `AvailabilityManager:NightCycle`, which cycles on any zone
exceeding a 1 K tolerance for a fixed 30 minutes, and that cycles harder than the schedule it
replaced. Nothing in the load breakdown or the control surface predicts this. It is a
property of the control objects already in the model.

## Decision

Commissioning is three steps, and the third is not optional.

1. **Discover** what can be actuated, from the model's own wiring.
2. **Survey** where the energy goes, from an untouched annual run.
3. **Trial** every measure that is both actuable and aimed at a material load, against that
   baseline, and keep only the ones that actually save energy without spending comfort.

If nothing survives, the system deploys nothing and says why. Refusing is a correct outcome.

## Consequences

The medium office commissions into supply air reset on the evidence: heating 18.5% of
electricity, tried, -5.33% for +7.0 K.h. It rejects availability trimming because fans are
2.6% there, below the share worth touching.

The small office rejects both — one for having no handle, one for being tried and found
harmful — and the system refuses to deploy. That refusal is the result. A framework that
deployed the fan measure because it was applicable and aimed at the dominant load would have
made that building worse and reported a success.

Trials cost one annual simulation per candidate, about 14 seconds each. That is cheap against
being wrong, and it is the same machinery `evaluate_policy` already exposes over MCP.

The limitation is now the catalogue rather than the framework, which is the right place for
it. Two measures is not many, and the small office needs one we do not have — most likely fan
cycling with load rather than scheduled operation, which is a plant change rather than a
supervisory one.
