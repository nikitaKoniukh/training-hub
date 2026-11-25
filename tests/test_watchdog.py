import hashlib
import json
from datetime import date, datetime
from pathlib import Path

from hub.watchdog import TZ, check, format_alert, pick_mode

TODAY = date(2026, 11, 25)


def make_repo(tmp: Path, *, report="## Утро 05:10\nok\n\n## Вечер\nok\n", sent=("evening", "morning"),
              workouts=None, pushed=None, stuck=()):
    for d in ("reports", "outbox/sent", "workouts", "logs"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    if report is not None:
        (tmp / "reports" / f"{TODAY}.md").write_text(report)
    for kind in sent:
        (tmp / "outbox" / "sent" / f"{TODAY:%Y%m%d}-0511-{kind}.json").write_text("{}")
    for name in stuck:
        (tmp / "outbox" / name).write_text("{}")
    lines = []
    for day, body in (workouts or {}).items():
        p = tmp / "workouts" / f"{day}.json"
        p.write_text(body)
        if pushed is None or day in pushed:
            sha = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
            lines.append(json.dumps({"date": day, "sha": sha}))
    (tmp / "logs" / "pushed.jsonl").write_text("\n".join(lines) + "\n")
    return tmp


def test_evening_ok(tmp_path):
    root = make_repo(tmp_path, workouts={"2026-11-26": "a", "2026-11-27": "b", "2026-11-25": "old"})
    assert check("evening", TODAY, root) == []


def test_evening_missing_everything(tmp_path):
    root = make_repo(tmp_path, report=None, sent=(), workouts={"2026-11-26": "a"}, pushed=[])
    probs = check("evening", TODAY, root)
    assert len(probs) == 3
    assert any("отчёта" in p for p in probs)
    assert any("2026-11-26" in p for p in probs)


def test_evening_future_day_not_pushed(tmp_path):
    root = make_repo(tmp_path, workouts={"2026-11-26": "a", "2026-11-29": "c"}, pushed=["2026-11-26"])
    probs = check("evening", TODAY, root)
    assert probs == ["2026-11-29: тренировка не на часах (текущая версия не отправлена в Garmin)"]


def test_edited_after_push_is_flagged(tmp_path):
    root = make_repo(tmp_path, workouts={"2026-11-26": "a"})
    (root / "workouts" / "2026-11-26.json").write_text("changed")
    assert len(check("evening", TODAY, root)) == 1


def test_morning(tmp_path):
    root = make_repo(tmp_path, report="## Вечер\n", sent=("evening",), workouts={"2026-11-25": "x"})
    probs = check("morning", TODAY, root)
    assert len(probs) == 2
    root2 = make_repo(tmp_path / "ok", workouts={"2026-11-25": "x"})
    assert check("morning", TODAY, root2) == []


def test_stuck_outbox(tmp_path):
    root = make_repo(tmp_path, stuck=("20261125-1846-evening.json",))
    assert any("застряли" in p for p in check("evening", TODAY, root))


def test_pick_mode_dst():
    # winter (UTC+2) and summer (UTC+3): only one of the two UTC crons lands in a window
    utc = lambda s: datetime.fromisoformat(s + "+00:00")
    assert pick_mode(utc("2026-11-26T03:35")) == "morning"
    assert pick_mode(utc("2026-11-26T02:35")) is None
    assert pick_mode(utc("2026-07-01T02:35")) == "morning"
    assert pick_mode(utc("2026-07-01T03:35")) is None
    assert pick_mode(utc("2026-11-26T17:45")) == "evening"
    assert pick_mode(utc("2026-11-26T16:45")) is None
    assert pick_mode(utc("2026-07-01T16:45")) == "evening"
    assert pick_mode(utc("2026-07-01T17:45")) is None


def test_alert_text():
    msg = format_alert("morning", ["x"])
    assert "по вечернему плану" in msg and "• x" in msg
