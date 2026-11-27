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


def _trim(obj: Any, limit: int = 15000) -> Any:
    """Keep raw Garmin payloads but cap their size so data/ stays readable."""
    if obj is None:
        return None
    raw = json.dumps(obj, ensure_ascii=False)
    if len(raw) <= limit:
        return obj
    if isinstance(obj, list):
        return obj[-10:]
    return {"_truncated": True, "_head": raw[:limit]}


def sync_performance(api, day: date) -> dict:
    """Performance Stats screen: LT, race predictor, FTP, endurance, tolerance, economy, CSS."""
    ds = day.isoformat()
    start4w = (day - timedelta(days=28)).isoformat()
    errors: dict[str, str] = {}
    calls = {
        "lactateThreshold": lambda: api.get_lactate_threshold(latest=True),
        "racePredictions": lambda: api.get_race_predictions(),
        "maxMetrics": lambda: api.get_max_metrics(ds),
        "cyclingFtp": lambda: api.get_cycling_ftp(),
        "enduranceScore": lambda: api.get_endurance_score(start4w, ds),
        "runningTolerance": lambda: api.get_running_tolerance(start4w, ds),
        "runningEconomy": lambda: api.get_running_economy(ds),
        "criticalSwimSpeed": lambda: api.get_critical_swim_speed(ds),
        "hillScore": lambda: api.get_hill_score(start4w, ds),
    }
    out: dict[str, Any] = {"date": ds, "syncedAt": datetime.now(TZ).isoformat(timespec="seconds")}
    for key, fn in calls.items():
        out[key] = _trim(_safe(key, fn, errors))
    out["summary"] = summarize_performance(out)
    if errors:
        out["errors"] = errors
    return out


def _hms(sec: float | None) -> str | None:
    if not sec:
        return None
    sec = int(round(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def summarize_performance(p: dict) -> dict:
    """Human units, so the coach never has to guess Garmin's encodings."""
    s: dict[str, Any] = {}
    lt = ((p.get("lactateThreshold") or {}).get("speed_and_heart_rate") or {})
    if lt.get("speed"):
        mps = lt["speed"] * 10  # Garmin stores LT speed in 10 m/s units
        s["ltPace"] = _pace(mps).replace("/km", "") if mps else None
        s["ltPaceSec"] = round(1000 / mps, 1)
        s["ltHr"] = lt.get("heartRate")
        s["ltHrCycling"] = lt.get("heartRateCycling")
        s["ltDate"] = (lt.get("calendarDate") or "")[:10]
    rp = p.get("racePredictions") or {}
    if isinstance(rp, list):
        rp = rp[-1] if rp else {}
    for k, name in (("time5K", "5k"), ("time10K", "10k"), ("timeHalfMarathon", "half"), ("timeMarathon", "marathon")):
        if rp.get(k):
            s[f"predict_{name}"] = _hms(rp[k])
    mm = p.get("maxMetrics")
    mm = mm[0] if isinstance(mm, list) and mm else (mm or {})
    s["vo2max"] = ((mm.get("generic") or {}).get("vo2MaxPreciseValue"))
    if not s["vo2max"]:  # no new VO2 today -> last known value from 12-week history
        try:
            hist = json.loads(Path("data/history/maxMetrics.json").read_text())
            vals = [(r.get("generic") or {}) for r in hist if (r.get("generic") or {}).get("vo2MaxPreciseValue")]
            if vals:
                s["vo2max"] = vals[-1]["vo2MaxPreciseValue"]
                s["vo2maxDate"] = vals[-1].get("calendarDate")
        except Exception:
            pass
    ftp = p.get("cyclingFtp")
    ftp = ftp[-1] if isinstance(ftp, list) and ftp else (ftp or {})
    s["ftpCycling"] = ftp.get("functionalThresholdPower")
    rpw = (p.get("lactateThreshold") or {}).get("power") or {}
    s["ftpRunningPower"] = rpw.get("functionalThresholdPower")
    s["weightKg"] = rpw.get("weight")
    css = (p.get("criticalSwimSpeed") or {}).get("criticalSwimSpeed")
    if css:
        sec100 = 100 / (css / 1000)  # css in mm/s
        s["cssPer100m"] = f"{int(sec100 // 60)}:{int(sec100 % 60):02d}"
        s["cssDate"] = ((p.get("criticalSwimSpeed") or {}).get("measurementTimestampLocal") or "")[:10]
    tol = p.get("runningTolerance")
    if isinstance(tol, list) and tol:
        w = tol[-1]
        s["runTolerance"] = {"week": w.get("startOfWeek"), "impactLoad": w.get("totalImpactLoad"),
                             "tolerance": w.get("tolerance"),
                             "ratio": round(w["totalImpactLoad"] / w["tolerance"], 2) if w.get("tolerance") else None}
    es = p.get("enduranceScore") or {}
    if isinstance(es, dict) and es.get("avg"):
        s["enduranceScore4wAvg"] = es.get("avg")
    return {k: v for k, v in s.items() if v is not None}


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
        perf = sync_performance(api, end)
        perf_dir = Path("data/performance")
        perf_dir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(perf, ensure_ascii=False, indent=2) + "\n"
        (perf_dir / f"{end.isoformat()}.json").write_text(text)
        (perf_dir / "latest.json").write_text(text)
        print(f"performance: errors={list(perf.get('errors', {}))}")
    finally:
        save_tokens(api)
    return 0


if __name__ == "__main__":
    sys.exit(main())
