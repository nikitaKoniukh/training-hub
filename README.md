# Training Hub

Garmin → Hub → Тренер (Claude) → твоё подтверждение → GitHub Actions → часы.

```
05:10  Claude (утро)     синк новой ночи → ОК или замена тренировки на лёгкую → пуш к 05:20
18:20  sync.yml          Garmin Connect → data/daily/YYYY-MM-DD.json (сон, HRV, RHR, readiness, активности, нагрузка)
19:00  Claude (задача)   анализ → reports/YYYY-MM-DD.md + workouts/<завтра>.json → PR + пуш
 ≤21:00  ты              мерджишь PR в приложении GitHub (= подтверждение) или пишешь комментарий
сразу  push-workout.yml  тренировка загружается в Garmin Connect и ставится в календарь на завтра
05:00+ часы              тренировка на Fenix после синхронизации
```

## Структура

| Путь | Что |
|---|---|
| `hub/sync.py` | сбор данных за день (`python -m hub.sync [дата] --days N`) |
| `hub/workout_builder.py` | компактный JSON → формат Garmin; валидация: `python -m hub.workout_builder workouts/*.json` |
| `hub/push_workout.py` | загрузка + планирование, идемпотентно, лог в `logs/pushed.jsonl` |
| `coach/COACH.md` | инструкция вечернего тренера: правила адаптации, формат отчёта |
| `coach/MORNING.md` | утренняя проверка 05:10 |
| `coach/auto_approve` | есть файл → тренировка уходит на часы без PR |
| `feedback.md` | самочувствие и заметки |
| `plan/current.md` | текущий план (Tiberias 08.01.2027, 2:59:59) — меняй свободно |
| `data/daily/` | данные Hub |
| `reports/` | ежедневные отчёты |
| `workouts/` | подтверждённые тренировки |

## Настройка (один раз, ~5 минут)

1. **Токены Garmin** — локально, т.к. нужен MFA:
   ```bash
   git clone https://github.com/nikitaKoniukh/training-hub && cd training-hub
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python scripts/bootstrap_login.py --set-secret nikitaKoniukh/training-hub
   ```
   Пароль используется один раз и нигде не сохраняется; в секрет `GARMIN_TOKENS` попадают только OAuth-токены.

2. **Ротация токенов** — Garmin периодически обновляет токены, workflow сохраняет новые обратно в секрет.
   Создай fine-grained PAT: Settings → Developer settings → Fine-grained tokens →
   Repository access: только `training-hub`, Permissions → **Secrets: Read and write**.
   Сохрани его в секрет репозитория `GH_PAT`.
   Без него всё работает, пока не истечёт refresh-токен, потом придётся повторить шаг 1.

3. **Проверка**: Actions → *Hub sync* → Run workflow. Должен появиться коммит с `data/daily/<сегодня>.json`.

## Ручное управление

- Отправить тренировку без PR: Actions → *Push workout to Garmin* → `workouts/2026-11-26.json`.
- Поправить тренировку: редактируй JSON прямо в PR — *Validate workout spec* проверит его до мерджа.
- Повторный пуш на ту же дату заменяет старую тренировку в календаре (старая удаляется).

## Ограничения

Garmin не даёт публичного API для частных лиц; используется `python-garminconnect` (мобильный SSO-флоу).
Если Garmin снова поменяет авторизацию, упадёт `sync`/`push` — обнови библиотеку (`requirements.txt`).
Cron в Actions идёт по UTC: летом (IDT) синк будет в 19:20 — сдвинь cron на `20 15 * * *` в конце марта.
