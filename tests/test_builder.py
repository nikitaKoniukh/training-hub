import json
from pathlib import Path

import pytest

from hub.workout_builder import SpecError, build, load_spec

RUN = {
    "date": "2026-11-26", "sport": "running", "name": "Tempo 3x10",
    "steps": [
        {"type": "warmup", "time": "15:00", "target": {"hr": [125, 145]}},
        {"repeat": 3, "steps": [
            {"type": "interval", "time": "10:00", "target": {"pace": ["4:30", "4:20"]}},
            {"type": "recovery", "time": "2:00"},
        ]},
        {"type": "cooldown", "lap": True},
    ],
}


def test_running_structure():
    w = build(RUN)
    steps = w["workoutSegments"][0]["workoutSteps"]
    assert [s["stepOrder"] for s in steps] == [1, 2, 5]
    rep = steps[1]
    assert rep["type"] == "RepeatGroupDTO" and rep["numberOfIterations"] == 3
    assert [c["stepOrder"] for c in rep["workoutSteps"]] == [3, 4]
    pace = rep["workoutSteps"][0]
    assert pace["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert pace["targetValueOne"] < pace["targetValueTwo"]  # slow < fast in m/s
    assert w["estimatedDurationInSecs"] == 15 * 60 + 3 * 12 * 60
    assert steps[2]["endCondition"]["conditionTypeKey"] == "lap.button"


def test_bike_power_and_swim():
    bike = {"date": "2026-11-28", "sport": "cycling", "name": "Z2",
            "steps": [{"type": "interval", "time": "2:00:00", "target": {"power": [170, 195]}}]}
    w = build(bike)
    s = w["workoutSegments"][0]["workoutSteps"][0]
    assert s["targetType"]["workoutTargetTypeId"] == 2 and s["endConditionValue"] == 7200

    swim = {"date": "2026-11-29", "sport": "swimming", "name": "CSS", "pool_length_m": 25,
            "steps": [{"repeat": 8, "steps": [
                {"type": "interval", "distance_m": 100},
                {"type": "rest", "time": "0:20"}]}]}
    w = build(swim)
    assert w["poolLength"] == 25.0


@pytest.mark.parametrize("bad", [
    {**RUN, "sport": "yoga"},
    {**RUN, "steps": [{"type": "interval", "time": "10:00", "distance_m": 100}]},
    {**RUN, "steps": [{"type": "interval", "time": "10:00", "target": {"pace": ["4:00", "4:30"]}}]},
    {**RUN, "steps": [{"type": "interval", "time": "10:00", "target": {"hr": [150, 220]}}]},
    {**RUN, "steps": [{"type": "interval", "time": "10:00", "target": {"power": [200, 220]}}]},
    {**RUN, "date": "26.11.2026"},
])
def test_rejects_bad_specs(bad):
    with pytest.raises(SpecError):
        build(bad)


def test_filename_must_match_date(tmp_path: Path):
    p = tmp_path / "2026-11-27.json"
    p.write_text(json.dumps(RUN))
    with pytest.raises(SpecError):
        load_spec(p)


def test_example_specs_valid():
    for p in Path("workouts").glob("*.json"):
        build(load_spec(p))
