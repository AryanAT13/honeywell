# 2. epJSON as the model mutation substrate

Status: accepted

## Context

The agent has to modify building models at runtime, and the deliverables ask for the
baseline `.idf` plus the modified versions generated during evaluation. IDF is a positional
text format: editing it means either string manipulation or a library such as eppy, and
mistakes surface only when EnergyPlus fails to parse the result.

EnergyPlus reads epJSON natively, ships a JSON Schema for it (`Energy+.schema.epJSON`), and
bundles `ConvertInputFormat` for lossless conversion in both directions.

## Decision

Models are converted to epJSON on entry and mutated as plain dictionaries. IDF is generated
on the way out for the deliverable.

## Consequences

Mutations are ordinary dict operations, diff cleanly in review, and can be schema-validated
before a run rather than after a failure. No IDF parsing dependency. The cost is a
sub-second conversion step per model and one extra representation to keep track of.
