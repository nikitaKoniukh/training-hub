"""Watchdog: checks that the Claude coach runs actually produced their results.

  python -m hub.watchdog evening|morning|auto [--date YYYY-MM-DD] [--dry-run] [--force]

Independent of Claude: runs in GitHub Actions on a schedule and alerts in Telegram
(hub.telegram alert) when something is missing. Exit 0 always unless the check itself crashed,
so a missing coach result never looks like a red workflow you have to read.

evening (coach 18:45, check ~19:45):
  - reports/<today>.md exists
  - an evening message for today is in outbox/sent/
  - every workouts/<date>.json with date >= tomorrow is on the watch
    (its content hash is in logs/pushed.jsonl)
morning (coach 05:10, check ~05:35):
  - reports/<today>.md has a "## Утро" section
  - a morning message for today is in outbox/sent/
  - workouts/<today>.json (if any) is on the watch in its current version
both:
  - no messages stuck in outbox/ (not sent)

"auto" picks the mode by Asia/Jerusalem local time and does nothing outside the windows,
so the workflow can fire at two UTC times and stay correct across summer/winter time.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Jerusalem")
WINDOWS = {
    "morning": (time(5, 25), time(6, 30)),
    "evening": (time(19, 25), time(20, 30)),
}


def pick_mode(now: datetime) -> str | None:
    t = now.astimezone(TZ).time()
    for mode, (lo, hi) in WINDOWS.items():
        if lo <= t < hi:
            return mode
    return None


def pushed_hashes(root: Path) -> set[str]:
    log = root / "logs" / "pushed.jsonl"
    if not log.exists():
        return set()
    out = set()
    for line in log.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.add(json.loads(line)["sha"])
        except (ValueError, KeyError):
            continue
    return out


def spec_hash(p: Path) -> str:
    # must match hub.push_workout
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def workout_files(root: Path, start: date, end: date | None = None) -> list[Path]:
    out = []
    for p in sorted((root / "workouts").glob("*.json")):
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if d >= start and (end is None or d <= end):
            out.append(p)
    return out


def sent_for(root: Path, day: date, kind: str) -> bool:
    return any((root / "outbox" / "sent").glob(f"{day:%Y%m%d}-*-{kind}.json"))


def check(mode: str, today: date, root: Path = Path(".")) -> list[str]:
    problems: list[str] = []
    report = root / "reports" / f"{today}.md"
    done = pushed_hashes(root)

    if mode == "evening":
        if not report.exists():
            problems.append(f"нет отчёта reports/{today}.md — вечерний запуск тренера не отработал")
        if not sent_for(root, today, "evening"):
            problems.append("вечернее сообщение тренера не отправлено")
        tomorrow = today + timedelta(days=1)
        for p in workout_files(root, tomorrow):
            if spec_hash(p) not in done:
                problems.append(f"{p.stem}: тренировка не на часах (текущая версия не отправлена в Garmin)")
    elif mode == "morning":
        text = report.read_text() if report.exists() else ""
        if "## Утро" not in text:
            problems.append("нет утреннего раздела в отчёте — утренняя проверка не отработала")
        if not sent_for(root, today, "morning"):
            problems.append("утреннее сообщение не отправлено")
        for p in workout_files(root, today, today):
            if spec_hash(p) not in done:
                problems.append(f"{p.stem}: сегодняшняя тренировка не на часах в текущей версии")
    else:
        raise ValueError(f"unknown mode {mode}")

    stuck = sorted(p.name for p in (root / "outbox").glob("*.json"))
    if stuck:
        problems.append("застряли неотправленные сообщения: " + ", ".join(stuck))
    return problems


def format_alert(mode: str, problems: list[str], run_url: str = "") -> str:
    head = "⚠️ Watchdog (вечер)" if mode == "evening" else "⚠️ Watchdog (утро)"
    lines = [head + ": тренер отработал не полностью"] + [f"• {p}" for p in problems]
    if mode == "morning":
        lines.append("Делай тренировку по вечернему плану.")
    else:
        lines.append("Открой задачу «Тренер: план на завтра» в Claude и запусти вручную.")
    if run_url:
        lines.append(run_url)
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    import argparse
    import os
    ap = argparse.ArgumentParser(prog="hub.watchdog")
    ap.add_argument("mode", nargs="?", default="auto", choices=["auto", "evening", "morning"])
    ap.add_argument("--date", type=date.fromisoformat)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="auto: run even outside the windows")
    a = ap.parse_args(argv)

    now = datetime.now(TZ)
    mode = a.mode
    if mode == "auto":
        mode = pick_mode(now)
        if mode is None:
            if not a.force:
                print(f"{now:%H:%M} local — outside watchdog windows, nothing to do")
                return 0
            mode = "evening" if now.hour >= 12 else "morning"
    today = a.date or now.date()

    problems = check(mode, today)
    print(f"mode={mode} date={today} problems={len(problems)}")
    for p in problems:
        print("  -", p)
    if not problems:
        return 0

    url = ""
    if os.getenv("GITHUB_RUN_ID"):
        url = f"{os.getenv('GITHUB_SERVER_URL')}/{os.getenv('GITHUB_REPOSITORY')}/actions/runs/{os.getenv('GITHUB_RUN_ID')}"
    msg = format_alert(mode, problems, url)
    if a.dry_run:
        print(msg)
        return 0
    return subprocess.run([sys.executable, "-m", "hub.telegram", "alert", msg]).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
