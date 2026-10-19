"""Разбор недели: что сделано, сколько это стоило владельцу, что улучшить. Чистые функции — без ящика и времени."""
import datetime
import glob
import importlib.util
import json
import os
import re
import statistics
import zoneinfo


def _sync():
    """hub-sync/sync.py из репо ядра (папка над пакетом core): его SECRET/redact — одна ловля секретов на хаб."""
    core = importlib.util.find_spec("core")
    path = os.path.join(os.path.dirname(os.path.dirname(core.origin)), "hub-sync", "sync.py")
    spec = importlib.util.spec_from_file_location("hub_sync_for_recap", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


redact = _sync().redact


def clean(x):
    """Строки события без секретов — до сводки, черновика и отчёта (16.10 токен бота из сообщения попал в digest W42)."""
    if isinstance(x, str):
        return redact(x)
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [clean(v) for v in x]
    return x

OWNER_CHANNELS = ("web", "telegram", "cli")
FORM = "AskUserQuestion"
CUT = 400                  # сколько знаков текста брать в сводку для разбора


DAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
DEFAULT = {"day": 0, "hour": 9, "minute": 0, "zone": None}   # понедельник 9:00, местное время машины
# «расписание: пн старт» — при первом запуске хаба в этот день (владелец 14.10: «при первом запуске хаба или в 9:00»)
SET = re.compile(r"^\s*расписание:\s*(пн|вт|ср|чт|пт|сб|вс)\s+(?:(\d{1,2}):(\d{2})|(старт))(?:\s+(\S+))?\s*$", re.I)


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
        day, zone = DAYS.index(m[1].lower()), m[5]
        hour, minute = (0, 0) if m[4] else (int(m[2]), int(m[3]))
        if hour < 24 and minute < 60 and (not zone or known(zone)):
            return {"day": day, "hour": hour, "minute": minute, "zone": zone, **({"start": True} if m[4] else {})}
    return dict(DEFAULT)


def describe(when):
    at = "при первом запуске хаба" if when.get("start") else f"{when['hour']:02d}:{when['minute']:02d}"
    return f"{DAYS[when['day']]} {at}, {when['zone'] or 'местное время'}"


def last_week(now, zone):
    """Прошлая полная неделя от now: (начало, конец, метка вида 2026-W41) — понедельник 0:00 .. понедельник 0:00."""
    local = datetime.datetime.fromtimestamp(now, zone)
    monday = (local - datetime.timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start = monday - datetime.timedelta(days=7)
    year, week, _ = start.isocalendar()
    return start.timestamp(), monday.timestamp(), f"{year}-W{week:02d}"


def week_of(label, zone, until=None):
    """Метка 2026-W41 → (начало, конец, метка); until — последний день включительно (2026-10-09: пн–пт)."""
    year, week = label.split("-W")
    start = datetime.datetime.fromisocalendar(int(year), int(week), 1).replace(tzinfo=zone)
    end = start + datetime.timedelta(days=7)
    if until:
        end = datetime.datetime.fromisoformat(until).replace(tzinfo=zone) + datetime.timedelta(days=1)
    return start.timestamp(), end.timestamp(), label


def due(now, zone, done, when=DEFAULT, started=None):
    """Пора разбирать прошлую неделю: на этой неделе наступили день и время из расписания, а её ещё не разбирали. Хаб
    лежал в это время — разберём при первом такте после. «Старт» — хаб запустили в этот день или позже (started —
    когда поднялся процесс); хаб не выключали — в 9:00. Метка недели — или None."""
    local = datetime.datetime.fromtimestamp(now, zone)
    monday = (local - datetime.timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    day = monday + datetime.timedelta(days=when["day"])
    if when.get("start"):
        if local < day or (local < day + datetime.timedelta(hours=9) and (started or 0) < day.timestamp()):
            return None
    elif local < day + datetime.timedelta(hours=when["hour"], minutes=when["minute"]):
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
                    out[seq] = {**e, "data": clean(e.get("data") or {})}
    return [out[s] for s in sorted(out)]



def _theirs(e, host):
    """Событие другого хаба — с его метками: seq, сессии, задачи и формы не путаются со своими (analyst есть у обоих)."""
    d, ent = dict(e.get("data") or {}), e.get("entity")
    at = lambda x: x and f"{x}@{host}"   # noqa: E731
    for k in ("to", "session", "for_session"):
        if d.get(k):
            d[k] = at(d[k])
    if d.get("for"):
        d["for"] = f"{d['for'][0]}{host}:{d['for'][1:]}"
    if d.get("request_id"):
        d["request_id"] = at(d["request_id"])
    ent = f"d{host}:{ent[1:]}" if e.get("type") == "delegation.done" and ent else at(ent)
    return {**e, "seq": f"{host}:{e['seq']}", "entity": ent, "data": d}


def other_hubs(root, start, end, me):
    """Работа других хабов (hub-sync/work/<машина>/<неделя>.jsonl, выгружает их обмен): (события [start, end),
    сессия → проект). Свою машину не берём — её журнал свой."""
    evs, where = [], {}
    for host in sorted(os.listdir(root) if os.path.isdir(root) else []):
        if host.split(".")[0] == me.split(".")[0]:   # MacBook.local и MacBook — одна машина, не задвоить свою
            continue
        for path in sorted(glob.glob(os.path.join(root, host, "*.jsonl"))):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("type") == "hub.where":
                        where.update({f"{k}@{host}": v for k, v in (e.get("data") or {}).items()})
                    elif start <= (e.get("ts") or 0) < end and e.get("seq") is not None:
                        evs.append(_theirs({**e, "data": clean(e.get("data") or {})}, host))
    return evs, where


def hubs_note(machines, theirs, me):
    """Строка в сводку: чья работа вошла, чья нет. machines — машины, известные обмену (hub-sync/agents/*/machines)."""
    got = sorted({str(e["seq"]).split(":")[0] for e in theirs})
    miss = sorted({m for m in machines if m.split(".")[0] != me.split(".")[0] and m not in got})
    parts = [f"работа хаба {h} вошла (без цен — $ только этого хаба)" for h in got]
    parts += [f"хаб {h} за эти дни не выгружал работу — она не вошла, скажи об этом владельцу одной строкой" for h in miss]
    return ("Другие хабы: " + "; ".join(parts) + ".") if parts else ""


def merged(mine, theirs):
    """Свои и чужие события по времени."""
    return sorted(mine + theirs, key=lambda e: e.get("ts") or 0)

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


HUMAN = ("Пиши для человека, не для системы: простыми словами, как коллега рассказывает итоги недели. Его задачи и "
         "результаты (иллюстрации, реклама, тексты, встречи), а не устройство хаба: никаких коммитов, веток, тестов, "
         "сессий, перезапусков, id вида mN/dN/qN, путей и названий моделей. Работа над самим хабом — одной строкой "
         "«хаб: что стало удобнее», если владелец сам её просил. Цифры — круглые и понятные.")


def prompt(label, period, digest_path, work_path, draft_path, sample_path):
    """Поручение тому, кто разбирает неделю (analyst, нет его — хаб): черновик рекапа и туду в JSON по образцу."""
    return (
        f"[week-recap] Недельный рекап {label} ({period}) для владельца. Материал из журнала хаба: работа недели с "
        f"токенами — `{work_path}`, слова владельца, задачи и вопросы по времени — `{digest_path}`. Рамка — вся его "
        "работа за неделю, не только твоя область.\n"
        "1. Что сделано, что в работе, что брошено: склей обсуждения и задачи в его человеческие задачи (одна цель — "
        "одна задача). Каждый проект из work.md (раздел ##) — в отчёте, хоть строкой; крупные — по задачам. Обсуждение «стихло» — реши по смыслу: сделано (результат был) или брошено. С первого раза или "
        "с правками. Токены задачи — сумма её строк.\n"
        "Хаб — только работа над самим хабом (страница, Telegram, ядро, обмен двух компьютеров). Создание и настройка "
        "агента, скилла, скрипта или плагина под конкретную тему — к теме, которой он служит, и его токены тоже: "
        "аниматор — к анимации, промт-инженер и скрипт Midjourney — к генеративным стилям (владелец 14.10: «да, это хаб, "
        "но нет, это задачи конкретные»). Доли тем в TL;DR и в токенах — после такой разноски.\n"
        "2. Jira (MCP atlassian), Granola (MCP granola), Teams (MCP ms365) — только чтение: что закрыто за неделю, о "
        "чём договорились на встречах, что просили в Teams. Нет доступа к чему-то — "
        "`hub tools request <id> \"недельный рекап\"` и строка в together.slow, что источник не вошёл.\n"
        "3. Как работали вместе: где связка шла легко и где тормозила (нечёткие вводные, переделки, ожидание "
        "владельца) — на примерах недели. 3–5 советов: как давать вводные лучше, с примером из недели и как попробовать.\n"
        "4. Туду на эту неделю: заведённое (Jira — открытые на нём, Teams — ждут его ответа) и незаведённое (договорились "
        "на встрече в Granola или в чате, а задачи нет).\n"
        f"{HUMAN}\n"
        f"Итог — файл `{draft_path}`: JSON по образцу `{sample_path}` (те же ключи; пустой раздел — пустой список). "
        "numbers — 4 плитки: задач сделано, с первого раза, часы работы агентов, токены. Итог хода — одна строка «готово».")


def writer_prompt(label, draft_path, final_path):
    """Поручение райтеру: тексты черновика — по-человечески, в тот же JSON."""
    return (
        f"[week-recap] Недельный рекап {label} для владельца: перепиши тексты черновика `{draft_path}` так, чтобы его "
        "было приятно и быстро читать (владелец 14.10: «максимально хуманизированным, не машинным»). Смысл, факты и "
        "цифры не меняй; структуру JSON и ключи — тоже; ссылки и источники оставь как есть. Убери служебное и "
        f"техническое, что ему не нужно. {HUMAN}\n"
        f"Итог — файл `{final_path}`, тот же JSON. Итог хода — одна строка «готово».")


def render(template, data):
    """Отчёт: шаблон дизайнера с данными недели в блоке recap-data (больше в шаблоне ничего не меняется)."""
    blob = json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/")
    m = re.search(r'(<script id="recap-data" type="application/json">)(.*?)(</script>)', template, re.S)
    if not m:
        raise ValueError("в шаблоне нет блока recap-data")
    return template[:m.start(2)] + "\n" + blob + "\n" + template[m.end(2):]


def valid(data):
    """Черновик похож на рекап: словарь с TL;DR и разделами-списками."""
    if not isinstance(data, dict) or not str(data.get("tldr") or "").strip():
        return False
    lists = ("numbers", "done", "in_progress", "dropped", "tokens_by_task", "tips")
    return all(isinstance(data.get(k, []), list) for k in lists) and isinstance(data.get("todo", {}), dict)


def reviewer(state):
    """Кто разбирает: агент analyst, если он есть; иначе сессия хаба."""
    sessions = (state or {}).get("sessions") or {}
    a = sessions.get("analyst") or {}
    if a.get("state") not in (None, "removed") and a.get("role") == "agent":
        return "analyst"
    return next((n for n, s in sessions.items() if s.get("kind") == "hub" and s.get("state") != "removed"), None)


THREAD_GAP = 3 * 3600      # сообщения владельца одной сессии ближе этого — одно обсуждение
FADED = 24 * 3600          # обсуждение без новых слов владельца дольше этого — стихло (владелец 14.10: «косвенно выполненные»)
LIVELY = 3                 # столько слов владельца в обсуждении — «активное»


def tokens(d):
    return int(d.get("tokens_in") or 0) + int(d.get("tokens_out") or 0)


def items(evs, end, spent=None, where=None):
    """Работа недели по-человечески: обсуждения владельца с сессией (его сообщения подряд, промежутки меньше
    THREAD_GAP) и задачи dN. Каждый ход сессии — к задаче, которую она ведёт (последней открытой), иначе к последнему
    её обсуждению; токены и цена (spent: seq хода → usd) — к ним. Обсуждение, где владелец писал не меньше LIVELY раз и
    замолчал дольше FADED до конца недели, — «стихло»: по нему решает разбирающий (сделано или брошено)."""
    spent, where = spent or {}, where or {}
    out, last_thread, open_tasks, own = [], {}, {}, {}
    for e in evs:
        t, d, ent, ts = e.get("type"), e.get("data") or {}, e.get("entity"), e.get("ts") or 0
        if t == "message.received" and _owner(d) and d.get("to") and \
                not str(d.get("text") or "").startswith("Ответ владельца на вопрос"):
            s = d["to"]
            th = last_thread.get(s)
            if not th or ts - th["last"] > THREAD_GAP:
                th = {"id": f"m{e['seq']}", "kind": "обсуждение", "session": s, "where": where.get(s) or s,
                      "topic": str(d.get("text") or ""), "first": ts, "last": ts, "owner_msgs": 0,
                      "tokens": 0, "usd": 0.0, "turns": 0}
                last_thread[s] = th
                out.append(th)
            th["owner_msgs"] += 1
            th["last"] = ts
        elif t == "delegation.created" and d.get("session"):
            s = d["session"]
            # задачу поставила рабочая сессия (направление попросило скрипт, агента) — токены её теме, не хабу
            # (владелец 14.10: «да, это хаб, но нет, это задачи конкретные»)
            by = d.get("for_session")
            task = {"id": f"d{e['seq']}", "kind": "задача", "session": s,
                    "where": (where.get(by) or by) if by else (where.get(s) or s),
                    "topic": str(d.get("context") or ""), "first": ts, "last": ts, "owner_msgs": 0,
                    "tokens": 0, "usd": 0.0, "turns": 0, "done": None, "for": d.get("for")}
            open_tasks.setdefault(s, []).append(task)
            out.append(task)
        elif t == "delegation.done":
            for tasks in open_tasks.values():
                for task in tasks:
                    if task["id"] == ent:
                        task["done"] = ts
                        tasks.remove(task)
                        break
        elif t == "turn.result" and ent:
            box = (open_tasks.get(ent) or [None])[-1] or last_thread.get(ent) or own.get(ent)
            if not box:   # ход без просьбы владельца на этой неделе — своё сессии (расписание, сторож)
                box = own[ent] = {"id": f"s:{ent}", "kind": "своё", "session": ent,
                                          "where": where.get(ent) or ent, "topic": "", "first": ts, "last": ts,
                                          "owner_msgs": 0, "tokens": 0, "usd": 0.0, "turns": 0}
                out.append(box)
            box["tokens"] += tokens(d)
            box["usd"] += spent.get(e["seq"], 0.0)
            box["turns"] += 1
            box["last"] = max(box["last"], ts)
    for x in out:
        x["usd"] = round(x["usd"], 2)
        if x["kind"] == "обсуждение":
            x["faded"] = x["owner_msgs"] >= LIVELY and end - x["last"] >= FADED
    return sorted(out, key=lambda x: -x["tokens"])


def human_tokens(n):
    return f"{n / 1e6:.1f} млн".replace(".", ",") if n >= 1e5 else f"{round(n / 1e3)} тыс"


def work_digest(label, start, end, work, zone, top=12, note=""):
    """Сводка работы недели для разбора: по проектам, крупное выше; в проекте — что владелец обсуждал и поручал,
    сколько ушло токенов, что стихло. Хвост проекта свёрнут, но ни один проект не пропадает (14.10: общий верх по
    токенам — почти одна работа над хабом, реклама ушла в «остальное»)."""
    total = sum(x["tokens"] for x in work) or 1
    lines = [f"# Работа недели {label}: {_at(start, zone)} — {_at(end - 1, zone)}", "",
             f"Всего токенов: {human_tokens(total)}, ≈ ${round(sum(x['usd'] for x in work))} по прайсу API.", "",
             *([note, ""] if note else []),
             "Строка: id · что · период · слов владельца · ходов · токены (доля) · $ · состояние"]
    groups = {}
    for x in work:
        groups.setdefault(x["where"], []).append(x)
    for where, xs in sorted(groups.items(), key=lambda g: -sum(x["tokens"] for x in g[1])):
        xs = sorted(xs, key=lambda x: -x["tokens"])
        tok = sum(x["tokens"] for x in xs)
        lines += ["", f"## {where} — {human_tokens(tok)} ({round(100 * tok / total)}%)", ""]
        for x in xs[:top]:
            state = ("сдана" if x.get("done") else "не сдана") if x["kind"] == "задача" else \
                ("стихло — реши, сделано или брошено" if x.get("faded") else "") if x["kind"] == "обсуждение" \
                else "без просьбы владельца"
            lines.append(f"- {x['id']} · {x['kind']}: {_cut(x['topic'], 300) or '—'} · "
                         f"{_at(x['first'], zone)} — {_at(x['last'], zone)} · {x['owner_msgs']} · {x['turns']} · "
                         f"{human_tokens(x['tokens'])} ({round(100 * x['tokens'] / total)}%) · ${x['usd']} · {state}")
        rest = xs[top:]
        if rest:
            lines.append(f"- остальное: {len(rest)} шт., {human_tokens(sum(x['tokens'] for x in rest))}")
    return "\n".join(lines) + "\n"
