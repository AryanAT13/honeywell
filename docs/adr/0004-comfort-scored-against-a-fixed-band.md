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

Phase 1 measured this rather than assuming it. Widening the occupied deadband by 0.5 K over a
summer week cut energy 1.49% and *improved* unmet hours from 6.00 to 4.50, while occupied
time outside a fixed 21-24 C band went from 10.83 to 99.00 hours.

## Decision

Two metrics, reported side by side, with different jobs.

- **Unmet hours**, against the live setpoint: did the plant deliver what the controller asked
  for? This is a question about control feasibility, and it stays useful.
- **Comfort excursion**, against a fixed band taken from the baseline model's occupied
  setpoints: were occupants actually comfortable? The controller cannot move this band.

The excursion is reported both as hours outside and as degree-hours, because an hour 0.1 K
over the band and an hour 5 K over it are the same by count and are not the same thing. Over
the same week the count suggested a ninefold degradation and degree-hours showed 2.2x, which
is the more faithful reading: many shallow excursions rather than a few deep ones.

## Consequences

Energy savings can no longer be bought quietly from comfort. The Phase 2 guardian clamps
against this band, and it is the constraint the agent is told about.

The band is currently a constant carried on the run spec. Moving to an adaptive standard —
ASHRAE 55, or PMV and PPD from the Fanger model — needs the People objects to declare a
thermal comfort model, which the stock prototype does not. That is deferred, and the fixed
band is the honest interim: it is stricter than an adaptive standard, so it cannot flatter
the result.
