"""Compact workout spec -> Garmin Connect workout JSON.

Spec (workouts/YYYY-MM-DD.json), human-editable in a PR:

{
  "date": "2026-11-26",
  "sport": "running",                # running | cycling | swimming | cardio
  "name": "Tempo 3x10",
  "description": "optional text shown on the watch",
  "pool_length_m": 25,               # swimming only
  "steps": [
    {"type": "warmup", "time": "15:00", "target": {"hr": [125, 145]}},
    {"repeat": 3, "steps": [
      {"type": "interval", "time": "10:00", "target": {"pace": ["4:30", "4:20"]}},
      {"type": "recovery", "time": "2:00"}
    ]},
    {"type": "interval", "distance_m": 2000, "target": {"power": [200, 215]}},
    {"type": "cooldown", "lap": true}
  ]
}

Step: type = warmup | interval | recovery | rest | cooldown | other
End (exactly one): "time": "mm:ss" or "h:mm:ss" | "distance_m": int | "lap": true
Target (optional, one): hr [lo,hi] bpm | hr_zone N | pace ["slow","fast"] min/km |
  power [lo,hi] W | power_zone N | cadence [lo,hi] | speed_kmh [lo,hi]
Optional "note": shown as the step description.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

SPORTS = {
    "running": (1, "running"),
    "cycling": (2, "cycling"),
    "swimming": (4, "swimming"),
    "cardio": (6, "cardio_training"),
}
STEP_TYPES = {"warmup": 1, "cooldown": 2, "interval": 3, "recovery": 4,
              "rest": 5, "other": 7}
REPEAT_TYPE = {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6}
END_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}
END_DIST = {"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True}
END_LAP = {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True}
END_ITER = {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False}
NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
METER = {"unitId": 1, "unitKey": "meter", "factor": 100.0}

MAX_HR = 186  # sanity ceiling only; real max ~180


class SpecError(ValueError):
    pass


def parse_duration(s: str | int | float) -> int:
    if isinstance(s, (int, float)):
        return int(s)
    if not re.fullmatch(r"\d{1,2}(:\d{2}){1,2}", s.strip()):
        raise SpecError(f"bad time '{s}', use mm:ss or h:mm:ss")
    parts = [int(x) for x in s.split(":")]
    secs = 0
    for p in parts:
        secs = secs * 60 + p
    if secs <= 0:
        raise SpecError(f"time must be > 0: '{s}'")
    return secs


def pace_to_mps(p: str) -> float:
    if not re.fullmatch(r"\d{1,2}:\d{2}", p.strip()):
        raise SpecError(f"bad pace '{p}', use m:ss (per km)")
    m, s = (int(x) for x in p.split(":"))
    return 1000 / (m * 60 + s)


def _pair(v: Any, name: str) -> tuple[float, float]:
    if not (isinstance(v, list) and len(v) == 2):
        raise SpecError(f"{name} must be [low, high]")
    return v[0], v[1]


def build_target(t: dict | None, sport: str) -> dict:
    if not t:
        return {"targetType": NO_TARGET}
    if len(t) != 1:
        raise SpecError(f"one target per step, got {list(t)}")
    (kind, v), = t.items()

    def tt(i: int, key: str) -> dict:
        return {"workoutTargetTypeId": i, "workoutTargetTypeKey": key, "displayOrder": 1}

    if kind == "hr":
        lo, hi = _pair(v, "hr")
        if not (60 <= lo < hi <= MAX_HR):
            raise SpecError(f"hr range {v} out of bounds")
        return {"targetType": tt(4, "heart.rate.zone"), "targetValueOne": lo, "targetValueTwo": hi}
    if kind == "hr_zone":
        if v not in (1, 2, 3, 4, 5):
            raise SpecError("hr_zone must be 1..5")
        return {"targetType": tt(4, "heart.rate.zone"), "zoneNumber": v}
    if kind == "power":
        lo, hi = _pair(v, "power")
        if sport != "cycling":
            raise SpecError("power target only for cycling")
        if not (50 <= lo < hi <= 600):
            raise SpecError(f"power range {v} out of bounds")
        return {"targetType": tt(2, "power.zone"), "targetValueOne": lo, "targetValueTwo": hi}
    if kind == "power_zone":
        if v not in range(1, 8):
            raise SpecError("power_zone must be 1..7")
        return {"targetType": tt(2, "power.zone"), "zoneNumber": v}
    if kind == "pace":
        slow, fast = _pair(v, "pace")
        lo, hi = pace_to_mps(slow), pace_to_mps(fast)
        if lo >= hi:
            raise SpecError(f"pace must be [slower, faster], got {v}")
        return {"targetType": tt(6, "pace.zone"), "targetValueOne": round(lo, 4), "targetValueTwo": round(hi, 4)}
    if kind == "cadence":
        lo, hi = _pair(v, "cadence")
        return {"targetType": tt(3, "cadence"), "targetValueOne": lo, "targetValueTwo": hi}
    if kind == "speed_kmh":
        lo, hi = _pair(v, "speed_kmh")
        return {"targetType": tt(5, "speed.zone"),
                "targetValueOne": round(lo / 3.6, 4), "targetValueTwo": round(hi / 3.6, 4)}
    raise SpecError(f"unknown target '{kind}'")


class _Builder:
    def __init__(self, sport: str):
        self.sport = sport
        self.order = 0
        self.est = 0

    def step(self, s: dict, mult: int) -> dict:
        if "repeat" in s:
            n = s["repeat"]
            if not (isinstance(n, int) and 1 <= n <= 50):
                raise SpecError("repeat must be int 1..50")
            if not s.get("steps"):
                raise SpecError("repeat needs steps")
            self.order += 1
            group = {"type": "RepeatGroupDTO", "stepOrder": self.order, "stepType": REPEAT_TYPE,
                     "numberOfIterations": n, "smartRepeat": False,
                     "endCondition": END_ITER, "endConditionValue": float(n)}
            group["workoutSteps"] = [self.step(c, mult * n) for c in s["steps"]]
            return group

        kind = s.get("type")
        if kind not in STEP_TYPES:
            raise SpecError(f"step type must be one of {list(STEP_TYPES)}, got {kind!r}")
        ends = [k for k in ("time", "distance_m", "lap") if k in s]
        if len(ends) != 1:
            raise SpecError(f"step needs exactly one of time/distance_m/lap: {s}")
        self.order += 1
        out: dict[str, Any] = {
            "type": "ExecutableStepDTO", "stepOrder": self.order,
            "stepType": {"stepTypeId": STEP_TYPES[kind], "stepTypeKey": kind,
                         "displayOrder": STEP_TYPES[kind]},
        }
        if "time" in s:
            secs = parse_duration(s["time"])
            out |= {"endCondition": END_TIME, "endConditionValue": float(secs)}
            self.est += secs * mult
        elif "distance_m" in s:
            if self.sport in ("running", "cycling"):
                raise SpecError("бег и вело — шаги по времени (time), не distance_m")
            d = s["distance_m"]
            if not (isinstance(d, (int, float)) and 0 < d <= 300000):
                raise SpecError(f"bad distance_m {d}")
            out |= {"endCondition": END_DIST, "endConditionValue": float(d),
                    "preferredEndConditionUnit": {"unitKey": "meter" if self.sport == "swimming" else "kilometer"}}
            self.est += int(d / {"running": 3.3, "cycling": 8.0, "swimming": 0.65}.get(self.sport, 3)) * mult
        else:
            out |= {"endCondition": END_LAP}
        out |= build_target(s.get("target"), self.sport)
        if self.sport == "swimming":
            out["strokeType"] = {"strokeTypeId": 0, "displayOrder": 0}
            out["equipmentType"] = {"equipmentTypeId": 0, "displayOrder": 0}
        if s.get("note"):
            out["description"] = str(s["note"])[:512]
        return out


def build(spec: dict) -> dict:
    for key in ("date", "sport", "name", "steps"):
        if key not in spec:
            raise SpecError(f"missing '{key}'")
    try:
        date.fromisoformat(spec["date"])
    except ValueError as exc:
        raise SpecError(f"bad date {spec['date']!r}") from exc
    sport = spec["sport"]
    if sport not in SPORTS:
        raise SpecError(f"sport must be one of {list(SPORTS)}")
    if not spec["steps"]:
        raise SpecError("steps is empty")
    sid, skey = SPORTS[sport]
    sport_type = {"sportTypeId": sid, "sportTypeKey": skey, "displayOrder": sid}

    b = _Builder(sport)
    steps = [b.step(s, 1) for s in spec["steps"]]
    name = str(spec["name"])[:60]
    workout: dict[str, Any] = {
        "workoutName": name,
        "description": (spec.get("description") or "")[:1024] or None,
        "sportType": sport_type,
        "estimatedDurationInSecs": b.est,
        "workoutSegments": [{"segmentOrder": 1, "sportType": sport_type, "workoutSteps": steps}],
    }
    if sport == "swimming":
        workout["poolLength"] = float(spec.get("pool_length_m", 25))
        workout["poolLengthUnit"] = METER
    return {k: v for k, v in workout.items() if v is not None}


def describe(spec: dict) -> str:
    """One readable line per step, for Telegram / reports."""
    def tgt(t: dict | None) -> str:
        if not t:
            return ""
        (k, v), = t.items()
        return {
            "hr": lambda: f" HR {v[0]}–{v[1]}",
            "hr_zone": lambda: f" HR зона {v}",
            "pace": lambda: f" @ {v[0]}–{v[1]}/км",
            "power": lambda: f" {v[0]}–{v[1]} Вт",
            "power_zone": lambda: f" мощность зона {v}",
            "cadence": lambda: f" каденс {v[0]}–{v[1]}",
            "speed_kmh": lambda: f" {v[0]}–{v[1]} км/ч",
        }.get(k, lambda: "")()

    names = {"warmup": "разминка", "cooldown": "заминка", "interval": "", "recovery": "отдых",
             "rest": "пауза", "other": ""}

    def one(s: dict) -> str:
        if "time" in s:
            end = s["time"]
        elif "distance_m" in s:
            if self.sport in ("running", "cycling"):
                raise SpecError("бег и вело — шаги по времени (time), не distance_m")
            d = s["distance_m"]
            end = f"{d / 1000:g} км" if spec.get("sport") != "swimming" else f"{d} м"
        else:
            end = "до кнопки"
        label = names.get(s.get("type"), "")
        return f"{label + ' ' if label else ''}{end}{tgt(s.get('target'))}".strip()

    lines = []
    for s in spec["steps"]:
        if "repeat" in s:
            lines.append(f"{s['repeat']}× (" + " / ".join(one(c) for c in s["steps"]) + ")")
        else:
            lines.append(one(s))
    return "\n".join(f"• {l}" for l in lines)


def load_spec(path: Path) -> dict:
    spec = json.loads(path.read_text())
    if path.stem != spec.get("date"):
        raise SpecError(f"{path.name}: file name must equal date ({spec.get('date')})")
    return spec


if __name__ == "__main__":
    # Validate / preview: python -m hub.workout_builder workouts/2026-11-26.json
    rc = 0
    for arg in sys.argv[1:]:
        try:
            w = build(load_spec(Path(arg)))
            print(f"OK  {arg}: {w['workoutName']} (~{w['estimatedDurationInSecs'] // 60} min)")
        except (SpecError, json.JSONDecodeError) as exc:
            print(f"ERR {arg}: {exc}")
            rc = 1
    sys.exit(rc)
