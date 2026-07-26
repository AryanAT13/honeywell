# 7. What the model supervises, and what measurement said about it

Status: accepted

## Context

Phase 2 ended with a named target. Trim and respond is reactive and structurally cannot
anticipate: it only sees a zone after it has left the comfort band, and shoulder-season core
overheating is driven by solar and occupancy gains that are visible hours ahead in a forecast.
Closing that gap looked like the natural first job for a model.

So the model was given one number, the supply air ceiling for the next 24 hours, chosen from a
day-ahead forecast. The Guideline 36 loop keeps running underneath at its own cadence. The
forecast is deliberately degraded, because the weather file is perfect foresight and reading
it straight out would flatter anything that depends on planning ahead.

## Decision

The model sets the daily ceiling and may only lower it, never raise it above the outdoor
temperature curve.

That asymmetry was not the original design. It was added after measuring that Qwen2.5 3B
answers 18.0 C — the warmest available deck — for a day forecast to reach 34 C, which starves
the coils on exactly the day they are needed most. Three prompt revisions did not fix it,
including stating the rule outright as "warm forecast means low ceiling". One design error of
ours was found and fixed along the way: constrained decoding emits JSON properties in schema
order, so with the number declared before the reason the model was committing to a value
before writing a word of reasoning. Reordering the fields improved the reasoning but not the
direction.

Qwen2.5 7B was measured on the same three cases and does not settle it either. It gets the hot
day right at 13.0 C, which the 3B never did, and then asks for 13.5 C on a day that never rises
above 3 C, forfeiting nearly all the reheat saving. Right for the wrong reason is not reliably
better than wrong. It is also three times slower on this hardware, which exposes 5.3 GiB of
VRAM: roughly 16 s a call against 5 s for the 3B, once each model is measured with the other
unloaded. Running both in one session thrashes them and inflates each to nearly a minute,
which is worth knowing before believing any latency figure taken while something else was
loaded.

Bounding the model to lowering is the same principle as the guardian one layer down. Do not
rely on the model being right; make being wrong harmless.

## Consequences

The bound works. On a summer week the agent arm is byte-identical to the untouched baseline,
because every request to raise the deck is discarded.

The measured result is that the model subtracts value on this task.

| period | deterministic | agent, Qwen2.5 3B |
| --- | --- | --- |
| winter | -13.25%, 21.7 K.h | -6.05%, 22.2 K.h |
| shoulder | -1.43%, 17.4 K.h | -0.03%, 17.3 K.h |
| summer | 0.00%, 25.7 K.h | 0.00%, 25.7 K.h |

Before blaming the model, we bounded the task. A perfect-discrimination supervisor — the same
outdoor curve applied to the forecast peak rather than the current reading, which is exactly
the anticipation the model was asked to supply — returns -13.24%, -1.40% and 0.00%. That is
the deterministic controller to within 0.03 percentage points.

**There is no anticipation headroom left on this measure.** The gap Phase 2 identified was
closed by Phase 2 itself, when the correction was retuned to respond to the worst zone's
excursion with a gain of 4.0. That reacts inside a single 30-minute decision, which is fast
enough that seeing the afternoon coming adds nothing.

So the ceiling on this task is a tie, and a model can only lose. That is worth knowing
precisely because it is invisible without a deterministic bar to measure against: compared
only to the stock building, this agent saves 6% and looks like a success.

Over a full year the agent makes 368 decisions, none of which fail, five of which need the
schema repair retry. It returns -1.50% against the supervisor's -5.33%, with comfort 3.7 K.h
better out of 1030. The arm takes 2,898 s live and 28 s to replay from cache, reproducing
every KPI exactly — but only after pinning the sampler seed. Ollama draws a fresh seed per
call by default, and because each answer feeds the next prompt through the scorecard, one
flipped digit cascaded into a visibly different year.

Two independent findings therefore point the same way: the task has no headroom, and neither
local model performs it reliably in any case. The first would hold even with a perfect model.

The conclusion is not that the model is useless, but that this was the wrong job for it.
Tuning a measure that is already tuned has no room in it. The measures this building has never
had — optimal start against its February morning peak, terminal minimum flow, economiser
operation — are worth more than perfecting the one it has, and choosing between them for an
unfamiliar building is the judgement a curve cannot make. That is Phase 5, and it is also the
original thesis: the model exists to remove the per-building engineering cost, not to
outperform a controller on a task an engineer has already tuned.
