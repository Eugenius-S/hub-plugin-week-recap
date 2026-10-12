"""Разбор недели: что сделано, сколько это стоило владельцу, что улучшить. Чистые функции — без ящика и времени."""
import datetime
import glob
import json
import os
import re
import statistics
import zoneinfo

OWNER_CHANNELS = ("web", "telegram", "cli")
FORM = "AskUserQuestion"
CUT = 400                  # сколько знаков текста брать в сводку для разбора


DAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
DEFAULT = {"day": 0, "hour": 9, "minute": 0, "zone": None}   # понедельник 9:00, местное время машины
SET = re.compile(r"^\s*расписание:\s*(пн|вт|ср|чт|пт|сб|вс)\s+(\d{1,2}):(\d{2})(?:\s+(\S+))?\s*$", re.I)


def local_zone():
    """Пояс машины: по ссылке /etc/localtime (с переходом на летнее время); нет её (Windows) — нынешнее смещение."""
    path = os.path.realpath("/etc/localtime")
    if "zoneinfo/" in path:
        try:
            return zoneinfo.ZoneInfo(path.split("zoneinfo/", 1)[1])
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            pass
    return datetime.datetime.now().astimezone().tzinfo


def known(name):
    try:
        zoneinfo.ZoneInfo(name)
        return True
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return False


def tz(name):
    return zoneinfo.ZoneInfo(name) if name and known(name) else local_zone()


def schedule(state):
    """Когда разбирать: последний верный пост «расписание: пн 09:00 [пояс]» в канале плагина от владельца или хаба."""
    hubs = {n for n, s in ((state or {}).get("sessions") or {}).items() if s.get("kind") == "hub"}
    for post in reversed(((state or {}).get("feeds") or {}).get("week-recap") or []):
        m = SET.match(str(post.get("text") or ""))
        if not m or (post.get("by") != "owner" and post.get("by") not in hubs):
            continue
        day, hour, minute, zone = DAYS.index(m[1].lower()), int(m[2]), int(m[3]), m[4]
        if hour < 24 and minute < 60 and (not zone or known(zone)):
            return {"day": day, "hour": hour, "minute": minute, "zone": zone}
    return dict(DEFAULT)


def describe(when):
    return f"{DAYS[when['day']]} {when['hour']:02d}:{when['minute']:02d}, {when['zone'] or 'местное время'}"


def last_week(now, zone):
    """Прошлая полная неделя от now: (начало, конец, метка вида 2026-W41) — понедельник 0:00 .. понедельник 0:00."""
    local = datetime.datetime.fromtimestamp(now, zone)
    monday = (local - datetime.timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start = monday - datetime.timedelta(days=7)
    year, week, _ = start.isocalendar()
    return start.timestamp(), monday.timestamp(), f"{year}-W{week:02d}"


def week_of(label, zone):
    """Метка 2026-W41 → (начало, конец, метка)."""
    year, week = label.split("-W")
    start = datetime.datetime.fromisocalendar(int(year), int(week), 1).replace(tzinfo=zone)
    return start.timestamp(), (start + datetime.timedelta(days=7)).timestamp(), label


def due(now, zone, done, when=DEFAULT):
    """Пора разбирать прошлую неделю: на этой неделе наступили день и время из расписания, а её ещё не разбирали. Хаб
    лежал в это время — разберём при первом такте после. Метка недели — или None."""
    local = datetime.datetime.fromtimestamp(now, zone)
    monday = (local - datetime.timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    if local < monday + datetime.timedelta(days=when["day"], hours=when["hour"], minutes=when["minute"]):
        return None
    label = last_week(now, zone)[2]
    return None if label in done else label


def journals(state_dir):
    """Архив и текущий журнал: старые журналы целиком лежат в journal-archive (ротация)."""
    return sorted(glob.glob(os.path.join(state_dir, "journal-archive", "journal-*.jsonl"))) + \
        [os.path.join(state_dir, "journal.jsonl")]


def events(paths, start, end):
    """События с ts в [start, end) из всех журналов, без повторов (архивы перекрываются), по порядку."""
    out = {}
    for path in paths:
        try:
            f = open(path, "rb")
        except OSError:
            continue
        with f:
            for line in f:
                if b'"ts"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                ts, seq = e.get("ts") or 0, e.get("seq")
                if start <= ts < end and seq is not None and e.get("type") != "core.snapshot":
                    out[seq] = e
    return [out[s] for s in sorted(out)]


def _owner(d):
    return d.get("owner") is True and d.get("channel") in OWNER_CHANNELS


def _median_min(xs):
    return round(statistics.median(xs) / 60, 1) if xs else None


def measure(evs):
    """Цифры недели и материал для разбора: сообщения владельца, задачи, формы, ходы по сессиям."""
    msgs, answers, forms, perms, tasks, files = [], [], {}, 0, {}, 0
    sessions, results = {}, {}
    for e in evs:
        t, d, ent, ts = e.get("type"), e.get("data") or {}, e.get("entity"), e.get("ts")
        if t == "message.received" and _owner(d) and d.get("to"):
            text = str(d.get("text") or "")
            (answers if text.startswith("Ответ владельца на вопрос") else msgs).append(
                {"id": f"m{e['seq']}", "ts": ts, "to": d["to"], "text": text})
        elif t == "permission.requested" and d.get("tool") == FORM:
            qs = (d.get("input") or {}).get("questions") or []
            forms[d.get("request_id")] = {"id": f"q{e['seq']}", "ts": ts, "session": ent, "answered": None,
                                          "question": " / ".join(str(q.get("question") or "") for q in qs)}
        elif t == "permission.requested":
            perms += 1
        elif t == "permission.answered" and d.get("request_id") in forms and _owner(d):
            f = forms[d["request_id"]]
            f["answered"], f["answer"] = ts, " / ".join(str(v) for v in (d.get("answers") or {}).values())
        elif t == "delegation.created":
            tasks[f"d{e['seq']}"] = {"id": f"d{e['seq']}", "ts": ts, "session": d.get("session"), "for": d.get("for"),
                                     "context": str(d.get("context") or ""), "done": None}
        elif t == "delegation.done" and ent in tasks:
            tasks[ent]["done"] = ts
        elif t == "owner.file":
            files += 1
        elif t == "turn.result" and ent:
            s = sessions.setdefault(ent, {"turns": 0, "busy_min": 0.0, "owner_msgs": 0})
            s["turns"] += 1
            s["busy_min"] += (d.get("duration_ms") or 0) / 60000
            if str(d.get("text") or "").strip():
                results.setdefault(ent, []).append(ts)
    waits = []
    for m in msgs:
        sessions.setdefault(m["to"], {"turns": 0, "busy_min": 0.0, "owner_msgs": 0})["owner_msgs"] += 1
        after = next((r for r in results.get(m["to"], []) if r > m["ts"]), None)
        m["wait_min"] = round((after - m["ts"]) / 60, 1) if after else None
        if after:
            waits.append(after - m["ts"])
    answered = [f for f in forms.values() if f["answered"]]
    asked = {m["id"]: m["ts"] for m in msgs}   # от просьбы владельца до сдачи задачи, поставленной по ней
    closed = [x["done"] - asked[x["for"]] for x in tasks.values() if x["done"] and x["for"] in asked]
    numbers = {
        "owner_messages": len(msgs),
        "owner_chars": sum(len(m["text"]) for m in msgs),
        "forms": len(forms),
        "forms_answered": len(answered),
        "form_answer_median_min": _median_min([f["answered"] - f["ts"] for f in answered]),
        "permission_requests": perms,
        "tasks": len(tasks),
        "tasks_done": sum(1 for x in tasks.values() if x["done"]),
        "wait_median_min": _median_min(waits),
        "task_median_min": _median_min(closed),
        "files_sent": files,
        "turns": sum(s["turns"] for s in sessions.values()),
        "busy_hours": round(sum(s["busy_min"] for s in sessions.values()) / 60, 1),
    }
    for s in sessions.values():
        s["busy_min"] = round(s["busy_min"])
    return {"numbers": numbers, "sessions": sessions, "messages": msgs, "answers": answers,
            "forms": list(forms.values()), "tasks": list(tasks.values())}


def _at(ts, zone):
    return datetime.datetime.fromtimestamp(ts, zone).strftime("%a %d.%m %H:%M")


def _cut(text, n=CUT):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + "…"


NAMES = {
    "owner_messages": "сообщений владельца", "owner_chars": "знаков в них", "forms": "вопросов формой",
    "forms_answered": "из них отвечено", "form_answer_median_min": "медиана ответа на форму, мин",
    "permission_requests": "запросов разрешений", "tasks": "задач хаба (dN)", "tasks_done": "из них сдано",
    "wait_median_min": "медиана до первого ответа сессии, мин",
    "task_median_min": "медиана от просьбы до сдачи задачи, мин", "files_sent": "файлов владельцу",
    "turns": "ходов сессий", "busy_hours": "часов работы сессий",
}


def digest(label, start, end, m, zone):
    """Сводка недели для разбора (markdown): цифры, сессии, затем по времени — слова владельца, задачи, формы."""
    lines = [f"# Неделя {label}: {_at(start, zone)} — {_at(end - 1, zone)}", "", "## Цифры"]
    lines += [f"- {NAMES[k]}: {v if v is not None else '—'}" for k, v in m["numbers"].items()]
    lines += ["", "## Сессии (сообщений владельца · ходов · минут работы)"]
    for name, s in sorted(m["sessions"].items(), key=lambda x: (-x[1]["owner_msgs"], -x[1]["turns"])):
        lines.append(f"- {name}: {s['owner_msgs']} · {s['turns']} · {s['busy_min']}")
    timeline = [(x["ts"], f"владелец → {x['to']} ({x['id']}, ответ через "
                 f"{x['wait_min'] if x['wait_min'] is not None else '—'} мин): {_cut(x['text'])}") for x in m["messages"]]
    timeline += [(x["ts"], f"задача {x['id']} → {x['session']} (по {x['for'] or '—'}): {_cut(x['context'], 250)}"
                  + (f" — сдана {_at(x['done'], zone)}" if x["done"] else " — не сдана")) for x in m["tasks"]]
    timeline += [(x["ts"], f"форма {x['id']} от {x['session']}: {_cut(x['question'], 250)} → "
                  + (f"ответ через {round((x['answered'] - x['ts']) / 60)} мин: {_cut(x.get('answer', ''), 200)}"
                     if x["answered"] else "без ответа")) for x in m["forms"]]
    lines += ["", "## По времени"]
    lines += [f"- {_at(ts, zone)} · {text}" for ts, text in sorted(timeline, key=lambda x: x[0])]
    return "\n".join(lines) + "\n"


def prompt(label, digest_path, recap_path):
    """Поручение тому, кто разбирает неделю (analyst, нет его — хаб)."""
    return (
        f"[week-recap] Недельный разбор {label} для владельца. Сводка из журнала хаба: `{digest_path}`. "
        f"Запиши разбор в `{recap_path}` — плагин сам отправит его владельцу; итог хода — одна строка «готово». "
        "Рамка — вся работа владельца с хабом за неделю: все проекты, агенты и сессии из сводки, не только твоя область.\n"
        "1. Задачи владельца: склей его сообщения, задачи dN и шаги агентов в его человеческие задачи (одна цель "
        "владельца — одна задача, не ход агента). По каждой: что, где (проект, агент), итог — сделано, в работе, "
        "брошено; с первого раза или с переделками (владелец вернул результат: «не то», «опять», правки).\n"
        "2. Jira: задачи, закрытые за эту неделю, — через MCP atlassian, только чтение; нет доступа — "
        "`hub tools request atlassian \"недельный разбор\"`, а в разборе одна строка, что Jira не вошла. Сопоставь их "
        "с задачами из п. 1.\n"
        "3. Эффективность связки владелец + хаб: доля задач с первого раза; сколько ушло времени владельца "
        "(сообщения, формы, переделки); сколько он ждал результат; где сессии стояли, ожидая его.\n"
        "4. Рекомендации, 3–5, конкретные и проверяемые: каких агентов или субагентов завести, какие инструменты "
        "(скиллы, MCP) поставить, в каком формате давать указания — каждая с примером из этой недели.\n"
        "Формат: первый абзац — TL;DR в 2–3 строки, дальше разделы «Задачи», «Jira», «Эффективность», "
        "«Рекомендации». Коротко, без служебного; файлы — полным путём.")


def reviewer(state):
    """Кто разбирает: агент analyst, если он есть; иначе сессия хаба."""
    sessions = (state or {}).get("sessions") or {}
    a = sessions.get("analyst") or {}
    if a.get("state") not in (None, "removed") and a.get("role") == "agent":
        return "analyst"
    return next((n for n, s in sessions.items() if s.get("kind") == "hub" and s.get("state") != "removed"), None)
