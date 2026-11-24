"""Training Hub: pull one day of Garmin data into data/daily/YYYY-MM-DD.json.

Usage: python -m hub.sync [YYYY-MM-DD] [--days N]
Default date = today in Asia/Jerusalem. The file for day D contains:
  - wellness of D: last night's sleep, overnight HRV, morning readiness, RHR
  - daily stats of D (steps, stress, body battery)
  - activities recorded on D (slim summary + laps for structured sessions)
  - training status / load as of D
Every Garmin call is isolated: one failing endpoint never kills the sync.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from hub.garmin_client import login, save_tokens

TZ = ZoneInfo("Asia/Jerusalem")
OUT_DIR = Path("data/daily")

ACTIVITY_FIELDS = [
    "activityId", "activityName", "startTimeLocal", "duration", "movingDuration",
    "distance", "elevationGain", "averageHR", "maxHR", "averageSpeed",
    "avgPower", "normPower", "maxPower", "averageRunningCadenceInStepsPerMinute",
    "averageBikingCadenceInRevPerMinute", "aerobicTrainingEffect",
    "anaerobicTrainingEffect", "trainingEffectLabel", "activityTrainingLoad",
    "calories", "avgStrokes", "poolLength", "averageSwimCadenceInStrokesPerMinute",
    "workoutId", "lapCount", "hrTimeInZone_1", "hrTimeInZone_2", "hrTimeInZone_3",
    "hrTimeInZone_4", "hrTimeInZone_5",
]
LAP_FIELDS = ["lapIndex", "duration", "distance", "averageHR", "maxHR",
              "averageSpeed", "averagePower", "elevationGain", "intensityType"]


def _safe(label: str, fn: Callable[[], Any], errors: dict[str, str]) -> Any:
    try:
        return fn()
    except Exception as exc:
        errors[label] = f"{type(exc).__name__}: {exc}"[:300]
        return None


def _pace(mps: float | None) -> str | None:
    if not mps:
        return None
    s = 1000 / mps
    return f"{int(s // 60)}:{int(s % 60):02d}/km"


def _slim_activity(api, a: dict, errors: dict) -> dict:
    out = {k: a.get(k) for k in ACTIVITY_FIELDS if a.get(k) is not None}
    out["type"] = (a.get("activityType") or {}).get("typeKey")
    if out.get("averageSpeed") and out["type"] and "run" in out["type"]:
        out["avgPace"] = _pace(out["averageSpeed"])
    if (a.get("lapCount") or 0) > 1:
        splits = _safe(f"splits_{a['activityId']}",
                       lambda: api.get_activity_splits(a["activityId"]), errors)
        laps = (splits or {}).get("lapDTOs") or []
        out["laps"] = [
            {**{k: l.get(k) for k in LAP_FIELDS if l.get(k) is not None},
             **({"pace": _pace(l.get("averageSpeed"))} if "run" in (out["type"] or "") else {})}
            for l in laps[:40]
        ]
    return out


def _pick_sleep(s: dict | None) -> dict | None:
    if not s:
        return None
    d = s.get("dailySleepDTO") or {}
    scores = d.get("sleepScores") or {}
    return {
        "sleepSeconds": d.get("sleepTimeSeconds"),
        "deep": d.get("deepSleepSeconds"), "light": d.get("lightSleepSeconds"),
        "rem": d.get("remSleepSeconds"), "awake": d.get("awakeSleepSeconds"),
        "start": d.get("sleepStartTimestampLocal"), "end": d.get("sleepEndTimestampLocal"),
        "score": (scores.get("overall") or {}).get("value"),
        "avgOvernightHrv": s.get("avgOvernightHrv"),
        "restingHeartRate": s.get("restingHeartRate"),
        "bodyBatteryChange": s.get("bodyBatteryChange"),
        "avgSleepStress": d.get("avgSleepStress"),
    }


def _pick_hrv(h: dict | None) -> dict | None:
    if not h:
        return None
    s = h.get("hrvSummary") or {}
    return {k: s.get(k) for k in ("lastNightAvg", "lastNight5MinHigh", "weeklyAvg",
                                  "status", "feedbackPhrase")} | {"baseline": s.get("baseline")}


def _pick_readiness(r: Any) -> dict | None:
    if isinstance(r, list):
        r = r[0] if r else None
    if not r:
        return None
    keys = ("score", "level", "feedbackShort", "sleepScore", "recoveryTime",
            "acuteLoad", "hrvWeeklyAverage", "hrvFactorPercent", "sleepHistoryFactorPercent",
            "stressHistoryFactorPercent", "acwrFactorPercent")
    return {k: r.get(k) for k in keys if k in r}


def _pick_stats(s: dict | None) -> dict | None:
    if not s:
        return None
    keys = ("totalSteps", "restingHeartRate", "averageStressLevel", "maxStressLevel",
            "bodyBatteryHighestValue", "bodyBatteryLowestValue",
            "bodyBatteryMostRecentValue", "moderateIntensityMinutes",
            "vigorousIntensityMinutes", "activeKilocalories")
    return {k: s.get(k) for k in keys if s.get(k) is not None}


def _pick_training_status(t: dict | None) -> dict | None:
    if not t:
        return None
    out: dict[str, Any] = {}
    vo2 = ((t.get("mostRecentVO2Max") or {}).get("generic") or {})
    out["vo2max"] = vo2.get("vo2MaxPreciseValue") or vo2.get("vo2MaxValue")
    for dev in ((t.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData") or {}).values():
        out["status"] = dev.get("trainingStatusFeedbackPhrase")
        acwr = dev.get("acuteTrainingLoadDTO") or {}
        out["acuteLoad"] = acwr.get("dailyTrainingLoadAcute")
        out["chronicLoad"] = acwr.get("dailyTrainingLoadChronic")
        out["acwr"] = acwr.get("dailyAcuteChronicWorkloadRatio")
        break
    bal = (t.get("mostRecentTrainingLoadBalance") or {}).get("metricsTrainingLoadBalanceDTOMap") or {}
    for dev in bal.values():
        out["loadBalance"] = {k: dev.get(k) for k in (
            "monthlyLoadAerobicLow", "monthlyLoadAerobicHigh", "monthlyLoadAnaerobic",
            "trainingBalanceFeedbackPhrase")}
        break
    return out


def sync_day(api, day: date) -> dict:
    ds = day.isoformat()
    errors: dict[str, str] = {}
    acts = _safe("activities", lambda: api.get_activities_by_date(ds, ds), errors) or []
    record = {
        "date": ds,
        "syncedAt": datetime.now(TZ).isoformat(timespec="seconds"),
        "sleep": _pick_sleep(_safe("sleep", lambda: api.get_sleep_data(ds), errors)),
        "hrv": _pick_hrv(_safe("hrv", lambda: api.get_hrv_data(ds), errors)),
        "readiness": _pick_readiness(_safe("readiness", lambda: api.get_training_readiness(ds), errors)),
        "stats": _pick_stats(_safe("stats", lambda: api.get_stats(ds), errors)),
        "trainingStatus": _pick_training_status(_safe("training_status", lambda: api.get_training_status(ds), errors)),
        "activities": [_slim_activity(api, a, errors) for a in acts],
    }
    if errors:
        record["errors"] = errors
    return record


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("date", nargs="?", help="YYYY-MM-DD, default today (Asia/Jerusalem)")
    p.add_argument("--days", type=int, default=2, help="also refresh N-1 previous days")
    args = p.parse_args()

    end = date.fromisoformat(args.date) if args.date else datetime.now(TZ).date()
    api = login()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        for i in range(args.days - 1, -1, -1):
            d = end - timedelta(days=i)
            rec = sync_day(api, d)
            (OUT_DIR / f"{d.isoformat()}.json").write_text(
                json.dumps(rec, ensure_ascii=False, indent=2) + "\n")
            print(f"{d}: {len(rec['activities'])} activities, errors={list(rec.get('errors', {}))}")
    finally:
        save_tokens(api)
    return 0


if __name__ == "__main__":
    sys.exit(main())
