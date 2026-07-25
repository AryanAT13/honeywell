"""The guardian is the reason a controller cannot buy energy with comfort."""

from __future__ import annotations

from datetime import datetime

import pytest

from ecoloop import digest
from ecoloop.contracts import ComfortBand
from ecoloop.control import Setpoints, ZoneObservation
from ecoloop.policy import Band, Guardian, Limits, Policy, apply

COMFORT = ComfortBand(lower=21.0, upper=24.0, tolerance=0.2)


def observation(zone="Core", temperature=23.0, occupancy=5.0):
    return ZoneObservation(
        zone=zone,
        temperature=temperature,
        occupancy=occupancy,
        scheduled=Setpoints(21.0, 24.0),
        outdoor_temperature=18.0,
    )


def test_widening_beyond_the_contract_is_clamped_back():
    guardian = Guardian(COMFORT)
    reviewed = guardian.review(Policy(occupied=Band(heating=18.0, cooling=28.0)))
    assert reviewed.occupied == Band(heating=21.0, cooling=24.0)
    assert guardian.clamps == {"occupied.heating": 1, "occupied.cooling": 1}


def test_a_tighter_band_is_left_alone():
    guardian = Guardian(COMFORT)
    tighter = Band(heating=22.0, cooling=23.5)
    assert guardian.review(Policy(occupied=tighter)).occupied == tighter
    assert not guardian.clamps


def test_deadband_is_widened_to_avoid_simultaneous_heating_and_cooling():
    guardian = Guardian(COMFORT, Limits(min_deadband=1.0))
    reviewed = guardian.review(Policy(occupied=Band(heating=23.4, cooling=23.6)))
    assert reviewed.occupied.width == pytest.approx(1.0)
    assert reviewed.occupied.cooling == pytest.approx(23.6)


def test_supply_air_is_held_inside_its_limits():
    guardian = Guardian(COMFORT, Limits(supply_air_min=12.0, supply_air_max=18.0))
    assert guardian.review(Policy(supply_air_temperature=25.0)).supply_air_temperature == 18.0
    assert guardian.review(Policy(supply_air_temperature=5.0)).supply_air_temperature == 12.0
    assert guardian.clamps["supply_air"] == 2


def test_a_policy_without_bands_leaves_the_zone_on_its_schedule():
    assert apply(Policy(supply_air_temperature=15.0), observation()) is None
    assert apply(None, observation()) is None


def test_occupancy_selects_the_band():
    policy = Policy(
        occupied=Band(heating=21.0, cooling=24.0),
        unoccupied=Band(heating=15.6, cooling=26.7),
    )
    assert apply(policy, observation(occupancy=5.0)) == Setpoints(21.0, 24.0)
    assert apply(policy, observation(occupancy=0.0)) == Setpoints(15.6, 26.7)


def test_digest_reports_the_worst_zone_not_an_average():
    observations = {
        "cool": observation("cool", temperature=22.0),
        "hot": observation("hot", temperature=26.0),
    }
    effective = dict.fromkeys(observations, Setpoints(21.0, 24.0))
    state = digest.build(datetime(2023, 7, 1, 12), observations, effective, 100.0, COMFORT)

    assert state.warmest_zone == "hot"
    assert state.worst_excursion_k == pytest.approx(26.0 - 24.2)
    assert state.cooling_requests == 1


def test_unoccupied_zones_do_not_generate_excursions():
    observations = {"hot": observation("hot", temperature=30.0, occupancy=0.0)}
    effective = {"hot": Setpoints(15.6, 26.7)}
    state = digest.build(datetime(2023, 1, 1, 3), observations, effective, 50.0, COMFORT)
    assert state.worst_excursion_k == 0.0
