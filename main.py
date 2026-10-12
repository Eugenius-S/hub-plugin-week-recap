"""Процесс плагина «Неделя»: раз в неделю (по умолчанию понедельник 9:00 по местному времени машины; меняется постом
владельца «расписание: вт 10:30 [пояс]» в канале плагина — из вкладки «Неделя») собирает из журнала хаба сводку прошлой недели и отдаёт её на разбор
(analyst, нет его — хабу); разбор пришёл — шлёт владельцу (Telegram, страница) и показывает во вкладке «Неделя».
Журнал только читает; пишет — фактами в ящик ядра. Своё — в <состояние хаба>/week-recap/.

Руками: python3 main.py --week 2026-W41 [--dry]   — собрать сводку недели (--dry — ничего не отправлять)
"""
import json
import os
import sys
import time

from core import catalog, journal, spool

import recap

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("HUB_STATE_DIR") or open(os.path.join(catalog.config_dir(), "state-dir")).read().strip()
NAME = os.environ.get("HUB_EXTENSION", "week-recap")
HOME = os.path.join(STATE_DIR, "week-recap")
OUT = os.path.join(HERE, "web", "data.json")   # виду — меняется пару раз в неделю, перезагрузка вкладки не мешает
EVERY = 60
WAIT = 6 * 3600          # разбор не пришёл за 6 ч — владельцу только цифры
KEEP = 12                # недель во вкладке
TELEGRAM = 3500          # длинный разбор — начало и путь к файлу


def read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def put(type_, entity, data):
    spool.put(os.path.join(STATE_DIR, "spool"), type_, entity, data, src=f"ext:{NAME}")


def collect(label, zone, state_dir=STATE_DIR, home=HOME):
    """Сводка недели в файл: (запись недели, путь к сводке)."""
    start, end, label = recap.week_of(label, zone)
    m = recap.measure(recap.events(recap.journals(state_dir), start, end))
    folder = os.path.join(home, label)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "digest.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(recap.digest(label, start, end, m, zone))
    return {"start": start, "end": end, "numbers": m["numbers"], "digest": path,
            "recap": os.path.join(folder, "recap.md")}, path


def start(label, zone, now, mine, state, send=put, state_dir=STATE_DIR, home=HOME):
    week, _ = collect(label, zone, state_dir, home)
    to = recap.reviewer(state)
    week.update(to=to, sent=now)
    if to:
        send("message.received", None, {"to": to, "text": recap.prompt(label, week["digest"], week["recap"]),
                                        "channel": "session", "owner": False, "sender": NAME})
    else:
        week["sent"] = now - WAIT   # разбирать некому — сразу цифры
    mine["weeks"][label] = week


def numbers_text(n):
    return "\n".join(f"{recap.NAMES[k]}: {v if v is not None else '—'}" for k, v in n.items())


def finish(now, mine, send=put):
    """Пришёл разбор — владельцу; не пришёл за WAIT — цифры. True — что-то поменялось."""
    changed = False
    for label, w in mine["weeks"].items():
        if w.get("notified"):
            continue
        text = ""
        try:
            if os.path.getmtime(w["recap"]) >= w["sent"] - 1:
                with open(w["recap"], encoding="utf-8") as f:
                    text = f.read().strip()
        except OSError:
            pass
        if text:
            body = text if len(text) <= TELEGRAM else text[:TELEGRAM].rsplit("\n", 1)[0] + "\n…"
            send("owner.notify", None, {"title": f"Неделя {label}", "text": f"{body}\n\nЦеликом: `{w['recap']}`",
                                        "source": NAME, "channel": "cli", "owner": True})
            w.update(notified=now, text=text)
        elif now - w["sent"] >= WAIT:
            why = f"разбор от {w['to']} не пришёл" if w.get("to") else "разбирать некому (нет analyst и хаба)"
            send("owner.notify", None, {"title": f"Неделя {label}: цифры",
                                        "text": f"{why}; цифры недели:\n{numbers_text(w['numbers'])}\n\n"
                                                f"Сводка: `{w['digest']}`",
                                        "source": NAME, "channel": "cli", "owner": True})
            w["notified"] = now
        else:
            continue
        changed = True
    return changed


def view(mine):
    weeks = sorted(mine["weeks"].items(), reverse=True)[:KEEP]
    return {"weeks": [{"label": k, "start": w["start"], "end": w["end"], "numbers": w["numbers"], "to": w.get("to"),
                       "sent": w.get("sent"), "notified": w.get("notified"), "text": w.get("text"),
                       "recap": w["recap"], "digest": w["digest"]} for k, w in weeks],
            "names": recap.NAMES, "schedule": recap.describe(mine.get("schedule") or recap.DEFAULT),
            "when": mine.get("schedule") or recap.DEFAULT}


def tick(now, mine, state, send=put, state_dir=STATE_DIR, home=HOME):
    """Один такт: True — своё состояние поменялось (записать и обновить вид)."""
    when = recap.schedule(state)
    changed = mine.get("schedule") != when
    mine["schedule"] = when
    zone = recap.tz(when["zone"])
    label = recap.due(now, zone, mine["weeks"], when)
    if label:
        start(label, zone, now, mine, state, send, state_dir, home)
        changed = True
    return finish(now, mine, send) or changed


def main():
    mine_path = os.path.join(HOME, "state.json")
    mine = read(mine_path, {"weeks": {}})
    if "--week" in sys.argv:
        label = sys.argv[sys.argv.index("--week") + 1]
        zone = recap.tz((mine.get("schedule") or recap.DEFAULT)["zone"])
        if "--dry" in sys.argv:
            week, path = collect(label, zone)
            print(numbers_text(week["numbers"]) + f"\n\nСводка: {path}")
            return
        start(label, zone, time.time(), mine, journal.load_state(STATE_DIR))
        write(mine_path, mine)
        write(OUT, view(mine))
        print(f"отдано на разбор: {mine['weeks'][label].get('to')}; сводка: {mine['weeks'][label]['digest']}")
        return
    write(OUT, view(mine))
    while True:
        mine = read(mine_path, mine)   # разбор руками (--week) пишет тот же файл: не отправить неделю второй раз
        try:
            if tick(time.time(), mine, journal.load_state(STATE_DIR)):
                write(mine_path, mine)
                write(OUT, view(mine))
        except Exception as e:   # noqa: BLE001 — сбой такта не роняет процесс; хаб поднимет упавший, но зря
            print(f"week-recap: {e!r}", file=sys.stderr, flush=True)
        time.sleep(EVERY)


if __name__ == "__main__":
    main()
