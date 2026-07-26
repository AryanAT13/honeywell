# 9. Evidence is generated, and the agent's decisions are committed

Status: accepted

## Context

Every figure in this repository was measured, and several changed as the work went on. The
supply air controller was retuned twice, the comfort metric was replaced, and the claim that
anticipation had headroom was refuted. Numbers transcribed into prose go stale silently, and
a reader has no way to tell which ones did.

Reproducibility has a second problem here. The agent arm makes 368 model calls over a
simulated year. Someone cloning the repository has to install Ollama, pull a model, and wait
roughly fifty minutes before they can check a single figure — and even then a different model
version would give different answers.

## Decision

`make evidence` runs the full ladder over both climates and four periods, commissions all
three building-and-climate pairs, and writes `docs/evidence/evidence.json` and a rendered
`report.html`. The report is generated from the same objects the simulations returned, so a
figure in the write-up and a figure in the repository cannot disagree.

The agent's decision journal is committed alongside it: 390 records of what the model was
asked, what it answered, how long it took and whether it needed a repair retry. The agent arm
replays from that journal, so a clean clone reproduces it exactly without a model server.

`tests/test_evidence.py` reads the published evidence back and asserts the claims the README
makes about it.

## Consequences

Regenerating everything takes about three minutes from the committed journal, against fifty
for a cold run against a live model. Ollama is needed to generate new decisions, not to
reproduce recorded ones.

The test caught its first drift immediately. It asserted that the foresight bound matches the
reactive controller to within 0.1 percentage points, which is true over a single season —
winter is -13.24% against -13.25% — and false over a year, where foresight returns -4.80%
against -5.33% with 22 K.h better comfort. Anticipation buys a different point on the
energy-comfort frontier rather than a better one, and neither arm dominates. That is a
sharper statement than the one it replaced, and it only surfaced because the claim was
executable.

The cost is 1.5 MB of committed JSON and the discipline of regenerating rather than editing.
The risk is that a stale `evidence.json` looks authoritative; the test is what stops that,
because it fails when the committed numbers stop supporting the claims made about them.
