"""Process of the "Week" plugin: once a week (by default Monday 9:00 local time of the machine; "mon start" - at the first
start of the hub on Monday; changed by the post "schedule: ..." in the plugin channel - from the Week tab) collects the
work of the last week from the hub journal and hands it to the review: the analyst (no analyst - the hub) writes a
draft JSON with Jira, Granola and Teams, the writer rewrites the texts in a human voice, the plugin puts them into the
designer's HTML and sends it to the owner. Only reads the journal; writes facts into the core's spool. Its own files
are in <hub state>/week-recap/.

The prompts to the models are English and ask for the report in the hub language (core.i18n.current(), then env
HUB_LANG, then en); the notifications to the owner and the report's labels are en/ru the same way. The language is
resolved at the moment of use.

By hand: python3 main.py --week 2026-W41 [--to 2026-10-09] [--dry]   - collect the week's digest (--to - the last day,
Sunday by default; --dry - send nothing)
"""
import glob
import json
import os
import shutil
import socket
import sys
import time

from core import catalog, journal, spool, usage

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

# Texts that go to the owner, by hub language (any language without a table - English); {name} placeholders.
STR = {
    "en": {
        "title": "Week {week}",
        "full": "Full report: `{path}`",
        "numbers_title": "Week {label}: numbers only",
        "numbers_body": "{why}; the week's numbers:\n{numbers}\n\nDigest: `{path}`",
        "not_came": "the recap from {to} did not arrive",
        "nobody": "nobody to write the recap (no analyst and no hub session)",
    },
    "ru": {
        "title": "Неделя {week}",
        "full": "Целиком: `{path}`",
        "numbers_title": "Неделя {label}: цифры",
        "numbers_body": "{why}; цифры недели:\n{numbers}\n\nСводка: `{path}`",
        "not_came": "рекап от {to} не пришёл",
        "nobody": "разбирать некому (нет analyst и хаба)",
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
    return out


def machines():
    """Machines the hub sync knows: hub-sync/agents/*/machines/<machine>.json."""
    names = glob.glob(os.path.join(os.path.dirname(OTHERS), "agents", "*", "machines", "*.json"))
    return {os.path.basename(p)[:-5] for p in names}


def collect(label, zone, state_dir=STATE_DIR, home=HOME, state=None, until=None, others=None):
    """The week's digests into files: (the week's record, path to the digest). until - the last day (2026-10-09),
    otherwise the whole week; others - exports of other hubs (hub-sync/work), their work goes into the same digest."""
    start, end, label = recap.week_of(label, zone, until)
    mine = recap.events(recap.journals(state_dir), start, end)
    theirs, their_where = recap.other_hubs(others or OTHERS, start, end, socket.gethostname())
    evs = recap.merged(mine, theirs)
    m = recap.measure(evs)
    try:
        paid, _ = usage.spends(usage.facts(state_dir))
        spent = {}
        by_ts = {(x["session"], round(x["ts"], 3)): x["usd"] for x in paid}
        for e in evs:
            if e.get("type") == "turn.result":
                spent[e["seq"]] = by_ts.get((e.get("entity"), round(e.get("ts") or 0, 3)), 0.0)
    except Exception:   # noqa: BLE001 - no prices - tokens only
        spent = {}
    work = recap.items(evs, end, spent, {**where_of(state), **their_where})
    folder = os.path.join(home, label)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "digest.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(recap.digest(label, start, end, m, zone))
    work_path = os.path.join(folder, "work.md")
    with open(work_path, "w", encoding="utf-8") as f:
        f.write(recap.work_digest(label, start, end, work, zone, note=recap.hubs_note(
            machines(), theirs, socket.gethostname())))
    return {"start": start, "end": end, "numbers": m["numbers"], "digest": path, "work": work_path,
            "draft": os.path.join(folder, "draft.json"), "final": os.path.join(folder, "recap.json"),
            "report": os.path.join(folder, "report.html"), "recap": os.path.join(folder, "recap.json")}, path


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
    data = {"week": w.get("period") or label, **data, "lang": recap.norm(w.get("lang") or _lang())}
    with open(TEMPLATE, encoding="utf-8") as f:
        html = recap.render(f.read(), data)
    with open(w["report"], "w", encoding="utf-8") as f:
        f.write(html)
    os.makedirs(REPORTS, exist_ok=True)
    shutil.copyfile(w["report"], os.path.join(REPORTS, f"{label}.html"))
    lang = _lang()
    send("owner.notify", None, {"title": tr(lang, "title", week=data["week"]),
                                "text": f"{str(data.get('tldr') or '').strip()}\n\n{tr(lang, 'full', path=w['report'])}",
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
    return finish(now, mine, state, send) or changed


def main():
    mine_path = os.path.join(HOME, "state.json")
    mine = read(mine_path, {"weeks": {}})
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
