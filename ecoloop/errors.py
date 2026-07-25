"""Structured reading of eplusout.err. Raw log text never reaches the agent."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

_SEVERITY = re.compile(r"\*\*\s*~?\s*(Warning|Severe|Fatal)\s*\*\*\s*(.*)", re.IGNORECASE)


class ErrorReport(BaseModel):
    warnings: int = 0
    severe: int = 0
    fatal: int = 0
    blocking: list[str] = []

    @property
    def failed(self) -> bool:
        return self.fatal > 0


def parse(err_file: Path) -> ErrorReport:
    if not err_file.is_file():
        return ErrorReport()

    counts: Counter[str] = Counter()
    blocking: list[str] = []
    for line in err_file.read_text(errors="replace").splitlines():
        match = _SEVERITY.search(line)
        if not match:
            continue
        severity, message = match.group(1).lower(), match.group(2).strip()
        counts[severity] += 1
        if severity != "warning":
            blocking.append(f"[{severity}] {message}")

    return ErrorReport(
        warnings=counts["warning"],
        severe=counts["severe"],
        fatal=counts["fatal"],
        blocking=blocking[:20],
    )
