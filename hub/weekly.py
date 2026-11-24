"""Weekly review numbers + chart from data/history (12 weeks, weeks Sun–Sat).

  python -m hub.weekly            # data/weekly/summary.json (stdlib only)
  python -m hub.weekly --chart    # + charts/weekly.png (needs matplotlib)
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Jerusalem")
H = Path("data/history")
RUN = {"running", "trail_running", "treadmill_running", "track_running"}
BIKE = {"cycling", "road_biking", "indoor_cycling", "virtual_ride", "gravel_cycling", "mountain_biking"}
SWIM = {"lap_swimming", "open_water_swimming"}


def _load(name: str):
    p = H / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def week_start(d: date) -> date:
    return d - timedelta(days=(d.weekday() + 1) % 7)  # Sunday


def hms(sec: float | None) -> str | None:
    if not sec:
        return None
    sec = int(round(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def pace(sec_per_km: float | None) -> str | None:
    if not sec_per_km:
        return None
    return f"{int(sec_per_km // 60)}:{int(round(sec_per_km % 60)):02d}"


def series() -> dict:
    """Daily series: date -> value."""
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for r in _load("racePredictions") or []:
        if r.get("timeMarathon"):
            out["marathon"][r["calendarDate"]] = r["timeMarathon"]
    lt = _load("lactateThreshold") or {}
    for r in lt.get("speed") or []:
        if r.get("value"):
            out["ltPace"][r["from"]] = 1000 / (r["value"] * 10)
    for r in lt.get("heart_rate") or []:
        out["ltHr"][r["from"]] = r["value"]
    for r in _load("maxMetrics") or []:
        g = r.get("generic") or {}
        if g.get("vo2MaxPreciseValue"):
            out["vo2"][g["calendarDate"]] = g["vo2MaxPreciseValue"]
    hrv = (_load("hrv") or {}).get("hrvSummaries") or []
    for r in hrv:
        if r.get("lastNightAvg"):
            out["hrv"][r["calendarDate"]] = r["lastNightAvg"]
        b = r.get("baseline") or {}
        if b.get("balancedLow"):
            out["hrvLow"][r["calendarDate"]] = b["balancedLow"]
            out["hrvHigh"][r["calendarDate"]] = b["balancedUpper"]
    for r in _load("rhr") or []:
        if r.get("value"):
            out["rhr"][r["calendarDate"]] = r["value"]
    for r in _load("sleep") or []:
        v = r.get("values") or {}
        if v.get("totalSleepTimeInSeconds"):
            out["sleepH"][r["calendarDate"]] = v["totalSleepTimeInSeconds"] / 3600
        if v.get("sleepScore"):
            out["sleepScore"][r["calendarDate"]] = v["sleepScore"]
    for r in _load("readiness") or []:
        if r.get("score") is not None:
            out["readiness"].setdefault(r["calendarDate"], r["score"])
    return out


def _last_on_or_before(s: dict[str, float], d: date):
    keys = [k for k in s if k <= d.isoformat()]
    return s[max(keys)] if keys else None


def weekly() -> list[dict]:
    s = series()
    acts = _load("activities") or []
    plan = {w["start"]: w for w in json.loads(Path("plan/weeks.json").read_text())["weeks"]}
    agg: dict[date, dict] = defaultdict(lambda: defaultdict(float))
    for a in acts:
        d = datetime.fromisoformat(a["startTimeLocal"]).date()
        w = agg[week_start(d)]
        t = a.get("type")
        km = (a.get("distance") or 0) / 1000
        hrs = (a.get("duration") or 0) / 3600
        if t in RUN:
            w["runKm"] += km
            w["runH"] += hrs
            w["runs"] += 1
            if km >= w["longestRun"]:
                w["longestRun"] = km
        elif t in BIKE:
            w["bikeH"] += hrs
        elif t in SWIM:
            w["swimKm"] += km
        elif t == "strength_training":
            w["strength"] += 1
        w["load"] += a.get("activityTrainingLoad") or 0
        w["totalH"] += hrs

    today = datetime.now(TZ).date()
    first = week_start(today) - timedelta(weeks=11)
    rows = []
    for i in range(12):
        ws = first + timedelta(weeks=i)
        we = min(ws + timedelta(days=6), today)
        days = [(ws + timedelta(days=k)).isoformat() for k in range((we - ws).days + 1)]

        def avg(name):
            vals = [s[name][d] for d in days if d in s[name]]
            return round(mean(vals), 1) if vals else None

        a = agg.get(ws, {})
        p = plan.get(ws.isoformat())
        rows.append({
            "weekStart": ws.isoformat(), "planWeek": p["w"] if p else None,
            "partial": we < ws + timedelta(days=6),
            "runKm": round(a.get("runKm", 0), 1), "planKm": p["km"] if p else None,
            "runs": int(a.get("runs", 0)), "longestRunKm": round(a.get("longestRun", 0), 1),
            "bikeH": round(a.get("bikeH", 0), 1), "swimKm": round(a.get("swimKm", 0), 1),
            "strength": int(a.get("strength", 0)), "totalH": round(a.get("totalH", 0), 1),
            "load": round(a.get("load", 0)),
            "marathonPredict": hms(_last_on_or_before(s["marathon"], we)),
            "ltPace": pace(_last_on_or_before(s["ltPace"], we)),
            "ltHr": _last_on_or_before(s["ltHr"], we),
            "vo2max": _last_on_or_before(s["vo2"], we),
            "hrvAvg": avg("hrv"), "rhrAvg": avg("rhr"), "sleepH": avg("sleepH"),
            "sleepScore": avg("sleepScore"), "readiness": avg("readiness"),
        })
    return rows


# ------------------------------------------------------------------ chart

BLUE, GRID, INK, INK2, MUTED, SURF = "#2a78d6", "#e7e6e2", "#0b0b0b", "#52514e", "#8a8984", "#fcfcfb"


def chart(rows: list[dict], path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    s = series()
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "axes.edgecolor": GRID,
                         "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                         "axes.titlecolor": INK, "axes.titleweight": "bold", "axes.titlesize": 13,
                         "axes.titlelocation": "left", "figure.facecolor": SURF, "axes.facecolor": SURF})
    fig, ax = plt.subplots(4, 1, figsize=(8, 13), dpi=150)
    fig.subplots_adjust(hspace=0.55, left=0.12, right=0.95, top=0.95, bottom=0.04)

    def style(a):
        a.grid(axis="y", color=GRID, linewidth=0.8)
        a.set_axisbelow(True)
        for sp in ("top", "right", "left"):
            a.spines[sp].set_visible(False)
        a.tick_params(length=0)

    # 1. weekly run km vs plan
    a = ax[0]
    xs = [datetime.fromisoformat(r["weekStart"]).date() for r in rows]
    labels = [f"{d:%d.%m}" for d in xs]
    km = [r["runKm"] for r in rows]
    bars = a.bar(range(len(rows)), km, width=0.62, color=BLUE, zorder=2)
    for i, r in enumerate(rows):
        if r["partial"]:
            bars[i].set_alpha(0.45)
        if r["planKm"]:
            a.hlines(r["planKm"], i - 0.38, i + 0.38, color=INK, linewidth=2.2, zorder=3)
        if km[i] >= 8:
            a.text(i, km[i] - 1.5, f"{km[i]:.0f}", ha="center", va="top", fontsize=9, color="white",
                   fontweight="bold", zorder=4)
    a.set_xticks(range(len(rows)), labels, fontsize=9)
    a.set_title("Бег, км в неделю — черта: план, бледный: неделя идёт")
    style(a)

    # 2. marathon prediction
    a = ax[1]
    m = sorted(s["marathon"].items())
    if m:
        d = [datetime.fromisoformat(k) for k, _ in m]
        v = [x / 3600 for _, x in m]
        a.plot(d, v, color=BLUE, linewidth=2)
        a.axhline(3 - 1 / 3600, color=MUTED, linestyle=(0, (4, 3)), linewidth=1.2)
        a.text(datetime.fromisoformat(rows[0]["weekStart"]) + timedelta(days=4), 3 - 1 / 3600 + 0.004,
               "цель 2:59:59", va="bottom", fontsize=9, color=INK2)
        a.annotate(hms(m[-1][1]), (d[-1], v[-1]), textcoords="offset points", xytext=(-8, -16),
                   ha="right", fontsize=10, color=INK)
        a.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
            lambda y, _: f"{int(y)}:{int(round((y % 1) * 60)):02d}"))
    a.set_title("Прогноз марафона Garmin")
    style(a)

    # 3. LT pace (faster = up)
    a = ax[2]
    lt = sorted(s["ltPace"].items())
    if lt:
        d = [datetime.fromisoformat(k) for k, _ in lt] + [datetime.now()]
        v = [x for _, x in lt] + [lt[-1][1]]
        a.step(d, v, where="post", color=BLUE, linewidth=2)
        a.invert_yaxis()
        a.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda y, _: pace(y)))
        a.annotate(f"{pace(lt[-1][1])} @ {int(_last_on_or_before(s['ltHr'], date.today()) or 0)}",
                   (d[-1], v[-1]), textcoords="offset points", xytext=(-4, -14), ha="right", fontsize=10)
    a.set_title("Порог (LT), мин/км")
    style(a)

    # 4. HRV with baseline band + 7-day mean
    a = ax[3]
    h = sorted(s["hrv"].items())
    if h:
        d = [datetime.fromisoformat(k) for k, _ in h]
        v = [x for _, x in h]
        lo = [s["hrvLow"].get(k) for k, _ in h]
        hi = [s["hrvHigh"].get(k) for k, _ in h]
        if all(lo) and all(hi):
            a.fill_between(d, lo, hi, color=GRID, linewidth=0, label="норма (baseline)")
        a.scatter(d, v, s=10, color=BLUE, alpha=0.35, linewidths=0)
        roll = [mean(v[max(0, i - 6):i + 1]) for i in range(len(v))]
        a.plot(d, roll, color=BLUE, linewidth=2)
    a.set_title("HRV ночью, мс — линия: 7 дней, полоса: норма")
    style(a)

    x0 = datetime.fromisoformat(rows[0]["weekStart"])
    for a in ax[1:]:
        a.set_xlim(x0, datetime.now() + timedelta(days=2))
        a.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=SURF)


def main() -> int:
    rows = weekly()
    Path("data/weekly").mkdir(parents=True, exist_ok=True)
    Path("data/weekly/summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n")
    for r in rows[-4:]:
        print(r)
    if "--chart" in sys.argv:
        chart(rows, Path("charts/weekly.png"))
        print("chart: charts/weekly.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
