"""Week review: what was done and how many tokens it took, what the owner may not know, what to improve. Pure functions - no spool, no clock.

Two kinds of text live here. What the analyst and the writer sessions read (the digests, the prompts) is always
English; the prompt tells them which language to write the report in. What the owner reads (the "numbers only"
fallback, the schedule words, month names) is en/ru by the hub language."""
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
CUT = 400                  # how many characters of a text go into the digest for the review

# Protocol marker, not UI text: the hub core starts the message that carries the owner's answer to a form with these
# Russian words ("Owner's answer to question qN"). Matched against the core's text, so it stays as is (spelled by code points).
ANSWER_MARK = "".join(map(chr, (0x41e, 0x442, 0x432, 0x435, 0x442, 0x20, 0x432, 0x43b, 0x430, 0x434, 0x435, 0x43b, 0x44c,
                                0x446, 0x430, 0x20, 0x43d, 0x430, 0x20, 0x432, 0x43e, 0x43f, 0x440, 0x43e, 0x441)))

LANGS = ("en", "ru")


def norm(lang):
    """The language the plugin has a table for: ru, otherwise (any other, none) en; a regional variant counts as its base."""
    base = (lang or "en").split("-")[0].lower()
    return base if base in LANGS else "en"


# Words of the owner's schedule post ("schedule: mon 09:00 [zone]"), months of the period label. The post is accepted
# in both languages whatever the hub language is (old posts stay valid); the tab writes it in the hub language.
WORDS = {
    "en": {"keyword": "schedule", "start": "start", "days": ("mon", "tue", "wed", "thu", "fri", "sat", "sun"),
           "months": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")},
    "ru": {"keyword": "расписание", "start": "старт", "days": ("пн", "вт", "ср", "чт", "пт", "сб", "вс"),
           "months": ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
                      "ноября", "декабря")},
}
DEFAULT = {"day": 0, "hour": 9, "minute": 0, "zone": None}   # Monday 9:00, the machine's local time
_DAYS = [d for w in WORDS.values() for d in w["days"]]
# "schedule: mon start" - at the first start of the hub on that day
SET = re.compile(r"^\s*(?:%s):\s*(%s)\s+(?:(\d{1,2}):(\d{2})|(%s))(?:\s+(\S+))?\s*$" % (
    "|".join(w["keyword"] for w in WORDS.values()), "|".join(_DAYS), "|".join(w["start"] for w in WORDS.values())), re.I)


def day_index(word):
    """Day word of either language -> 0 (Monday) .. 6."""
    word = word.lower()
    return next(w["days"].index(word) for w in WORDS.values() if word in w["days"])


def local_zone():
    """The machine's zone: from the /etc/localtime link (with daylight saving); no link (Windows) - the current offset."""
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
    """When to review: the last valid post "schedule: mon 09:00 [zone]" (or the Russian form) in the plugin channel
    from the owner or the hub."""
    hubs = {n for n, s in ((state or {}).get("sessions") or {}).items() if s.get("kind") == "hub"}
    for post in reversed(((state or {}).get("feeds") or {}).get("week-recap") or []):
        m = SET.match(str(post.get("text") or ""))
        if not m or (post.get("by") != "owner" and post.get("by") not in hubs):
            continue
        day, zone = day_index(m[1]), m[5]
        hour, minute = (0, 0) if m[4] else (int(m[2]), int(m[3]))
        if hour < 24 and minute < 60 and (not zone or known(zone)):
            return {"day": day, "hour": hour, "minute": minute, "zone": zone, **({"start": True} if m[4] else {})}
    return dict(DEFAULT)


def last_week(now, zone):
    """The last full week before now: (start, end, label like 2026-W41) - Monday 0:00 .. Monday 0:00."""
    local = datetime.datetime.fromtimestamp(now, zone)
    monday = (local - datetime.timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start = monday - datetime.timedelta(days=7)
    year, week, _ = start.isocalendar()
    return start.timestamp(), monday.timestamp(), f"{year}-W{week:02d}"


def week_of(label, zone, until=None):
    """Label 2026-W41 -> (start, end, label); until - the last day inclusive (2026-10-09: Mon-Fri)."""
    year, week = label.split("-W")
    start = datetime.datetime.fromisocalendar(int(year), int(week), 1).replace(tzinfo=zone)
    end = start + datetime.timedelta(days=7)
    if until:
        end = datetime.datetime.fromisoformat(until).replace(tzinfo=zone) + datetime.timedelta(days=1)
    return start.timestamp(), end.timestamp(), label


def due(now, zone, done, when=DEFAULT, started=None):
    """Time to review the last week: this week the day and time of the schedule have come and it has not been reviewed
    yet. The hub was down at that time - review at the first tick after. "Start" - the hub was started on that day or
    later (started - when the process came up); the hub was not switched off - at 9:00. The week label or None."""
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
    """The archive and the current journal: old journals lie whole in journal-archive (rotation)."""
    return sorted(glob.glob(os.path.join(state_dir, "journal-archive", "journal-*.jsonl"))) + \
        [os.path.join(state_dir, "journal.jsonl")]


def events(paths, start, end):
    """Events with ts in [start, end) from all journals, without repeats (archives overlap), in order."""
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



def _base(name):
    """A session name without the hub mark of another machine: hub-dev@Win -> hub-dev."""
    return str(name).split("@")[0]


def _host_of(name, me):
    """The machine a session lives on: the mark after @ (another hub), none - ours (me)."""
    return str(name).split("@", 1)[1] if "@" in str(name) else me


def _theirs(e, host):
    """An event of another hub - with its marks: seq, sessions, tasks and forms do not mix with ours (both have an analyst)."""
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
    """Work of other hubs (hub-sync/work/<machine>/<week>.jsonl, exported by their sync): (events [start, end),
    session -> project). Our own machine is skipped - its journal is ours."""
    evs, where = [], {}
    for host in sorted(os.listdir(root) if os.path.isdir(root) else []):
        if host.split(".")[0] == me.split(".")[0]:   # MacBook.local and MacBook are one machine, do not count ours twice
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
    """A line for the digest: whose work came in, whose did not. machines - the machines the sync knows (hub-sync/agents/*/machines)."""
    got = sorted({str(e["seq"]).split(":")[0] for e in theirs})
    miss = sorted({m for m in machines if m.split(".")[0] != me.split(".")[0] and m not in got})
    parts = [f"the work of hub {h} is included (its tokens are counted like this hub's)" for h in got]
    parts += [f"hub {h} did not export its work for these days - it is not included, tell the owner about it in one line"
              for h in miss]
    return ("Other hubs: " + "; ".join(parts) + ".") if parts else ""


def merged(mine, theirs):
    """Our and other hubs' events by time."""
    return sorted(mine + theirs, key=lambda e: e.get("ts") or 0)

def _owner(d):
    return d.get("owner") is True and d.get("channel") in OWNER_CHANNELS


def _median_min(xs):
    return round(statistics.median(xs) / 60, 1) if xs else None


def measure(evs):
    """The week's numbers and material for the review: owner messages, tasks, forms, turns per session."""
    msgs, answers, forms, perms, tasks, files = [], [], {}, 0, {}, 0
    sessions, results = {}, {}
    for e in evs:
        t, d, ent, ts = e.get("type"), e.get("data") or {}, e.get("entity"), e.get("ts")
        if t == "message.received" and _owner(d) and d.get("to"):
            text = str(d.get("text") or "")
            (answers if text.startswith(ANSWER_MARK) else msgs).append(
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
    asked = {m["id"]: m["ts"] for m in msgs}   # from the owner's request to the delivery of the task set by it
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


# Names of the numbers: the English ones go to the digest for the model, both go to the owner (numbers-only fallback).
NAMES = {
    "en": {
        "owner_messages": "owner messages", "owner_chars": "characters in them", "forms": "questions by form",
        "forms_answered": "of them answered", "form_answer_median_min": "median time to answer a form, min",
        "permission_requests": "permission requests", "tasks": "hub tasks (dN)", "tasks_done": "of them delivered",
        "wait_median_min": "median time to a session's first reply, min",
        "task_median_min": "median time from request to delivery of the task, min", "files_sent": "files to the owner",
        "turns": "session turns", "busy_hours": "hours of session work",
    },
    "ru": {
        "owner_messages": "сообщений владельца", "owner_chars": "знаков в них", "forms": "вопросов формой",
        "forms_answered": "из них отвечено", "form_answer_median_min": "медиана ответа на форму, мин",
        "permission_requests": "запросов разрешений", "tasks": "задач хаба (dN)", "tasks_done": "из них сдано",
        "wait_median_min": "медиана до первого ответа сессии, мин",
        "task_median_min": "медиана от просьбы до сдачи задачи, мин", "files_sent": "файлов владельцу",
        "turns": "ходов сессий", "busy_hours": "часов работы сессий",
    },
}


def digest(label, start, end, m, zone):
    """The week's digest for the review (markdown, English): numbers, sessions, then by time - the owner's words, tasks, forms."""
    lines = [f"# Week {label}: {_at(start, zone)} — {_at(end - 1, zone)}", "", "## Numbers"]
    lines += [f"- {NAMES['en'][k]}: {v if v is not None else '—'}" for k, v in m["numbers"].items()]
    lines += ["", "## Sessions (owner messages · turns · minutes of work)"]
    for name, s in sorted(m["sessions"].items(), key=lambda x: (-x[1]["owner_msgs"], -x[1]["turns"])):
        lines.append(f"- {name}: {s['owner_msgs']} · {s['turns']} · {s['busy_min']}")
    timeline = [(x["ts"], f"owner → {x['to']} ({x['id']}, reply in "
                 f"{x['wait_min'] if x['wait_min'] is not None else '—'} min): {_cut(x['text'])}") for x in m["messages"]]
    timeline += [(x["ts"], f"task {x['id']} → {x['session']} (for {x['for'] or '—'}): {_cut(x['context'], 250)}"
                  + (f" — delivered {_at(x['done'], zone)}" if x["done"] else " — not delivered")) for x in m["tasks"]]
    timeline += [(x["ts"], f"form {x['id']} from {x['session']}: {_cut(x['question'], 250)} → "
                  + (f"answered in {round((x['answered'] - x['ts']) / 60)} min: {_cut(x.get('answer', ''), 200)}"
                     if x["answered"] else "no answer")) for x in m["forms"]]
    lines += ["", "## Timeline"]
    lines += [f"- {_at(ts, zone)} · {text}" for ts, text in sorted(timeline, key=lambda x: x[0])]
    return "\n".join(lines) + "\n"


HUMAN = ("Write for a person, not for a system: in plain words, as a colleague tells the results of the week. Their "
         "tasks and results (illustrations, ads, videos, texts, plugins, the hub itself), what became possible - not "
         "commits, branches, tests, ids like mN/dN/qN, paths or model names. Numbers - round and clear. Money is "
         "never mentioned: no currencies and no prices - the cost of work is only tokens (\"9.2M "
         "tokens\"), at every task and every item.")

# English names of the report languages for the prompts (the plugin has tables for these two only)
LANGUAGE = {"en": "English", "ru": "Russian"}


def _write_in(lang):
    return (f"Write every text of the report (titles, stories, tips, todo) in {LANGUAGE[norm(lang)]}; the JSON keys stay "
            "exactly as in the sample (English), and the sample's own texts only show the shape, not the language.")


def prompt(label, period, digest_path, work_path, draft_path, sample_path, lang="en"):
    """The assignment for whoever reviews the week (analyst, no analyst - the hub): the recap draft and the todo as JSON like the sample."""
    return (
        f"[week-recap] Weekly recap {label} ({period}) for the owner. Material from the hub journal: the week's work "
        f"with tokens, from all the owner's computers (Mac and Windows together) - `{work_path}`, the owner's words, "
        f"tasks and questions in time order - `{digest_path}`. The frame is all of their work for the week, not only "
        "your area. The cost of work is tokens only: money and prices appear nowhere in your result.\n"
        "1. The main part - insights about working with the hub (the `insights` list, 3-6 items, the sharpest first). "
        "What the owner may not know: hub abilities they do not use, and what quietly broke or hung this week (tasks "
        "not delivered, a session or a form that waited for days, work that went quiet, two computers that did not "
        "agree). What to improve critically in their own feedback and way of setting tasks: where they set a task "
        "unclearly and then redid it, repeated one request, answered a form late, gave no \"done when...\"; and in "
        "how the hub and agents worked for them. Each insight: kind (unknown / improve / broken), a title in one "
        "line, what exactly happened with a concrete example from this week (the date, the task, the tokens it "
        "cost), and what to do next (`try`). No general advice without an example. You may check the facts with "
        "`hub status`, `hub task show dN`, `hub coach`.\n"
        "2. What was done, by themes (the `themes` list): merge discussions and tasks into the owner's human tasks "
        "(one goal - one item) and group them by topic: work on the hub itself (the page, Telegram, the core, sync "
        "between computers), plugins that were developed, Hub Design and the designer, videos, and every other "
        "project from work.md (a ## section; none is dropped, a small one - a line). Every item has its own tokens - "
        "each video, each plugin, each task: the sum of its lines of work.md, from both computers. A theme's tokens "
        "and share are the sum of its items. State of an item: done / progress / dropped (a discussion that went quiet "
        "- decide by meaning). Done - first try or with corrections. Creating and setting up an agent, skill, script "
        "or plugin for a specific topic belongs to the topic it serves, and its tokens too: an animator - to "
        "animation, a prompt engineer and an image-generation script - to generative styles; a plugin for the hub "
        "page - to plugins.\n"
        "3. Jira (MCP atlassian), Granola (MCP granola), Teams (MCP ms365) - read only: what was closed this week, "
        "what was agreed in meetings, what was asked in Teams. No access to something - "
        "`hub tools request <id> \"weekly recap\"` and a line in together.slow saying the source was not included.\n"
        "4. How you worked together: where the pair went easily and where it slowed down - short, with examples "
        "(the details go to insights).\n"
        "5. Todo for this week: filed (Jira - open ones on the owner, Teams - waiting for their answer) and not filed "
        "(agreed at a meeting in Granola or in a chat, but there is no task).\n"
        f"{HUMAN}\n{_write_in(lang)}\n"
        f"The result is the file `{draft_path}`: JSON like `{sample_path}` (the same keys; an empty section - an empty "
        "list; do not write `machines` - the plugin adds which computers are covered). numbers - 4 tiles: items done, "
        "first try, hours of agent work, tokens (the hint of the tokens tile - how they split between computers). The "
        "result of your turn is one line: \"done\".")


def writer_prompt(label, draft_path, final_path, lang="en"):
    """The assignment for the writer: the draft's texts in a human voice, into the same JSON."""
    return (
        f"[week-recap] Weekly recap {label} for the owner: rewrite the texts of the draft `{draft_path}` so that it is "
        "pleasant and quick to read. Do not change the meaning, facts and numbers; nor the JSON structure and keys; "
        "the insights keep their examples and their sharpness - do not soften or generalize them; the tokens of every "
        "item stay. Leave links and sources as they are. Remove the internal and technical things they do not need. "
        f"{HUMAN}\n{_write_in(lang)}\n"
        f"The result is the file `{final_path}`, the same JSON. The result of your turn is one line: \"done\".")


def render(template, data):
    """The report: the designer's template with the week's data in the recap-data block (nothing else in the template changes)."""
    blob = json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/")
    m = re.search(r'(<script id="recap-data" type="application/json">)(.*?)(</script>)', template, re.S)
    if not m:
        raise ValueError("the template has no recap-data block")
    return template[:m.start(2)] + "\n" + blob + "\n" + template[m.end(2):]


def valid(data):
    """The draft looks like a recap: a dict with a TL;DR and sections that are lists."""
    if not isinstance(data, dict) or not str(data.get("tldr") or "").strip():
        return False
    lists = ("numbers", "insights", "themes")
    return all(isinstance(data.get(k, []), list) for k in lists) and isinstance(data.get("todo", {}), dict)


def reviewer(state):
    """Who reviews: the analyst agent if there is one; otherwise a hub session."""
    sessions = (state or {}).get("sessions") or {}
    a = sessions.get("analyst") or {}
    if a.get("state") not in (None, "removed") and a.get("role") == "agent":
        return "analyst"
    return next((n for n, s in sessions.items() if s.get("kind") == "hub" and s.get("state") != "removed"), None)


THREAD_GAP = 3 * 3600      # an owner's messages to one session closer than this - one discussion
FADED = 24 * 3600          # a discussion without new words of the owner for longer than this - went quiet
LIVELY = 3                 # this many words of the owner in a discussion - "active"


def tokens(d):
    return int(d.get("tokens_in") or 0) + int(d.get("tokens_out") or 0)


def items(evs, end, where=None, me="this"):
    """The week's work in human terms: the owner's discussions with a session (their messages in a row, gaps shorter
    than THREAD_GAP) and tasks dN. Every turn of a session goes to the task it leads (the last open one), otherwise to
    its last discussion; its tokens go there too, and "machines" - the same tokens by the computer the turn ran on
    (me - the name of ours; sessions of another hub carry @<machine>). A discussion where the owner wrote at least
    LIVELY times and then fell silent for longer than FADED before the week's end "went quiet": the reviewer decides
    (done or dropped). Kinds: "discussion", "task", "own"."""
    where = where or {}
    topic = lambda s: where.get(s) or _base(s)   # noqa: E731
    out, last_thread, open_tasks, own = [], {}, {}, {}
    for e in evs:
        t, d, ent, ts = e.get("type"), e.get("data") or {}, e.get("entity"), e.get("ts") or 0
        if t == "message.received" and _owner(d) and d.get("to") and \
                not str(d.get("text") or "").startswith(ANSWER_MARK):
            s = d["to"]
            th = last_thread.get(s)
            if not th or ts - th["last"] > THREAD_GAP:
                th = {"id": f"m{e['seq']}", "kind": "discussion", "session": s, "where": topic(s),
                      "topic": str(d.get("text") or ""), "first": ts, "last": ts, "owner_msgs": 0,
                      "tokens": 0, "machines": {}, "turns": 0}
                last_thread[s] = th
                out.append(th)
            th["owner_msgs"] += 1
            th["last"] = ts
        elif t == "delegation.created" and d.get("session"):
            s = d["session"]
            # the task was set by a working session (a direction asked for a script, an agent) - tokens go to its topic, not to the hub
            by = d.get("for_session")
            task = {"id": f"d{e['seq']}", "kind": "task", "session": s, "where": topic(by) if by else topic(s),
                    "topic": str(d.get("context") or ""), "first": ts, "last": ts, "owner_msgs": 0,
                    "tokens": 0, "machines": {}, "turns": 0, "done": None, "for": d.get("for")}
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
            if not box:   # a turn without an owner's request this week - the session's own (schedule, watchdog)
                box = own[ent] = {"id": f"s:{ent}", "kind": "own", "session": ent, "where": topic(ent), "topic": "",
                                  "first": ts, "last": ts, "owner_msgs": 0, "tokens": 0, "machines": {}, "turns": 0}
                out.append(box)
            n, host = tokens(d), _host_of(ent, me)
            box["tokens"] += n
            box["machines"][host] = box["machines"].get(host, 0) + n
            box["turns"] += 1
            box["last"] = max(box["last"], ts)
    for x in out:
        if x["kind"] == "discussion":
            x["faded"] = x["owner_msgs"] >= LIVELY and end - x["last"] >= FADED
    return sorted(out, key=lambda x: -x["tokens"])


def fold_agents(names, where, agents):
    """Sessions that are not in a project but are named <agent>-<what> (an executor of an agent, also on another
    machine, where its parent is unknown) go to the topic of the agent: session -> topic."""
    out = {}
    for n in names:
        base, _, host = str(n).partition("@")
        if n in where or not base:
            continue
        for a in sorted(agents, key=len, reverse=True):
            if base.startswith(a + "-"):
                out[n] = where.get(a) or where.get(f"{a}@{host}") or a
                break
    return out


def machine_tokens(work):
    """Tokens of the week by computer: {machine: tokens}."""
    out = {}
    for x in work:
        for host, n in (x.get("machines") or {}).items():
            out[host] = out.get(host, 0) + n
    return out


def machine_report(work, names, me, got, known=()):
    """Which computers the report covers, for the owner (the plugin fills it in, not the model): a list of
    {name, tokens, share} for every computer whose work came in and {name, missing: True} for the ones that did not
    export. names - machine -> display name (Mac, Windows), me - ours, got - the other machines whose work came in."""
    by, total = machine_tokens(work), sum(x["tokens"] for x in work) or 1
    show = lambda h: names.get(h) or _hub_name(h)   # noqa: E731
    out = [{"name": show(h), "tokens": human_tokens(by.get(h, 0)), "share": round(by.get(h, 0) / total, 3)}
           for h in [me] + sorted(got)]
    seen = {h.split(".")[0] for h in [me, *got]}
    out += [{"name": show(h), "missing": True} for h in sorted(known) if h.split(".")[0] not in seen]
    return out


def _hub_name(host):
    return host[:-6] if host.endswith(".local") else host


def platform_of(cwd):
    """The platform of a machine by a session folder it reported (hub-sync/agents/*/machines/<machine>.json): a drive
    letter - Windows, /Users - Mac, /home - Linux; unknown - None."""
    cwd = str(cwd or "")
    if re.match(r"^[A-Za-z]:[\\/]", cwd):
        return "Windows"
    return "Mac" if cwd.startswith("/Users/") else "Linux" if cwd.startswith("/home/") else None


def human_tokens(n):
    return f"{n / 1e6:.1f}M" if n >= 1e5 else f"{round(n / 1e3)}k"


def work_digest(label, start, end, work, zone, top=12, note="", names=None):
    """The week's work digest for the review (English): by topic, big ones higher; in a topic - what the owner
    discussed and assigned, how many tokens went (and on which computer), what went quiet. A topic's tail is folded,
    but no topic disappears (14.10: the overall top by tokens was almost all work on the hub, ads went into "the
    rest"). names - machine -> display name (Mac, Windows)."""
    names = names or {}
    show = lambda h: names.get(h) or _hub_name(h)   # noqa: E731
    total = sum(x["tokens"] for x in work) or 1
    by = machine_tokens(work)
    split = lambda m: " · ".join(f"{show(h)} {human_tokens(n)}" for h, n in sorted(m.items(), key=lambda p: -p[1]) if n)   # noqa: E731
    lines = [f"# Work of the week {label}: {_at(start, zone)} — {_at(end - 1, zone)}", "",
             f"Total tokens: {human_tokens(total)} ({split(by) or '—'}).", "",
             *([note, ""] if note else []),
             "Line: id · what · period · owner's words · turns · tokens (share) · computer · state"]
    groups = {}
    for x in work:
        groups.setdefault(x["where"], []).append(x)
    for where, xs in sorted(groups.items(), key=lambda g: -sum(x["tokens"] for x in g[1])):
        xs = sorted(xs, key=lambda x: -x["tokens"])
        tok = sum(x["tokens"] for x in xs)
        lines += ["", f"## {where} — {human_tokens(tok)} ({round(100 * tok / total)}%; {split(machine_tokens(xs)) or '—'})", ""]
        for x in xs[:top]:
            state = ("delivered" if x.get("done") else "not delivered") if x["kind"] == "task" else \
                ("went quiet - decide: done or dropped" if x.get("faded") else "") if x["kind"] == "discussion" \
                else "no request from the owner"
            ms = x.get("machines") or {}
            on = show(max(ms, key=ms.get)) if ms else "—"
            lines.append(f"- {x['id']} · {x['kind']}: {_cut(x['topic'], 300) or '—'} · "
                         f"{_at(x['first'], zone)} — {_at(x['last'], zone)} · {x['owner_msgs']} · {x['turns']} · "
                         f"{human_tokens(x['tokens'])} ({round(100 * x['tokens'] / total)}%) · {on} · {state}")
        rest = xs[top:]
        if rest:
            lines.append(f"- the rest: {len(rest)} items, {human_tokens(sum(x['tokens'] for x in rest))}")
    return "\n".join(lines) + "\n"


# ───── daily recap: what is in progress now - on all hubs, in Jira and with the agents ─────

DAY_DEFAULT = {"on": True, "hour": 9, "minute": 0, "zone": None}   # every day at 9:00, the zone as the week's
# "day: 08:30 [zone]" or "day: off" - a post of the owner (or the hub) in the plugin channel, like "schedule:" of the week;
# accepted in both languages whatever the hub language is
DAY_SET = re.compile(r"^\s*(?:day|день):\s*(?:(off|выкл)|(\d{1,2}):(\d{2}))(?:\s+(\S+))?\s*$", re.I)
DAY_TOP = 6                # tasks of a hub in the owner's text; the rest - "and N more"
DAY_STALE = 2 * 86400      # another hub's upload is older - its data is stale (the hub is off?)

# What the owner reads in the daily recap without the analyst, by hub language ({name} placeholders).
DAYW = {
    "en": {
        "head": "In progress, {date}:", "mine": "This hub", "hub": "Hub {name}",
        "count": "{name}: tasks in progress - {n}{stale}", "none": "{name}: nothing hangs{stale}",
        "stale": " (old data - the hub has not uploaded for long)", "task": "• {text} - {who}, {state}, {age}",
        "more": "• and {n} more", "backlog": "In the agents' backlogs: {n} ({dirs})",
        "nosnap": "Hub {name}: no upload of open work yet - its tasks are not here.",
        "states": {"working": "in progress", "waiting-owner": "waits for you", "stopped": "broke off"},
        "hours": "{n} h", "days": "{n} d", "title": "In progress · {date}",
    },
    "ru": {
        "head": "Что в работе, {date}:", "mine": "Этот хаб", "hub": "Хаб {name}",
        "count": "{name}: задач в работе — {n}{stale}", "none": "{name}: ничего не висит{stale}",
        "stale": " (данные старые — хаб давно не выгружал)", "task": "• {text} — {who}, {state}, {age}",
        "more": "• и ещё {n}", "backlog": "В бэклогах агентов: {n} ({dirs})",
        "nosnap": "Хаб {name}: выгрузки открытого пока нет — его задач здесь нет.",
        "states": {"working": "в работе", "waiting-owner": "ждёт тебя", "stopped": "оборвалась"},
        "hours": "{n} ч", "days": "{n} дн.", "title": "Что в работе · {date}",
    },
}


def day_date(label, lang="en"):
    """"2026-10-21" -> "Oct 21" in English, "21 октября" in Russian."""
    d = datetime.datetime.strptime(label, "%Y-%m-%d")
    month = WORDS[norm(lang)]["months"][d.month - 1]
    return f"{d.day} {month}" if norm(lang) == "ru" else f"{month} {d.day}"


def day_schedule(state):
    hubs = {n for n, s in ((state or {}).get("sessions") or {}).items() if s.get("kind") == "hub"}
    for post in reversed(((state or {}).get("feeds") or {}).get("week-recap") or []):
        m = DAY_SET.match(str(post.get("text") or ""))
        if not m or (post.get("by") != "owner" and post.get("by") not in hubs):
            continue
        if m[1]:
            return {**DAY_DEFAULT, "on": False}
        hour, minute, zone = int(m[2]), int(m[3]), m[4]
        if hour < 24 and minute < 60 and (not zone or known(zone)):
            return {"on": True, "hour": hour, "minute": minute, "zone": zone}
    return dict(DAY_DEFAULT)


def day_due(now, zone, done, when=DAY_DEFAULT):
    """The day's label (2026-10-21) if today the recap time has come and it has not been sent; the hub was down - the first tick after.
    Past days are not caught up: the recap is about "now"."""
    if not when.get("on", True):
        return None
    local = datetime.datetime.fromtimestamp(now, zone)
    if local < local.replace(hour=when["hour"], minute=when["minute"], second=0, microsecond=0):
        return None
    label = local.strftime("%Y-%m-%d")
    return None if label in done else label


def open_now(state, now, droot):
    """This hub's snapshot of open work - by the same function the exchange uploads with (hub-sync/sync.py), so this hub and
    the others look alike. A core without it (the hub has not updated yet) - None."""
    fn = getattr(_sync(), "open_snapshot", None)
    return fn(state, now, droot) if fn else None


def other_open(root, me):
    """Snapshots of open work of the other hubs (hub-sync/work/<machine>/open.json, the exchange uploads them): {machine: snapshot}.
    Our own machine is skipped (MacBook.local and MacBook are one)."""
    out = {}
    for host in sorted(os.listdir(root) if os.path.isdir(root) else []):
        if host.split(".")[0] == me.split(".")[0]:
            continue
        try:
            with open(os.path.join(root, host, "open.json"), encoding="utf-8") as f:
                snap = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(snap, dict):
            out[host] = clean(snap)
    return out


def _age(then, now, lang="en"):
    if not then:
        return "—"
    h = max(0, now - then) / 3600
    w = DAYW[norm(lang)]
    return w["hours"].format(n=round(h)) if h < 48 else w["days"].format(n=round(h / 24))


def _hub(host):
    return host[:-6] if host.endswith(".local") else host


def day_digest(label, mine, theirs, now, zone, known_hosts=(), jira=None):
    """Material for the day's review (markdown, English): by hub - dN tasks (the long-hanging above), direction backlogs.
    This hub - mine (the open_now snapshot), the others - theirs {machine: snapshot}; machines with no snapshot - in a line."""
    lines = [f"# In progress: {label}", ""]
    lines += [f"Jira (MCP atlassian): {json.dumps(jira, ensure_ascii=False)}" if jira else "Jira: no settings in hub.json.", ""]
    snaps = ([("this hub", mine)] if mine else []) + [(f"hub {_hub(h)}", s) for h, s in theirs.items()]
    for name, snap in snaps:
        stale = now - (snap.get("ts") or 0) >= DAY_STALE
        lines += [f"## {name} ({_hub(snap.get('host') or '')}; snapshot from {_at(snap['ts'], zone)}"
                  + (" - STALE, the hub has not uploaded for long: say so" if stale else "") + ")", ""]
        tasks = snap.get("tasks") or []
        lines.append(f"dN tasks in progress: {len(tasks)} (the long-hanging first)")
        for t in tasks:
            about = (snap.get("agents") or {}).get(t["session"])
            lines.append(f"- {DAYW['en']['states'].get(t['state'], t['state'])} · with {t['session']}"
                         + (f" ({about})" if about else "") + f" · hanging {_age(t.get('created'), now)}"
                         + ("" if t.get("owner") else " · set by the hub or an agent") + f" · {_cut(t.get('text'), 200) or '—'}")
        lines.append("")
        lines.append("Tasks in the direction backlogs (inside the agents):")
        back = snap.get("backlog") or {}
        for slug, xs in sorted(back.items()):
            lines.append(f"- {slug}: " + "; ".join(f"{x['status']}: {_cut(x['title'], 100)}" for x in xs))
        if not back:
            lines.append("- none")
        lines.append("")
    missing = sorted(h for h in known_hosts if h.split(".")[0] not in {x.split(".")[0] for x in theirs}
                     and (not mine or h.split(".")[0] != mine["host"].split(".")[0]))
    if missing:
        lines += [f"Hubs with no snapshot: {', '.join(_hub(h) for h in missing)} - they have not uploaded, their tasks are not "
                  "here; say so.", ""]
    return "\n".join(lines)


def day_text(label, mine, theirs, now, zone, known_hosts=(), lang="en"):
    """The day's recap without a reviewer - the owner gets only what the plugin knows: what is in progress by hub and in the
    agents' backlogs. No ids and paths, up to ~15 lines; the line about Jira (why it is missing) is added by the caller."""
    w = DAYW[norm(lang)]
    lines = [w["head"].format(date=day_date(label, lang))]
    snaps = ([(w["mine"], mine)] if mine else []) + [(w["hub"].format(name=_hub(h)), s) for h, s in theirs.items()]
    for name, snap in snaps:
        tasks = snap.get("tasks") or []
        stale = w["stale"] if now - (snap.get("ts") or 0) >= DAY_STALE else ""
        lines.append(w["count"].format(name=name, n=len(tasks), stale=stale) if tasks
                     else w["none"].format(name=name, stale=stale))
        for t in tasks[:DAY_TOP]:
            lines.append(w["task"].format(text=_cut(t.get("text"), 90) or t["session"], who=t["session"],
                                          state=w["states"].get(t["state"], ""), age=_age(t.get("created"), now, lang)))
        if len(tasks) > DAY_TOP:
            lines.append(w["more"].format(n=len(tasks) - DAY_TOP))
        back = [(slug, x) for slug, xs in (snap.get("backlog") or {}).items() for x in xs]
        if back:
            lines.append(w["backlog"].format(n=len(back), dirs=", ".join(sorted({s for s, _ in back}))))
    seen = {h.split(".")[0] for h in theirs} | ({mine["host"].split(".")[0]} if mine else set())
    lines += [w["nosnap"].format(name=_hub(h)) for h in sorted(known_hosts) if h.split(".")[0] not in seen]
    return "\n".join(lines)


def day_prompt(label, digest_path, out_path, jira, lang="en"):
    """The task for whoever writes the day's recap (the analyst, no analyst - the hub): Jira + the plugin's digest -> a short text for the owner."""
    where = (f"Jira (MCP atlassian, read only): site {jira.get('url')}, projects {', '.join(jira.get('projects') or []) or 'all'}. "
             if jira else "Jira (MCP atlassian, read only). ")
    return (
        f"[week-recap] Daily recap {label}: what the owner has in progress right now. The plugin's material is `{digest_path}`: "
        "the tasks of all their hubs (this Mac and Windows) and the agents' (directions') backlogs, the long-hanging first.\n"
        f"1. {where}The open tasks on it (not Done; JQL like `assignee = currentUser() AND statusCategory != Done ORDER BY "
        "updated`), what hangs longest and what waits for them. No access - `hub tools request atlassian \"daily recap\"` and "
        "a line in the text that Jira is not in.\n"
        "2. Merge the hubs' tasks and the agents' backlogs into their human affairs (one affair - one line, a duplicate of one "
        "piece of work on two hubs - one line). What hangs longest goes first. Old snapshots and hubs with no snapshot - one line.\n"
        "3. The text: up to ~15 lines, three blocks - hubs, Jira, agents; short, the way a colleague says in the morning what "
        "stands where. No ids (dN, mN), paths, branches, model names or how the hub works.\n"
        f"{_write_in_text(lang)}\n"
        f"The result is the file `{out_path}`: JSON {{\"text\": \"<the recap as one text, lines separated by \\n>\"}}. "
        "The result of your turn is one line: \"done\".")


def _write_in_text(lang):
    return f"Write the text in {LANGUAGE[norm(lang)]}."


def day_valid(data):
    return isinstance(data, dict) and bool(str(data.get("text") or "").strip())
