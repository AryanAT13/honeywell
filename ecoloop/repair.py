"""Fixing a model that will not run, without a human editing it.

EnergyPlus reports a broken reference once per affected object: one mistyped schedule name
produced eleven severe errors and one fatal. Those resolve to a single fault, which is the
only thing worth acting on.

Each attempt keeps the model it produced, in both epJSON and IDF, so the versions generated
during a run are inspectable afterwards.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from pydantic import BaseModel

from . import errors, runner
from . import model as model_io
from .contracts import RunResult, RunSpec
from .errors import Fault
from .strategies import PolicyAuthor

MAX_ATTEMPTS = 3
SIMILARITY = 0.6


class Repair(BaseModel):
    attempt: int
    fault: Fault
    replacement: str
    objects_changed: int


class RepairedRun(BaseModel):
    result: RunResult | None = None
    repairs: list[Repair] = []
    versions: list[Path] = []
    gave_up: str | None = None


def _candidates(model: model_io.Model, field: str) -> list[str]:
    """Names that could plausibly satisfy this field, narrowed by its naming convention."""
    if field.endswith("_schedule_name"):
        pool = [n for kind, objs in model.items() if kind.startswith("Schedule") for n in objs]
        if pool:
            return pool
    return [name for objs in model.values() for name in objs]


def propose(model: model_io.Model, fault: Fault) -> str | None:
    """The existing name closest to the one EnergyPlus could not resolve."""
    matches = difflib.get_close_matches(
        fault.value, _candidates(model, fault.field), n=1, cutoff=SIMILARITY
    )
    return matches[0] if matches else None


def patch(model: model_io.Model, fault: Fault, replacement: str) -> int:
    """Repoint every object carrying the broken reference. Returns how many changed."""
    changed = 0
    for fields in model.get(fault.object_type, {}).values():
        if fields.get(fault.field) == fault.value:
            fields[fault.field] = replacement
            changed += 1
    return changed


def run_with_repair(
    spec: RunSpec,
    author: PolicyAuthor | None = None,
    attempts: int = MAX_ATTEMPTS,
) -> RepairedRun:
    """Run the model, and if EnergyPlus refuses it, fix what it complained about and retry."""
    workspace = spec.output_dir.parent / f"{spec.output_dir.name}_versions"
    workspace.mkdir(parents=True, exist_ok=True)
    source = spec.model
    if source.suffix.lower() == ".idf":
        source = model_io.convert(source, workspace, "epJSON")

    outcome = RepairedRun()
    for attempt in range(1, attempts + 1):
        try:
            outcome.result = runner.run(spec.model_copy(update={"model": source}), author)
            return outcome
        except RuntimeError as refused:
            report = errors.parse(spec.output_dir / "eplusout.err")
            if not report.faults:
                outcome.gave_up = f"nothing repairable in the error log: {refused}"
                return outcome

            building = model_io.load(source)
            applied = []
            for fault in report.faults:
                replacement = propose(building, fault)
                if replacement is None:
                    continue
                changed = patch(building, fault, replacement)
                if changed:
                    applied.append(
                        Repair(
                            attempt=attempt,
                            fault=fault,
                            replacement=replacement,
                            objects_changed=changed,
                        )
                    )
            if not applied:
                outcome.gave_up = (
                    f"no candidate for {report.faults[0].field} = {report.faults[0].value}"
                )
                return outcome

            outcome.repairs.extend(applied)
            source = model_io.save(building, workspace / f"repaired_v{attempt}.epJSON")
            outcome.versions.append(model_io.convert(source, workspace, "idf"))

    outcome.gave_up = f"still failing after {attempts} attempts"
    return outcome
