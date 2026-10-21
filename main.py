"""Process of the "Week" plugin: once a week (by default Monday 9:00 local time of the machine; "mon start" - at the first
start of the hub on Monday; changed by the post "schedule: ..." in the plugin channel - from the Week tab) collects the
work of the last week from the hub journal and hands it to the review: the analyst (no analyst - the hub) writes a
draft JSON with Jira, Granola and Teams, the writer rewrites the texts in a human voice, the plugin puts them into the
designer's HTML and sends it to the owner. Only reads the journal; writes facts into the core's spool. Its own files
are in <hub state>/week-recap/.

The prompts to the models are English and ask for the report in the hub language (core.i18n.current(), then env
HUB_LANG, then en); the notifications to the owner and the report's labels are en/ru the same way. The language is
resolved at the moment of use.

Daily recap: every day (by default 9:00 local time of the machine, at the first tick after that if the hub was down; changed by the post
"day: 08:30 [zone]" or "day: off" in the plugin channel) - what is in progress now on all hubs (this one from the core
state, the others from hub-sync/work/<machine>/open.json), in Jira and with the agents (direction backlogs). The analyst
(no analyst - the hub) writes it, the owner gets one message; the reviewer silent for DAY_WAIT - the plugin's own text goes.

By hand: python3 main.py --week 2026-W41 [--to 2026-10-09] [--dry]   - collect the week's digest (--to - the last day,
Sunday by default; --dry - send nothing)
        python3 main.py --day [2026-10-21] [--dry] [--force]       - the daily recap (--dry - the text on screen, send nothing;
--force - once more for a day that already went)
"""
import datetime
import glob
import json
import os
import shutil
import socket
import sys
import time

from core import catalog, journal, spool

import recap

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("HUB_STATE_DIR") or open(os.path.join(catalog.config_dir(), "state-dir")).read().strip()
NAME = os.environ.get("HUB_EXTENSION", "week-recap")
HOME = os.path.join(STATE_DIR, "week-recap")
OUT = os.path.join(HERE, "web", "data.json")   # for the view - changes a couple of times a week, reloading the tab does no harm
EVERY = 60
WAIT = 6 * 3600          # no draft in 6 h - the owner gets the numbers only
WRITER_WAIT = 3 * 3600   # the writer did not answer in 3 h - the report goes out from the draft
TEMPLATE = os.path.join(HERE, "web", "report-template.html")
SAMPLE = os.path.join(HERE, "web", "sample.json")
OTHERS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(catalog.__file__))), "hub-sync", "work")
REPORTS = os.path.join(HERE, "web", "reports")   # a copy of the report for the link from the hub page
KEEP = 12                # weeks in the tab
STARTED = time.time()    # when the process came up: "mon start" - the first start of the hub on Monday
TELEGRAM = 3500          # a long review - the beginning and the path to the file
DAY_WAIT = 45 * 60       # the reviewer did not answer in 45 min - the daily recap goes without Jira, from what the plugin knows
DAY_KEEP = 30            # days kept in the state

# Texts that go to the owner, by hub language (any language without a table - English); {name} placeholders.
STR = {
    "en": {
        "title": "Week {week}",
        "full": "Full report: `{path}`",
        "numbers_title": "Week {label}: numbers only",
        "numbers_body": "{why}; the week's numbers:\n{numbers}\n\nDigest: `{path}`",
        "not_came": "the recap from {to} did not arrive",
        "nobody": "nobody to write the recap (no analyst and no hub session)",
        "day_title": "In progress · {date}",
        "day_not_came": "No recap from {to} - Jira and the merge of the tasks are not in.",
        "day_nobody": "Nobody to write it - Jira is not in.",
    },
    "ru": {
        "title": "Неделя {week}",
        "full": "Целиком: `{path}`",
        "numbers_title": "Неделя {label}: цифры",
        "numbers_body": "{why}; цифры недели:\n{numbers}\n\nСводка: `{path}`",
        "not_came": "рекап от {to} не пришёл",
        "nobody": "разбирать некому (нет analyst и хаба)",
        "day_title": "Что в работе · {date}",
        "day_not_came": "Рекап от {to} не пришёл — Jira и сведение дел не вошли.",
        "day_nobody": "Разбирать некому — Jira не вошла.",
    },
}


def _lang():
    """Hub language: core.i18n.current() (hub.json language -> owner language -> en), then env HUB_LANG, then en."""
    try:
        from core import i18n
        return i18n.current()
    except Exception:   # noqa: BLE001 - an old core without languages
        return os.environ.get("HUB_LANG") or "en"


def tr(lang, key, **params):
    """Text of a key in the language (any language without a table - English); {name} placeholders."""
    text = STR[recap.norm(lang)][key]
    return text.format(**params) if params else text


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
    """Session -> project (hub settings: projects, the session folder inside the project folder) or its own name."""
    try:
        projects = (catalog.load_config(STATE_DIR) or {}).get("projects") or {}
    except Exception:   # noqa: BLE001 - no settings - session names
        projects = {}
    out = {}
    for name, s in ((state or {}).get("sessions") or {}).items():
        cwd = os.path.realpath(s.get("cwd") or "")
        hit = [p for p, v in projects.items() if isinstance(v, dict) and v.get("path")
               and cwd.startswith(os.path.realpath(os.path.expanduser(v["path"])))]
        out[name] = max(hit, key=len) if hit else name
    for name, s in ((state or {}).get("sessions") or {}).items():   # an executor works for the topic of the session that raised it
        if s.get("role") == "executor" and s.get("raised_by") in out:
            out[name] = out[s["raised_by"]]
    return out


def agents_of(state):
    """Names of the agents (specialists) of this hub: their executors and other machines' copies are named <agent>-<what>."""
    return {n for n, s in ((state or {}).get("sessions") or {}).items() if s.get("role") == "agent"}


def machines():
    """Machines the hub sync knows: hub-sync/agents/*/machines/<machine>.json."""
    names = glob.glob(os.path.join(os.path.dirname(OTHERS), "agents", "*", "machines", "*.json"))
    return {os.path.basename(p)[:-5] for p in names}


def platforms():
    """Machine -> display name (Mac, Windows): by the folders its sessions reported (hub-sync/agents/*/machines/<machine>.json);
    ours - by the system this plugin runs on."""
    me = socket.gethostname()
    out = {me: {"darwin": "Mac", "win32": "Windows"}.get(sys.platform, "Linux")}
    for path in glob.glob(os.path.join(os.path.dirname(OTHERS), "agents", "*", "machines", "*.json")):
        host = os.path.basename(path)[:-5]
        if host in out or host.split(".")[0] == me.split(".")[0]:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        named = recap.platform_of(data.get("cwd") if isinstance(data, dict) else None)
        if named:
            out[host] = named
    return out


def collect(label, zone, state_dir=STATE_DIR, home=HOME, state=None, until=None, others=None):
    """The week's digests into files: (the week's record, path to the digest). until - the last day (2026-10-09),
    otherwise the whole week; others - exports of other hubs (hub-sync/work), their work goes into the same digest."""
    start, end, label = recap.week_of(label, zone, until)
    mine = recap.events(recap.journals(state_dir), start, end)
    theirs, their_where = recap.other_hubs(others or OTHERS, start, end, socket.gethostname())
    evs = recap.merged(mine, theirs)
    m = recap.measure(evs)
    where = {**where_of(state), **their_where}
    where.update(recap.fold_agents({e.get("entity") for e in evs if e.get("type") == "turn.result"}, where,
                                   agents_of(state)))
    me = socket.gethostname()
    work = recap.items(evs, end, where, me)
    names = platforms()
    got = sorted({str(e["seq"]).split(":")[0] for e in theirs})
    folder = os.path.join(home, label)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "digest.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(recap.digest(label, start, end, m, zone))
    work_path = os.path.join(folder, "work.md")
    with open(work_path, "w", encoding="utf-8") as f:
        f.write(recap.work_digest(label, start, end, work, zone, names=names, note=recap.hubs_note(
            machines(), theirs, me)))
    return {"start": start, "end": end, "numbers": m["numbers"], "digest": path, "work": work_path,
            "machines": recap.machine_report(work, names, me, got, machines()),
            "draft": os.path.join(folder, "draft.json"), "final": os.path.join(folder, "recap.json"),
            "report": os.path.join(folder, "report.html"), "recap": os.path.join(folder, "recap.json")}, path


def jira_config():
    try:
        j = (catalog.load_config(STATE_DIR) or {}).get("jira")
    except Exception:   # noqa: BLE001 - no settings: the reviewer looks for itself
        j = None
    return {k: j[k] for k in ("url", "projects") if j.get(k)} if isinstance(j, dict) else None


def day_material(label, zone, now, state, state_dir=STATE_DIR, home=HOME, others=None):
    """The day's digest into a file: (the day's record, this hub's snapshot, the others' snapshots). No open_snapshot in the core - None."""
    lang = recap.norm(_lang())
    mine = recap.open_now(state, now, os.path.join(state_dir, "directions"))
    theirs = recap.other_open(others or OTHERS, socket.gethostname())
    if mine is None and not theirs:
        return None
    folder = os.path.join(home, "day", label)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "open.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(recap.day_digest(label, mine, theirs, now, zone, machines(), jira_config()))
    return {"digest": path, "final": os.path.join(folder, "recap.json"),
            "lang": lang, "text": recap.day_text(label, mine, theirs, now, zone, machines(), lang)}, mine, theirs


def start_day(label, zone, now, mine, state, send=put, state_dir=STATE_DIR, home=HOME, others=None):
    """The daily recap: the digest -> the reviewer (the analyst, no analyst - the hub; nobody - the plugin's text at once). False - nothing to send."""
    got = day_material(label, zone, now, state, state_dir, home, others)
    if not got:
        return False
    day = got[0]
    to = recap.reviewer(state)
    day.update(to=to, sent=now)
    if to:
        send("message.received", None, {"to": to, "text": recap.day_prompt(label, day["digest"], day["final"], jira_config(), day["lang"]),
                                        "channel": "session", "owner": False, "sender": NAME})
    else:
        day["sent"] = now - DAY_WAIT   # nobody to write it - at once what there is
    mine.setdefault("days", {})[label] = day
    return True


def notify_day(label, day, text, now, send=put):
    lang = day.get("lang") or _lang()
    send("owner.notify", None, {"title": tr(lang, "day_title", date=recap.day_date(label, lang)), "text": text.strip(),
                                "source": NAME, "channel": "cli", "owner": True})
    day.update(notified=now, shown=text.strip())


def finish_days(now, mine, send=put):
    """The recap came - to the owner; did not come in DAY_WAIT - the plugin's text. True - something changed."""
    changed = False
    for label, day in (mine.get("days") or {}).items():
        if day.get("notified"):
            continue
        try:
            with open(day["final"], encoding="utf-8") as f:
                data = json.load(f)
            fresh = os.path.getmtime(day["final"]) >= day["sent"] - 1 and recap.day_valid(data)
        except (OSError, ValueError):
            fresh = False
        if fresh:
            notify_day(label, day, str(data["text"]), now, send)
        elif now - day["sent"] >= DAY_WAIT:
            lang = day.get("lang") or _lang()
            why = tr(lang, "day_not_came", to=day["to"]) if day.get("to") else tr(lang, "day_nobody")
            notify_day(label, day, day["text"] + "\n" + why, now, send)
        else:
            continue
        changed = True
    for old in sorted(mine.get("days") or {})[:-DAY_KEEP]:
        del mine["days"][old]
    return changed


def period(w, zone, lang="en"):
    """The week's period for the owner in the language: "Oct 5-11" in English, day first and a genitive month in Russian (months of the language's table)."""
    import datetime
    a, b = (datetime.datetime.fromtimestamp(t, zone) for t in (w["start"], w["end"] - 1))
    months = recap.WORDS[recap.norm(lang)]["months"]
    if recap.norm(lang) == "ru":
        return f"{a.day}–{b.day} {months[b.month - 1]}" if a.month == b.month else \
            f"{a.day} {months[a.month - 1]} – {b.day} {months[b.month - 1]}"
    return f"{months[b.month - 1]} {a.day}–{b.day}" if a.month == b.month else \
        f"{months[a.month - 1]} {a.day} – {months[b.month - 1]} {b.day}"


def start(label, zone, now, mine, state, send=put, state_dir=STATE_DIR, home=HOME, until=None):
    week, _ = collect(label, zone, state_dir, home, state, until)
    to = recap.reviewer(state)
    lang = recap.norm(_lang())   # the language of this week's report: the prompts ask for it, the template labels follow it
    week.update(to=to, sent=now, lang=lang, period=period(week, zone, lang))
    if to:
        send("message.received", None, {"to": to, "text": recap.prompt(label, week["period"], week["digest"], week["work"],
                                                                         week["draft"], SAMPLE, lang),
                                        "channel": "session", "owner": False, "sender": NAME})
    else:
        week["sent"] = now - WAIT   # nobody to review - the numbers at once
    mine["weeks"][label] = week


def fresh_json(path, since):
    """The recap JSON written no earlier than since; missing or not like a recap - None."""
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
    """The report in the designer's HTML (labels in the week's language), a copy for the link from the page, the owner gets the TL;DR and the path."""
    data = {"week": w.get("period") or label, **data, "machines": w.get("machines") or [],
            "lang": recap.norm(w.get("lang") or _lang())}
    with open(TEMPLATE, encoding="utf-8") as f:
        html = recap.render(f.read(), data)
    with open(w["report"], "w", encoding="utf-8") as f:
        f.write(html)
    os.makedirs(REPORTS, exist_ok=True)
    shutil.copyfile(w["report"], os.path.join(REPORTS, f"{label}.html"))
    lang = _lang()
    heads = [f"• {str(x.get('title') or '').strip()}" for x in (data.get("insights") or [])[:3]
             if isinstance(x, dict) and str(x.get("title") or "").strip()]
    send("owner.notify", None, {"title": tr(lang, "title", week=data["week"]),
                                "text": "\n\n".join([str(data.get("tldr") or "").strip(), *(["\n".join(heads)] if heads else []),
                                                     tr(lang, "full", path=w["report"])]),
                                "source": NAME, "channel": "cli", "owner": True})
    w.update(notified=now, text=str(data.get("tldr") or ""), data=data)


def numbers_text(n, lang="en"):
    return "\n".join(f"{recap.NAMES[recap.norm(lang)][k]}: {v if v is not None else '—'}" for k, v in n.items())


def finish(now, mine, state=None, send=put):
    """The draft came - to the writer; the writer's text came (or it is silent for WRITER_WAIT) - the report to the
    owner; no draft within WAIT - the numbers. True - something changed."""
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
                send("message.received", None, {"to": to, "text": recap.writer_prompt(label, w["draft"], w["final"],
                                                                                  w.get("lang") or _lang()),
                                                "channel": "session", "owner": False, "sender": NAME})
            else:
                publish(label, w, draft, now, send)
            changed = True
        elif now - w["sent"] >= WAIT:
            lang = _lang()
            why = tr(lang, "not_came", to=w["to"]) if w.get("to") else tr(lang, "nobody")
            send("owner.notify", None, {"title": tr(lang, "numbers_title", label=label),
                                        "text": tr(lang, "numbers_body", why=why, numbers=numbers_text(w["numbers"], lang),
                                                   path=w["digest"]),
                                        "source": NAME, "channel": "cli", "owner": True})
            w["notified"] = now
            changed = True
    return changed


def view(mine):
    weeks = sorted(mine["weeks"].items(), reverse=True)[:KEEP]
    def stage(w):
        # a code, not a text: the tab words it in its own language
        if w.get("data"):
            return "ready"
        if w.get("notified"):
            return "numbers"
        return "writing" if w.get("writing") else "reviewing"
    return {"weeks": [{"label": k, "start": w["start"], "end": w["end"], "numbers": w["numbers"], "to": w.get("to"),
                       "sent": w.get("sent"), "notified": w.get("notified"), "text": w.get("text"),
                       "tiles": (w.get("data") or {}).get("numbers") or [], "stage": stage(w),
                       "report": f"web/reports/{k}.html" if w.get("data") else None,
                       "recap": w.get("report") or w["recap"], "digest": w["digest"]} for k, w in weeks],
            "when": mine.get("schedule") or recap.DEFAULT}


def tick(now, mine, state, send=put, state_dir=STATE_DIR, home=HOME):
    """One tick: True - its own state changed (write it and refresh the view)."""
    when = recap.schedule(state)
    changed = mine.get("schedule") != when
    mine["schedule"] = when
    zone = recap.tz(when["zone"])
    label = recap.due(now, zone, mine["weeks"], when, STARTED)
    if label:
        start(label, zone, now, mine, state, send, state_dir, home)
        changed = True
    changed = finish(now, mine, state, send) or changed
    day = recap.day_schedule(state)
    dzone = recap.tz(day["zone"] or when["zone"])
    label = recap.day_due(now, dzone, mine.setdefault("days", {}), day)
    if label and start_day(label, dzone, now, mine, state, send, state_dir, home):
        changed = True
    return finish_days(now, mine, send) or changed


def main():
    mine_path = os.path.join(HOME, "state.json")
    mine = read(mine_path, {"weeks": {}})
    if "--day" in sys.argv:
        i = sys.argv.index("--day")
        label = sys.argv[i + 1] if len(sys.argv) > i + 1 and not sys.argv[i + 1].startswith("--") else None
        when = recap.day_schedule(journal.load_state(STATE_DIR))
        zone = recap.tz(when["zone"] or (mine.get("schedule") or recap.DEFAULT)["zone"])
        now = time.time()
        label = label or datetime.datetime.fromtimestamp(now, zone).strftime("%Y-%m-%d")
        state = journal.load_state(STATE_DIR)
        if "--dry" in sys.argv:
            got = day_material(label, zone, now, state)
            print(got[0]["text"] + f"\n\nDigest for the review: {got[0]['digest']}" if got else "nothing to collect: the core has no open_snapshot")
            return
        if label in (mine.get("days") or {}) and "--force" not in sys.argv:
            sys.exit(f"the recap {label} is already sent (again - with --force)")
        if not start_day(label, zone, now, mine, state):
            sys.exit("nothing to collect: the core has no open_snapshot (hub-sync/sync.py) and there are no other hubs' uploads")
        write(mine_path, mine)
        print(f"handed to the review: {mine['days'][label].get('to')}; digest: {mine['days'][label]['digest']}; "
              f"no answer in {DAY_WAIT // 60} min - the plugin's text goes (the plugin's main process sends it)")
        return
    if "--week" in sys.argv:
        label = sys.argv[sys.argv.index("--week") + 1]
        zone = recap.tz((mine.get("schedule") or recap.DEFAULT)["zone"])
        until = sys.argv[sys.argv.index("--to") + 1] if "--to" in sys.argv else None
        if "--dry" in sys.argv:
            week, path = collect(label, zone, state=journal.load_state(STATE_DIR), until=until)
            print(numbers_text(week["numbers"]) + f"\n\nDigest: {path}\nWork: {week['work']}")
            return
        start(label, zone, time.time(), mine, journal.load_state(STATE_DIR), until=until)
        write(mine_path, mine)
        write(OUT, view(mine))
        print(f"handed over for review: {mine['weeks'][label].get('to')}; digest: {mine['weeks'][label]['digest']}")
        return
    write(OUT, view(mine))
    while True:
        mine = read(mine_path, mine)   # a manual run (--week) writes the same file: do not send a week twice
        try:
            if tick(time.time(), mine, journal.load_state(STATE_DIR)):
                write(mine_path, mine)
                write(OUT, view(mine))
        except Exception as e:   # noqa: BLE001 - a failed tick does not kill the process; the hub would restart it, but for nothing
            print(f"week-recap: {e!r}", file=sys.stderr, flush=True)
        time.sleep(EVERY)


if __name__ == "__main__":
    main()
