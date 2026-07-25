"""Locates the pinned EnergyPlus install and exposes its Python API."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

VERSION = "26.1.0"
_DIRNAME = f"EnergyPlus-{VERSION.replace('.', '-')}"

_CANDIDATES = [
    Path.home() / "opt" / _DIRNAME,
    Path("/Applications") / _DIRNAME,
    Path("/usr/local") / _DIRNAME,
    Path("/opt") / _DIRNAME,
]


class EnergyPlusNotFound(RuntimeError):
    pass


@lru_cache(maxsize=1)
def root() -> Path:
    """Root of the EnergyPlus install (the directory containing pyenergyplus/)."""
    override = os.environ.get("ECOLOOP_EPLUS_DIR")
    candidates = [Path(override)] if override else _CANDIDATES
    for path in candidates:
        if (path / "pyenergyplus" / "api.py").is_file():
            return path
    searched = "\n  ".join(str(c) for c in candidates)
    raise EnergyPlusNotFound(
        f"EnergyPlus {VERSION} not found. Searched:\n  {searched}\n"
        "Run `make install-eplus`, or set ECOLOOP_EPLUS_DIR."
    )


@lru_cache(maxsize=1)
def api():
    """The EnergyPlusAPI singleton. States are created per-run, the API object is not."""
    path = str(root())
    if path not in sys.path:
        sys.path.insert(0, path)
    from pyenergyplus.api import EnergyPlusAPI

    return EnergyPlusAPI()


def convert_input_format() -> Path:
    """Path to the ConvertInputFormat binary used for IDF <-> epJSON."""
    binary = root() / "ConvertInputFormat"
    if not binary.is_file():
        raise EnergyPlusNotFound(f"ConvertInputFormat missing at {binary}")
    return binary


def example_files() -> Path:
    return root() / "ExampleFiles"


def weather_data() -> Path:
    return root() / "WeatherData"
