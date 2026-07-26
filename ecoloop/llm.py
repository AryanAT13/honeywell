"""Talking to a local model, defensively.

Nothing in here raises. A slow, unreachable or malformed response comes back as a Decision
carrying an error instead of a plan, and the caller keeps the plan it already had. The
simulation cannot be stopped by inference.

Every call is recorded whole and cached by prompt, so a run can be replayed without a model.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, Field, ValidationError

from .digest import StateDigest

# Both overridable, so swapping models or pointing at a remote server needs no code change.
DEFAULT_MODEL = os.environ.get("ECOLOOP_LLM_MODEL", "qwen2.5:3b-instruct")
DEFAULT_HOST = os.environ.get("ECOLOOP_LLM_HOST", "http://127.0.0.1:11434")

SYSTEM = """
You set one number for a commercial office building: the supply air temperature ceiling for
the next 24 hours.

The rule that decides this is one sentence: supply air must be cold enough to carry away the
day's cooling load, and no colder. A hot day has a large cooling load, so the ceiling must sit
near the 12.8 C minimum or the coils cannot keep up and zones overheat. A cold day has almost
no cooling load, so the ceiling can go to 18 C, which avoids chilling air only to reheat it
again. Warm forecast means low ceiling. Cold forecast means high ceiling.

The building is a three floor VAV system with electric reheat at the terminals. Air is cooled
centrally and then reheated in each zone. Raising supply air temperature cuts reheat, which is
the largest electrical load in this building. Lowering it protects zones that need cooling.

Interior core zones carry lighting and equipment load and need cooling in every season.
Perimeter zones lose heat outward and need reheat. One supply air temperature serves all of
them, so the ceiling is a compromise, and the cores are what overheat if you set it too high.

A trim and respond loop runs underneath you every 30 minutes. It lowers supply air below your
ceiling whenever a zone drifts outside the comfort band and lets it climb back as zones
recover, but it can only react once a zone is already uncomfortable. Your advantage over it is
the forecast: you can lower the ceiling before a warm afternoon instead of after it.

Mild days are the hard ones. Outdoor air is cool, so the reheat saving looks available, but
solar gain and occupancy still push the cores into cooling by afternoon. Judge those on the
warmest hours in the forecast, not on the temperature right now.

Valid range is 12.8 to 18.0 C. Give the ceiling and one sentence of reasoning.
""".strip()


class Plan(BaseModel):
    """The one decision the model is trusted with.

    Field order is load bearing. Constrained decoding emits properties in schema order, so
    reasoning has to come first or the number is generated before the model has thought.
    """

    reason: str
    supply_air_ceiling: float = Field(ge=12.8, le=18.0)


class Scorecard(BaseModel):
    """What happened under the previous plan."""

    ceiling: float
    hours: float
    mean_supply_air: float
    worst_excursion_k: float


class Decision(BaseModel):
    """One call, recorded whole: what was asked, what came back, what it cost."""

    time: datetime
    prompt_hash: str
    plan: Plan | None = None
    error: str | None = None
    attempts: int = 0
    latency_s: float = 0.0
    cached: bool = False


def render(digest: StateDigest, scorecard: Scorecard | None) -> str:
    forecast = ", ".join(f"{value:.1f}" for value in digest.forecast)
    lines = [
        f"Date {digest.time:%Y-%m-%d %H:%M}. Outdoor now {digest.outdoor_temperature:.1f} C.",
        f"Forecast next {len(digest.forecast) * digest.forecast_step_hours} h "
        f"at {digest.forecast_step_hours} h steps: {forecast}",
        f"Warmest zone {digest.warmest_zone} at {max(z.temperature for z in digest.zones):.1f} C.",
        f"Worst occupied excursion now {digest.worst_excursion_k:.2f} K; "
        f"{digest.cooling_requests} of {len(digest.zones)} zones asking for cooling.",
    ]
    if scorecard:
        lines.append(
            f"Under the previous ceiling of {scorecard.ceiling:.1f} C over "
            f"{scorecard.hours:.0f} h, supply air averaged {scorecard.mean_supply_air:.1f} C "
            f"and the worst excursion was {scorecard.worst_excursion_k:.2f} K."
        )
    lines.append("Choose the supply air ceiling for the next 24 hours.")
    return "\n".join(lines)


class Planner:
    """Asks a local model for the next plan, and never lets that fail loudly."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        timeout: float = 60.0,
        attempts: int = 2,
        cache_dir: Path | None = None,
    ):
        self.model = model
        self.host = host
        self.timeout = timeout
        self.attempts = attempts
        self.cache_dir = cache_dir
        self.decisions: list[Decision] = []

    def _cache_path(self, prompt_hash: str) -> Path | None:
        if self.cache_dir is None:
            return None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir / f"{prompt_hash}.json"

    def _ask(self, prompt: str, correction: str | None) -> str:
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ]
        if correction:
            messages.append({"role": "user", "content": correction})
        response = httpx.post(
            f"{self.host}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "format": Plan.model_json_schema(),
                "stream": False,
                "options": {"temperature": 0, "seed": 0, "num_predict": 200},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]

    def plan(self, digest: StateDigest, scorecard: Scorecard | None = None) -> Decision:
        prompt = render(digest, scorecard)
        prompt_hash = hashlib.sha256(f"{self.model}\n{SYSTEM}\n{prompt}".encode()).hexdigest()[:16]

        cached = self._cache_path(prompt_hash)
        if cached and cached.is_file():
            decision = Decision.model_validate_json(cached.read_text())
            decision.cached = True
            self.decisions.append(decision)
            return decision

        started = time.perf_counter()
        decision = Decision(time=digest.time, prompt_hash=prompt_hash)
        correction = None
        for attempt in range(1, self.attempts + 1):
            decision.attempts = attempt
            try:
                raw = self._ask(prompt, correction)
                decision.plan = Plan.model_validate_json(raw)
                decision.error = None
                break
            except ValidationError as invalid:
                decision.error = f"schema: {invalid.errors()[0]['msg']}"
                correction = (
                    f"That reply was rejected: {decision.error}. "
                    "Reply with only the JSON object, ceiling between 12.8 and 18.0."
                )
            except (httpx.HTTPError, json.JSONDecodeError, KeyError) as failure:
                decision.error = f"{type(failure).__name__}: {failure}"
                break

        decision.latency_s = round(time.perf_counter() - started, 2)
        if cached and decision.plan:
            cached.write_text(decision.model_dump_json(indent=2))
        self.decisions.append(decision)
        return decision
