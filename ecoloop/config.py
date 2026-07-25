"""Repository paths and pinned defaults."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
WEATHER = ROOT / "weather"
RUNS = ROOT / "runs"

DEFAULT_MODEL = MODELS / "RefBldgMediumOfficeNew2004_Chicago.idf"
DEFAULT_WEATHER = WEATHER / "USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw"

# Short shared windows so every arm is compared over identical weather.
PERIODS = {
    "smoke": "07-01:07-03",
    "summer": "07-15:07-21",
    "winter": "01-15:01-21",
    "shoulder": "04-15:04-21",
    "annual": "01-01:12-31",
}
