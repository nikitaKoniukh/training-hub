"""Telegram bridge for the coach, run from GitHub Actions (no server).

  python -m hub.telegram send          # deliver outbox/*.json, move them to outbox/sent/
  python -m hub.telegram poll          # read new messages/buttons -> feedback.md, replies
  python -m hub.telegram alert "text"  # one-off message (workflow failures)

Outbox message (written by the coach and pushed):
  {"text": "plain text", "buttons_for": "2026-11-25"}   # buttons_for optional -> ОК / Легче / Отменить

The bot answers only the chat stored in bot/state.json. The first chat that sends
/start claims the bot (do it right after creating it).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Jerusalem")
STATE = Path("bot/state.json")
OUTBOX = Path("outbox")
SENT = OUTBOX / "sent"
FEEDBACK = Path("feedback.md")
MAX_LEN = 4000


def _token() -> str:
    tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not tok:
        sys.exit("TELEGRAM_BOT_TOKEN secret is not set")
    return tok


def api(method: str, **params) -> dict:
    data = urllib.parse.urlencode(
        {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
         for k, v in params.items() if v is not None}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{_token()}/{method}", data=data)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read())
            break
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500 and exc.code != 429:
                raise
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    if not res.get("ok"):
        raise RuntimeError(f"{method}: {res}")
    return res["result"]


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"chat_id": None, "offset": 0}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2) + "\n")


def now() -> datetime:
    return datetime.now(TZ)


def buttons(day: str) -> dict:
    return {"inline_keyboard": [[
        {"text": "ОК", "callback_data": f"ok:{day}"},
        {"text": "Легче", "callback_data": f"easier:{day}"},
        {"text": "Отменить", "callback_data": f"cancel:{day}"},
    ]]}


def say(st: dict, text: str, markup: dict | None = None) -> None:
    if not st.get("chat_id"):
        print("no chat_id yet — send /start to the bot")
        return
    for i in range(0, len(text), MAX_LEN):
        part = text[i:i + MAX_LEN]
        last = i + MAX_LEN >= len(text)
        api("sendMessage", chat_id=st["chat_id"], text=part,
            reply_markup=markup if last else None, disable_web_page_preview="true")


def add_feedback(line: str) -> None:
    stamp = now().strftime("%Y-%m-%d %H:%M")
    entry = f"- {stamp} — {line.strip()}"
    if not FEEDBACK.exists():
        FEEDBACK.write_text("# Самочувствие и заметки Никиты\n\nСвежие сверху.\n\n")
    lines = FEEDBACK.read_text().splitlines()
    idx = next((i for i, l in enumerate(lines) if l.startswith("- ")), len(lines))
    lines.insert(idx, entry)
    FEEDBACK.write_text("\n".join(lines) + "\n")


def next_coach_run() -> str:
    t = now()
    if t.hour < 5 or (t.hour == 5 and t.minute < 10):
        return "утренняя проверка в 05:10"
    if t.hour < 18 or (t.hour == 18 and t.minute < 45):
        return "вечерний тренер в 18:45"
    return "утренняя проверка в 05:10"


def workout_text(day: str) -> str:
    from hub.workout_builder import describe  # stdlib-only module
    p = Path("workouts") / f"{day}.json"
    if not p.exists():
        return f"{day}: тренировки на часах нет (отдых, силовая или план ещё не готов)."
    spec = json.loads(p.read_text())
    return f"{day}: {spec['name']}\n{describe(spec)}"


# ---------------------------------------------------------------- send

def cmd_send() -> int:
    st = load_state()
    files = sorted(p for p in OUTBOX.glob("*.json")) if OUTBOX.exists() else []
    if not files:
        print("outbox empty")
        return 0
    if not st.get("chat_id"):
        print("no chat_id yet; keeping outbox")
        return 0
    SENT.mkdir(parents=True, exist_ok=True)
    for p in files:
        msg = json.loads(p.read_text())
        day = msg.get("buttons_for")
        say(st, msg["text"], buttons(day) if day else None)
        p.rename(SENT / p.name)
        print(f"sent {p.name}")
    return 0


# ---------------------------------------------------------------- poll

HELP = ("Пиши сюда как тренеру: самочувствие, боль, «завтра только 40 минут» — всё попадёт в заметки,\n"
        "тренер учтёт в ближайший запуск.\n\n"
        "/today — тренировка на сегодня\n/tomorrow — на завтра\n/report — последний отчёт\n/help — эта справка")


def handle_message(st: dict, m: dict) -> None:
    chat = m["chat"]["id"]
    text = (m.get("text") or "").strip()
    if st.get("chat_id") is None and text.startswith("/start"):
        st["chat_id"] = chat
        say(st, "Привет! Я на связи с твоим тренером.\n\n" + HELP)
        return
    if chat != st.get("chat_id"):
        api("sendMessage", chat_id=chat, text="Это приватный бот.")
        return
    if not text:
        say(st, "Пока понимаю только текст.")
        return
    if text in ("/start", "/help"):
        say(st, HELP)
    elif text == "/today":
        say(st, workout_text(now().date().isoformat()))
    elif text == "/tomorrow":
        say(st, workout_text((now().date() + timedelta(days=1)).isoformat()))
    elif text == "/report":
        reps = sorted(Path("reports").glob("*.md"))
        say(st, reps[-1].read_text() if reps else "Отчётов пока нет.")
    elif text.startswith("/"):
        say(st, "Не знаю такой команды.\n\n" + HELP)
    else:
        add_feedback(text)
        say(st, f"Записал. Учтёт {next_coach_run()}.")


def handle_callback(st: dict, cb: dict) -> None:
    api("answerCallbackQuery", callback_query_id=cb["id"])
    if cb["message"]["chat"]["id"] != st.get("chat_id"):
        return
    action, _, day = (cb.get("data") or "").partition(":")
    msg = cb["message"]
    try:
        api("editMessageReplyMarkup", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
            reply_markup={"inline_keyboard": []})
    except Exception:
        pass
    if action == "ok":
        add_feedback(f"тренировка {day}: ОК")
        say(st, f"Принято, {day} — по плану.")
    elif action == "easier":
        add_feedback(f"тренировка {day}: попросил ЛЕГЧЕ")
        say(st, f"Записал «легче» на {day}. Сделает {next_coach_run()}.")
    elif action == "cancel":
        from hub.push_workout import cancel  # needs Garmin login
        res = cancel(day)
        add_feedback(f"тренировка {day}: ОТМЕНИЛ")
        say(st, res)


def cmd_poll() -> int:
    st = load_state()
    updates = api("getUpdates", offset=st.get("offset", 0), timeout=0,
                  allowed_updates=["message", "callback_query"])
    for u in updates:
        st["offset"] = u["update_id"] + 1
        try:
            if "message" in u:
                handle_message(st, u["message"])
            elif "callback_query" in u:
                handle_callback(st, u["callback_query"])
        except Exception as exc:
            print(f"error on update {u['update_id']}: {exc}")
        save_state(st)
    print(f"processed {len(updates)} updates")
    write_status(ok=True, lastPollUpdates=len(updates), offset=st.get("offset"))
    return 0


def cmd_alert(text: str) -> int:
    say(load_state(), text)
    return 0


def write_status(**kw) -> None:
    """bot/last_run.json — lets the coach (and Claude) see bot health without Actions logs."""
    STATE.parent.mkdir(exist_ok=True)
    path = STATE.parent / "last_run.json"
    cur = json.loads(path.read_text()) if path.exists() else {}
    cur.update(kw, at=now().isoformat(timespec="seconds"))
    path.write_text(json.dumps(cur, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if not os.getenv("TELEGRAM_BOT_TOKEN", "").strip():
        print("TELEGRAM_BOT_TOKEN not set — bot disabled, skipping")
        write_status(cmd=cmd, ok=False, error="TELEGRAM_BOT_TOKEN not set")
        sys.exit(0)
    try:
        me = api("getMe")
        hook = api("getWebhookInfo")
        write_status(cmd=cmd, bot=me.get("username"), webhook=hook.get("url") or None,
                     pending=hook.get("pending_update_count"), chat_id=load_state().get("chat_id"))
    except Exception as exc:
        write_status(cmd=cmd, ok=False, error=f"{type(exc).__name__}: {exc}"[:300])
        print(f"Telegram API error: {exc}")
        sys.exit(1)
    if cmd == "send":
        sys.exit(cmd_send())
    if cmd == "poll":
        sys.exit(cmd_poll())
    if cmd == "alert":
        sys.exit(cmd_alert(" ".join(sys.argv[2:])))
    sys.exit(__doc__)
