# 5. Supply air reset is the deterministic controller, and what it cannot do

Status: accepted; the anticipation hypothesis below was later refuted by ADR 0007

## Context

The deterministic arm exists to be the honest bar. Beating the stock model is easy; the claim
worth making later is that an agent beats a competent controller, so this one has to be
competent.

Two measurements pointed at the same measure. Electric terminal reheat is 18.5% of this
building's electricity, more than cooling at 10%, and the model holds supply air at a
constant 12.8 C all year through a schedule named "Seasonal-Reset" that resets nothing.
Meanwhile the obvious alternative, widening the thermostat deadband, returns 0.89% of
electricity for a 67% increase in comfort degree-hours.

## Decision

Supply air temperature reset, after ASHRAE Guideline 36: an outdoor-temperature feedforward
sets the ceiling, and a trim-and-respond correction pulls back from it. Thermostats are not
touched, so the measured effect is supply air alone.

Two details are load-bearing and both were found by measurement.

The correction is driven by the **single worst zone**, not by a count of zones past setpoint.
Two internal-load-dominated core zones need cooling in every season while the perimeter needs
reheat, and one supply air temperature serves both. Averaging across fifteen zones let the
cores overheat: they accounted for 115 of the 128 degree-hours of shoulder-season damage,
against 1.8 or less for every perimeter zone.

The correction is **clamped to the usable range**. Without that it accumulates all summer,
where it cannot help because the outdoor ceiling already holds supply air at the minimum, and
then takes months of trimming to decay back through the shoulder season. That windup cost the
entire saving: 11.02% became 0.17%.

## Consequences

Annually against the stock model: 5.33% less electricity, 8.66% lower peak, unmet hours down
from 56.0 to 51.5, and comfort degree-hours up 0.68%.

Strict non-degradation of degree-hours is not reachable with a reactive controller. The
frontier is real, and was mapped rather than tuned away:

| trim rate | electricity | peak | degree-hours |
| --- | --- | --- | --- |
| 0.05 | -5.33% | -8.66% | +7.0 |
| 0.10 | -7.59% | -9.47% | +15.5 |
| 0.20 | -9.84% | -8.30% | +19.6 |
| 0.40 | -10.88% | -9.24% | +22.7 |

We take the comfort-first end. The frontier exists because the controller is reactive:
shoulder-season core overheating is driven by solar and internal gains that are predictable
hours ahead, and a controller that responds only after a zone has left the band cannot
pre-empt it. Closing that gap needs anticipation, which is a concrete and measurable target
for the agent rather than a general hope that it will do better.

That hypothesis was tested in Phase 4 and did not survive. A supervisor with perfect
discrimination, applying this same curve to the forecast peak instead of the current reading,
matches the reactive controller to within 0.03 percentage points. The excursion-driven
correction adopted above already reacts inside one 30-minute decision, which is fast enough
that seeing the afternoon coming adds nothing. See ADR 0007.

The measure is also climate-specific, which matters more than the frontier. Run unchanged on
New Delhi weather it returns 0.59% over a year, because Delhi sits above the outdoor ceiling
for almost all of it and supply air stays pinned at the minimum. In a Delhi January it
returns 2.88%. A controller tuned for one climate is worth nothing in another, and selecting
the right measure per building and per climate is exactly the engineering cost that keeps
advanced control out of most buildings.
