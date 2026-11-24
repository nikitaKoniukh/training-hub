"""Upload approved workout specs to Garmin Connect and schedule them.

Usage: python -m hub.push_workout workouts/2026-11-26.json [...]
Idempotent: a spec whose content hash is already in logs/pushed.jsonl is skipped.
If the same date was pushed before with different content, the old workout is
unscheduled and deleted first, so the calendar never holds duplicates.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from hub.garmin_client import login, save_tokens
from hub.workout_builder import build, load_spec

LOG = Path("logs/pushed.jsonl")
TZ = ZoneInfo("Asia/Jerusalem")


def _log_entries() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()]


def main(paths: list[str]) -> int:
    paths = [p for p in paths if p.endswith(".json") and Path(p).exists()]
    if not paths:
        print("nothing to push")
        return 0
    entries = _log_entries()
    done = {e["sha"] for e in entries}

    api = login()
    rc = 0
    try:
        for p in paths:
            path = Path(p)
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()[:16]
            if sha in done:
                print(f"skip {p}: already pushed")
                continue
            spec = load_spec(path)
            workout = build(spec)

            for prev in [e for e in entries if e["date"] == spec["date"]]:
                try:
                    if prev.get("scheduleId"):
                        api.unschedule_workout(prev["scheduleId"])
                    api.delete_workout(prev["workoutId"])
                    print(f"removed previous workout {prev['workoutId']} for {spec['date']}")
                except Exception as exc:
                    print(f"warn: could not remove {prev.get('workoutId')}: {exc}")

            try:
                res = api.upload_workout(workout)
                wid = res["workoutId"]
                sched = api.schedule_workout(wid, spec["date"])
                sched_id = (sched or {}).get("workoutScheduleId")
            except Exception as exc:
                print(f"ERR {p}: {exc}")
                rc = 1
                continue

            entry = {"date": spec["date"], "file": p, "sha": sha, "workoutId": wid,
                     "scheduleId": sched_id, "name": workout["workoutName"],
                     "pushedAt": datetime.now(TZ).isoformat(timespec="seconds")}
            LOG.parent.mkdir(exist_ok=True)
            with LOG.open("a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            entries.append(entry)
            print(f"OK {p}: workout {wid} scheduled on {spec['date']}")
    finally:
        save_tokens(api)
    return rc


def cancel(day: str, api=None) -> str:
    """Remove every workout pushed for `day` from the Garmin calendar."""
    live = [e for e in _log_entries() if e["date"] == day and not e.get("cancelled")]
    cancelled_ids = {e.get("workoutId") for e in _log_entries() if e.get("cancelled")}
    live = [e for e in live if e.get("workoutId") not in cancelled_ids]
    if not live:
        return f"На {day} в календаре нет тренировки от тренера."
    own = api is None
    api = api or login()
    removed = []
    try:
        for e in live:
            try:
                if e.get("scheduleId"):
                    api.unschedule_workout(e["scheduleId"])
                api.delete_workout(e["workoutId"])
                removed.append(e)
            except Exception as exc:
                print(f"warn: cancel {e.get('workoutId')}: {exc}")
        with LOG.open("a") as f:
            for e in removed:
                f.write(json.dumps({"date": day, "workoutId": e["workoutId"], "cancelled": True,
                                    "at": datetime.now(TZ).isoformat(timespec="seconds")},
                                   ensure_ascii=False) + "\n")
    finally:
        if own:
            save_tokens(api)
    if not removed:
        return f"Не получилось удалить тренировку на {day} — удали её в Garmin Connect вручную."
    return f"Отменил на {day}: " + ", ".join(e.get("name", str(e["workoutId"])) for e in removed)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "cancel":
        print(cancel(sys.argv[2]))
        sys.exit(0)
    sys.exit(main(sys.argv[1:]))
