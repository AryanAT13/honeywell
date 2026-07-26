"""Making a model EnergyPlus refuses run, without a human editing it."""

from __future__ import annotations

import pytest

from ecoloop import config, errors, repair
from ecoloop import model as model_io
from ecoloop.contracts import RunPeriod, RunSpec

WINDOW = "01-15:01-16"
TYPO = "CLGSETP_SCH_TYPO"


@pytest.fixture
def broken(tmp_path):
    """A model with one mistyped schedule reference, which EnergyPlus will not run."""
    source = model_io.convert(
        config.MODELS / "RefBldgSmallOfficeNew2004_Chicago.idf", tmp_path, "epJSON"
    )
    building = model_io.load(source)
    for fields in building["ThermostatSetpoint:DualSetpoint"].values():
        fields["cooling_setpoint_temperature_schedule_name"] = TYPO
    return model_io.save(building, tmp_path / "broken.epJSON")


@pytest.mark.slow
def test_many_errors_resolve_to_one_fault(broken, tmp_path):
    outcome = repair.run_with_repair(
        RunSpec(
            label="healed",
            model=broken,
            weather=config.DEFAULT_WEATHER,
            run_period=RunPeriod.parse(WINDOW),
            output_dir=tmp_path / "healed",
        )
    )
    report = errors.parse(tmp_path / "healed" / "eplusout.err")
    assert report.severe == 0

    assert len(outcome.repairs) == 1
    repaired = outcome.repairs[0]
    assert repaired.fault.value == TYPO
    assert repaired.replacement == "CLGSETP_SCH"
    assert repaired.objects_changed == 5


@pytest.mark.slow
def test_the_repaired_model_runs_and_is_written_out(broken, tmp_path):
    outcome = repair.run_with_repair(
        RunSpec(
            label="healed",
            model=broken,
            weather=config.DEFAULT_WEATHER,
            run_period=RunPeriod.parse(WINDOW),
            output_dir=tmp_path / "healed",
        )
    )
    assert outcome.gave_up is None
    assert outcome.result is not None
    assert outcome.result.kpis.electricity_kwh > 0
    assert [v.name for v in outcome.versions] == ["repaired_v1.idf"]
    assert outcome.versions[0].is_file()


@pytest.mark.slow
def test_an_unrepairable_model_gives_up_instead_of_looping(tmp_path):
    source = model_io.convert(
        config.MODELS / "RefBldgSmallOfficeNew2004_Chicago.idf", tmp_path, "epJSON"
    )
    building = model_io.load(source)
    for fields in building["ThermostatSetpoint:DualSetpoint"].values():
        fields["cooling_setpoint_temperature_schedule_name"] = "ZZZZZZZZZZ"
    unfixable = model_io.save(building, tmp_path / "unfixable.epJSON")

    outcome = repair.run_with_repair(
        RunSpec(
            label="hopeless",
            model=unfixable,
            weather=config.DEFAULT_WEATHER,
            run_period=RunPeriod.parse(WINDOW),
            output_dir=tmp_path / "hopeless",
        ),
        attempts=2,
    )
    assert outcome.result is None
    assert outcome.gave_up


def test_a_close_name_is_proposed_and_a_wild_one_is_not():
    building = {"Schedule:Compact": {"CLGSETP_SCH": {}, "HTGSETP_SCH": {}}}
    near = errors.Fault(
        object_type="ThermostatSetpoint:DualSetpoint",
        object_name="x",
        field="cooling_setpoint_temperature_schedule_name",
        value=TYPO,
    )
    far = near.model_copy(update={"value": "ZZZZZZZZZZ"})
    assert repair.propose(building, near) == "CLGSETP_SCH"
    assert repair.propose(building, far) is None
