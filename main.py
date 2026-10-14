"""Процесс плагина «Неделя»: раз в неделю (по умолчанию понедельник 9:00 по местному времени машины; «пн старт» — при
первом запуске хаба в понедельник; меняется постом «расписание: …» в канале плагина — из вкладки «Неделя») собирает из
журнала хаба работу прошлой недели и отдаёт на рекап: analyst (нет его — хаб) пишет черновик JSON с Jira, Granola и
Teams, writer переписывает тексты по-человечески, плагин кладёт их в HTML дизайнера и шлёт владельцу.
Журнал только читает; пишет — фактами в ящик ядра. Своё — в <состояние хаба>/week-recap/.

Руками: python3 main.py --week 2026-W41 [--dry]   — собрать сводку недели (--dry — ничего не отправлять)
"""
import json
import os
import shutil
import sys
import time

from core import catalog, journal, spool, usage

import recap

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("HUB_STATE_DIR") or open(os.path.join(catalog.config_dir(), "state-dir")).read().strip()
NAME = os.environ.get("HUB_EXTENSION", "week-recap")
HOME = os.path.join(STATE_DIR, "week-recap")
OUT = os.path.join(HERE, "web", "data.json")   # виду — меняется пару раз в неделю, перезагрузка вкладки не мешает
EVERY = 60
WAIT = 6 * 3600          # черновик не пришёл за 6 ч — владельцу только цифры
WRITER_WAIT = 3 * 3600   # райтер не ответил за 3 ч — отчёт по черновику
TEMPLATE = os.path.join(HERE, "web", "report-template.html")
SAMPLE = os.path.join(HERE, "web", "sample.json")
REPORTS = os.path.join(HERE, "web", "reports")   # копия отчёта для ссылки со страницы хаба
KEEP = 12                # недель во вкладке
STARTED = time.time()    # когда поднялся процесс: «пн старт» — первый запуск хаба в понедельник
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


def where_of(state):
    """Сессия → проект (настройки хаба: projects, папка сессии внутри папки проекта) или её описание."""
    try:
        projects = (catalog.load_config(STATE_DIR) or {}).get("projects") or {}
    except Exception:   # noqa: BLE001 — без настроек — имена сессий
        projects = {}
    out = {}
    for name, s in ((state or {}).get("sessions") or {}).items():
        cwd = os.path.realpath(s.get("cwd") or "")
        hit = [p for p, v in projects.items() if isinstance(v, dict) and v.get("path")
               and cwd.startswith(os.path.realpath(os.path.expanduser(v["path"])))]
        out[name] = max(hit, key=len) if hit else name
    return out


def collect(label, zone, state_dir=STATE_DIR, home=HOME, state=None):
    """Сводки недели в файлы: (запись недели, путь к сводке)."""
    start, end, label = recap.week_of(label, zone)
    evs = recap.events(recap.journals(state_dir), start, end)
    m = recap.measure(evs)
    try:
        paid, _ = usage.spends(usage.facts(state_dir))
        spent = {}
        by_ts = {(x["session"], round(x["ts"], 3)): x["usd"] for x in paid}
        for e in evs:
            if e.get("type") == "turn.result":
                spent[e["seq"]] = by_ts.get((e.get("entity"), round(e.get("ts") or 0, 3)), 0.0)
    except Exception:   # noqa: BLE001 — без цен — одни токены
        spent = {}
    work = recap.items(evs, end, spent, where_of(state))
    folder = os.path.join(home, label)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "digest.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(recap.digest(label, start, end, m, zone))
    work_path = os.path.join(folder, "work.md")
    with open(work_path, "w", encoding="utf-8") as f:
        f.write(recap.work_digest(label, start, end, work, zone))
    return {"start": start, "end": end, "numbers": m["numbers"], "digest": path, "work": work_path,
            "draft": os.path.join(folder, "draft.json"), "final": os.path.join(folder, "recap.json"),
            "report": os.path.join(folder, "report.html"), "recap": os.path.join(folder, "recap.json")}, path


def period(w, zone):
    import datetime
    a, b = (datetime.datetime.fromtimestamp(t, zone) for t in (w["start"], w["end"] - 1))
    months = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря")
    return f"{a.day}–{b.day} {months[b.month - 1]}" if a.month == b.month else \
        f"{a.day} {months[a.month - 1]} – {b.day} {months[b.month - 1]}"


def start(label, zone, now, mine, state, send=put, state_dir=STATE_DIR, home=HOME):
    week, _ = collect(label, zone, state_dir, home, state)
    to = recap.reviewer(state)
    week.update(to=to, sent=now, period=period(week, zone))
    if to:
        send("message.received", None, {"to": to, "text": recap.prompt(label, week["period"], week["digest"], week["work"],
                                                                         week["draft"], SAMPLE),
                                        "channel": "session", "owner": False, "sender": NAME})
    else:
        week["sent"] = now - WAIT   # разбирать некому — сразу цифры
    mine["weeks"][label] = week


def fresh_json(path, since):
    """JSON рекапа, записанный не раньше since; нет или не похож — None."""
    try:
        if os.path.getmtime(path) < since - 1:
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if recap.valid(data) else None


def writer(state):
    w = ((state or {}).get("sessions") or {}).get("writer") or {}
    return "writer" if w.get("state") not in (None, "removed") else None


def publish(label, w, data, now, send=put):
    """Отчёт в HTML дизайнера, копия — для ссылки со страницы, владельцу — TL;DR и путь."""
    data = {"week": w.get("period") or label, **data}
    with open(TEMPLATE, encoding="utf-8") as f:
        html = recap.render(f.read(), data)
    with open(w["report"], "w", encoding="utf-8") as f:
        f.write(html)
    os.makedirs(REPORTS, exist_ok=True)
    shutil.copyfile(w["report"], os.path.join(REPORTS, f"{label}.html"))
    send("owner.notify", None, {"title": f"Неделя {data['week']}",
                                "text": f"{str(data.get('tldr') or '').strip()}\n\nЦеликом: `{w['report']}`",
                                "source": NAME, "channel": "cli", "owner": True})
    w.update(notified=now, text=str(data.get("tldr") or ""), data=data)


def numbers_text(n):
    return "\n".join(f"{recap.NAMES[k]}: {v if v is not None else '—'}" for k, v in n.items())


def finish(now, mine, state=None, send=put):
    """Черновик пришёл — райтеру; текст райтера пришёл (или он молчит WRITER_WAIT) — отчёт владельцу; черновика нет
    за WAIT — цифры. True — что-то поменялось."""
    changed = False
    for label, w in mine["weeks"].items():
        if w.get("notified"):
            continue
        if w.get("writing"):
            data = fresh_json(w["final"], w["writing"])
            if data or now - w["writing"] >= WRITER_WAIT:
                publish(label, w, data or w["drafted"], now, send)
                changed = True
            continue
        draft = fresh_json(w.get("draft", ""), w["sent"])
        if draft:
            w["drafted"] = draft
            to = writer(state)
            if to:
                w["writing"] = now
                send("message.received", None, {"to": to, "text": recap.writer_prompt(label, w["draft"], w["final"]),
                                                "channel": "session", "owner": False, "sender": NAME})
            else:
                publish(label, w, draft, now, send)
            changed = True
        elif now - w["sent"] >= WAIT:
            why = f"рекап от {w['to']} не пришёл" if w.get("to") else "разбирать некому (нет analyst и хаба)"
            send("owner.notify", None, {"title": f"Неделя {label}: цифры",
                                        "text": f"{why}; цифры недели:\n{numbers_text(w['numbers'])}\n\n"
                                                f"Сводка: `{w['digest']}`",
                                        "source": NAME, "channel": "cli", "owner": True})
            w["notified"] = now
            changed = True
    return changed


def view(mine):
    weeks = sorted(mine["weeks"].items(), reverse=True)[:KEEP]
    def stage(w):
        if w.get("data"):
            return "готов"
        if w.get("notified"):
            return "только цифры"
        return "тексты пишет writer" if w.get("writing") else f"разбирает {w.get('to') or '—'}"
    return {"weeks": [{"label": k, "start": w["start"], "end": w["end"], "numbers": w["numbers"], "to": w.get("to"),
                       "sent": w.get("sent"), "notified": w.get("notified"), "text": w.get("text"),
                       "tiles": (w.get("data") or {}).get("numbers") or [], "stage": stage(w),
                       "report": f"web/reports/{k}.html" if w.get("data") else None,
                       "recap": w.get("report") or w["recap"], "digest": w["digest"]} for k, w in weeks],
            "names": recap.NAMES, "schedule": recap.describe(mine.get("schedule") or recap.DEFAULT),
            "when": mine.get("schedule") or recap.DEFAULT}


def tick(now, mine, state, send=put, state_dir=STATE_DIR, home=HOME):
    """Один такт: True — своё состояние поменялось (записать и обновить вид)."""
    when = recap.schedule(state)
    changed = mine.get("schedule") != when
    mine["schedule"] = when
    zone = recap.tz(when["zone"])
    label = recap.due(now, zone, mine["weeks"], when, STARTED)
    if label:
        start(label, zone, now, mine, state, send, state_dir, home)
        changed = True
    return finish(now, mine, state, send) or changed


def main():
    mine_path = os.path.join(HOME, "state.json")
    mine = read(mine_path, {"weeks": {}})
    if "--week" in sys.argv:
        label = sys.argv[sys.argv.index("--week") + 1]
        zone = recap.tz((mine.get("schedule") or recap.DEFAULT)["zone"])
        if "--dry" in sys.argv:
            week, path = collect(label, zone, state=journal.load_state(STATE_DIR))
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
