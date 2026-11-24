"""Long-range series for the weekly review: data/history/<name>.json (last 12 weeks).

Range endpoints, ~10 Garmin calls total — cheap enough to run with every sync.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from hub.garmin_client import login, save_tokens

TZ = ZoneInfo("Asia/Jerusalem")
OUT = Path("data/history")
WEEKS = 12

ACT_FIELDS = ["activityId", "startTimeLocal", "duration", "distance", "averageHR",
              "averageSpeed", "activityTrainingLoad", "aerobicTrainingEffect",
              "anaerobicTrainingEffect", "avgPower", "normPower", "elevationGain"]


def _slim_acts(acts: list[dict]) -> list[dict]:
    out = []
    for a in acts or []:
        row = {k: a.get(k) for k in ACT_FIELDS if a.get(k) is not None}
        row["type"] = (a.get("activityType") or {}).get("typeKey")
        out.append(row)
    return out


def fetch(api, end: date) -> dict[str, Any]:
    start = end - timedelta(weeks=WEEKS)
    s, e = start.isoformat(), end.isoformat()
    calls = {
        "racePredictions": lambda: api.get_race_predictions(s, e, "daily"),
        "lactateThreshold": lambda: api.get_lactate_threshold(latest=False, start_date=s, end_date=e),
        "maxMetrics": lambda: api.get_max_metrics_range(s, e),
        "hrv": lambda: api.get_hrv_data_range(s, e),
        "rhr": lambda: api.get_rhr_daily(s, e),
        "sleep": lambda: api.get_sleep_daily(s, e),
        "bodyBattery": lambda: api.get_daily_body_battery_stats(s, e),
        "readiness": lambda: api.get_training_readiness_range(s, e),
        "activities": lambda: _slim_acts(api.get_activities_by_date(s, e)),
    }
    res: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name, fn in calls.items():
        try:
            res[name] = fn()
        except Exception as exc:
            errors[name] = f"{type(exc).__name__}: {exc}"[:300]
    res["_meta"] = {"start": s, "end": e, "syncedAt": datetime.now(TZ).isoformat(timespec="seconds"),
                    "errors": errors}
    return res


def main() -> int:
    end = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(TZ).date()
    api = login()
    try:
        res = fetch(api, end)
    finally:
        save_tokens(api)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, val in res.items():
        (OUT / f"{name.strip('_')}.json").write_text(json.dumps(val, ensure_ascii=False, indent=1) + "\n")
    print("history:", {k: (len(v) if isinstance(v, (list, dict)) else type(v).__name__) for k, v in res.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
