# 4. Comfort is scored against a fixed band, not the live setpoint

Status: accepted

## Context

The obvious comfort metric is unmet hours: occupied time when a zone is outside its
thermostat's throttling range. EnergyPlus reports it natively and ASHRAE 90.1 caps it at 300
hours a year.

It is measured against whatever setpoint is currently commanded. A controller that widens its
own deadband therefore improves its own score, and one that widens it to 40 C scores
perfectly. Comfort is 20% of the evaluation and the brief asks explicitly whether the agent
saved energy at the occupants' expense, so a metric the agent can move is not usable.

The failure is not subtle. A controller holding a zone at 29 C while commanding a 30 C
cooling setpoint reports zero unmet hours, and `tests/test_kpi.py` pins that down. Over a
full year the naive widened-band arm saves 0.89% of electricity and takes occupied time
outside a fixed 21-24 C band from 507 to 4,891 hours; unmet hours register only a quarter of
that relative change.

## Decision

Two metrics, reported side by side, with different jobs.

- **Unmet hours**, against the live setpoint: did the plant deliver what the controller asked
  for? This is a question about control feasibility, and it stays useful.
- **Comfort excursion**, against a fixed band taken from the baseline model's occupied
  setpoints: were occupants actually comfortable? The controller cannot move this band.

The excursion is reported both as hours outside and as degree-hours, because an hour 0.1 K
over the band and an hour 5 K over it are the same by count and are not the same thing. For
the widened-band arm the count suggests a tenfold degradation and degree-hours show 1.7x,
which is the more faithful reading: many shallow excursions rather than a few deep ones.

## Consequences

Energy savings can no longer be bought quietly from comfort. The Phase 2 guardian clamps
against this band, and it is the constraint the agent is told about.

The band is currently a constant carried on the run spec. Moving to an adaptive standard —
ASHRAE 55, or PMV and PPD from the Fanger model — needs the People objects to declare a
thermal comfort model, which the stock prototype does not. That is deferred, and the fixed
band is the honest interim: it is stricter than an adaptive standard, so it cannot flatter
the result.
