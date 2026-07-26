"""Structured reading of eplusout.err. Raw log text never reaches the agent."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

_SEVERITY = re.compile(r"\*\*\s*(Warning|Severe|Fatal)\s*\*\*\s*(.*)", re.IGNORECASE)
_DETAIL = re.compile(r"\*\*\s*~~~\s*\*\*\s*(.*)")

# "GetZoneAirSetpoints: ThermostatSetpoint:DualSetpoint = CORE_ZN DUALSPSCHED"
_SUBJECT = re.compile(r"([\w:]+)\s*=\s*(.+?)\s*$")
# "cooling_setpoint_temperature_schedule_name = CLGSETP_SCH_TYPO, item not found."
_DANGLING = re.compile(r"(\w+)\s*=\s*(.+?),\s*item not found", re.IGNORECASE)


class Fault(BaseModel):
    """A blocking error resolved down to the one field that caused it."""

    object_type: str
    object_name: str
    field: str
    value: str

    @property
    def signature(self) -> tuple[str, str, str]:
        return (self.object_type, self.field, self.value)


class ErrorReport(BaseModel):
    warnings: int = 0
    severe: int = 0
    fatal: int = 0
    blocking: list[str] = []
    faults: list[Fault] = []

    @property
    def failed(self) -> bool:
        return self.fatal > 0


def parse(err_file: Path) -> ErrorReport:
    if not err_file.is_file():
        return ErrorReport()

    counts: Counter[str] = Counter()
    blocking: list[str] = []
    faults: dict[tuple[str, str, str], Fault] = {}
    subject: tuple[str, str] | None = None

    for line in err_file.read_text(errors="replace").splitlines():
        severity_match = _SEVERITY.search(line)
        if severity_match:
            severity, message = severity_match.group(1).lower(), severity_match.group(2).strip()
            counts[severity] += 1
            subject = None
            if severity != "warning":
                blocking.append(f"[{severity}] {message}")
                named = _SUBJECT.search(message)
                if named:
                    subject = (named.group(1), named.group(2))
            continue

        detail = _DETAIL.search(line)
        if not detail or subject is None:
            continue
        dangling = _DANGLING.search(detail.group(1))
        if dangling:
            fault = Fault(
                object_type=subject[0],
                object_name=subject[1],
                field=dangling.group(1),
                value=dangling.group(2),
            )
            faults.setdefault(fault.signature, fault)

    return ErrorReport(
        warnings=counts["warning"],
        severe=counts["severe"],
        fatal=counts["fatal"],
        blocking=blocking[:20],
        faults=list(faults.values()),
    )
