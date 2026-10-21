"""Week review on a made-up journal: week borders, repeats from archives, numbers, who gets what and when, what the owner
gets, and the en/ru switching of everything the owner reads. Run: python3 -m unittest discover -s tests (with PYTHONPATH
pointing at the hub code)."""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zoneinfo
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
# always our own folder: in a hub session HUB_STATE_DIR is the live hub, the test would overwrite its digest (12.10, digest W41)
os.environ["HUB_STATE_DIR"] = tempfile.mkdtemp(prefix="wr-state-")

import main   # noqa: E402
import recap  # noqa: E402

main.REPORTS = tempfile.mkdtemp(prefix="wr-reports-")   # report copies - not into the plugin folder
main.OTHERS = tempfile.mkdtemp(prefix="wr-others-")     # exports of another hub - not from the live repo

ZONE = zoneinfo.ZoneInfo("Europe/Berlin")
CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def at(y, mo, d, h=12, mi=0):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=ZONE).timestamp()


def say(lang):
    """Pin the hub language the plugin sees (it resolves it at the moment of use)."""
    return mock.patch.object(main, "_lang", lambda: lang)


def read(*path):
    with open(os.path.join(HERE, *path), encoding="utf-8") as f:
        return f.read()


MON = at(2026, 10, 5, 0)
BERLIN_9 = {"id": "p1", "by": "owner", "text": "schedule: mon 09:00 Europe/Berlin", "ts": 0}     # week 2026-W41: 5.10 0:00 - 12.10 0:00

# The owner and the model write in their own language: Russian here, on purpose - it also keeps non-ASCII text going
# through the journal, the digests and the files.
ASK = "сделай отчёт"
DRAFT_TLDR = "Неделя: сделали отчёт"
FINAL_TLDR = "Хорошая неделя: отчёт готов"


def ev(seq, ts, type_, entity=None, **data):
    return {"seq": seq, "ts": ts, "src": "x", "type": type_, "entity": entity, "data": data}


OWNER = {"channel": "web", "owner": True}
WEEK = [
    ev(1, MON - 60, "message.received", to="hub-core", text="last week", **OWNER),
    ev(10, at(2026, 10, 6, 10), "message.received", to="hub-core", text=ASK, **OWNER),
    ev(11, at(2026, 10, 6, 10, 1), "delegation.created", session="analyst", **{"for": "m10"}, context="report"),
    ev(12, at(2026, 10, 6, 10, 2), "turn.result", "hub-core", text="handed to analyst", duration_ms=60000, cost_usd=5),
    ev(13, at(2026, 10, 6, 10, 20), "permission.requested", "analyst", request_id="r1", tool="AskUserQuestion",
       input={"questions": [{"question": "Which month?"}]}),
    ev(14, at(2026, 10, 6, 10, 30), "permission.answered", "analyst", request_id="r1", answers={"Which month?": "September"},
       **OWNER),
    # the hub core words the owner's answer to a form with the Russian marker (recap.ANSWER_MARK)
    ev(15, at(2026, 10, 6, 10, 31), "message.received", to="analyst",
       text=recap.ANSWER_MARK + " q13: September", **OWNER),
    ev(16, at(2026, 10, 6, 11), "permission.requested", "analyst", request_id="r2", tool="Bash", input={}),
    ev(17, at(2026, 10, 6, 11, 30), "turn.result", "analyst", text="report ready", duration_ms=1800000, cost_usd=9),
    ev(18, at(2026, 10, 6, 11, 40), "delegation.done", "d11"),
    ev(19, at(2026, 10, 6, 11, 41), "owner.file", path="/x/report.pdf"),
    ev(20, at(2026, 10, 7, 9), "message.received", to="designer", text="a session wrote to the owner", channel="session",
       owner=False, sender="designer"),
    ev(30, at(2026, 10, 12, 0, 1), "message.received", to="hub-core", text="already the next one", **OWNER),
]


def journal_dir(parts):
    """A state folder: archives and the current journal, the parts overlap, as after a rotation."""
    d = tempfile.mkdtemp(prefix="wr-")
    os.makedirs(os.path.join(d, "journal-archive"))
    for i, part in enumerate(parts):
        name = "journal.jsonl" if i == len(parts) - 1 else os.path.join("journal-archive", f"journal-{i}.jsonl")
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(e) + "\n" for e in part))
    return d


class Isolation(unittest.TestCase):
    def test_tests_never_touch_the_live_hub(self):
        self.assertTrue(main.HOME.startswith(tempfile.gettempdir()), main.HOME)
        self.assertTrue(main.REPORTS.startswith(tempfile.gettempdir()), main.REPORTS)


class Week(unittest.TestCase):
    def test_last_week_is_monday_to_monday_in_the_hub_zone(self):
        start, end, label = recap.last_week(at(2026, 10, 12, 9, 5), ZONE)
        self.assertEqual((start, end, label), (MON, at(2026, 10, 12, 0), "2026-W41"))
        self.assertEqual(recap.week_of("2026-W41", ZONE), (start, end, label))

    def test_due_on_monday_from_nine_once(self):
        self.assertIsNone(recap.due(at(2026, 10, 12, 8, 59), ZONE, {}))
        self.assertEqual(recap.due(at(2026, 10, 12, 9), ZONE, {}), "2026-W41")
        self.assertEqual(recap.due(at(2026, 10, 14, 15), ZONE, {}), "2026-W41")   # the hub was down on Monday
        self.assertIsNone(recap.due(at(2026, 10, 12, 9), ZONE, {"2026-W41": {}}))

    def test_due_on_the_chosen_day_and_time(self):
        when = {"day": 1, "hour": 10, "minute": 30, "zone": None}    # Tuesday 10:30
        self.assertIsNone(recap.due(at(2026, 10, 13, 10, 29), ZONE, {}, when))
        self.assertEqual(recap.due(at(2026, 10, 13, 10, 30), ZONE, {}, when), "2026-W41")
        self.assertIsNone(recap.due(at(2026, 10, 12, 23), ZONE, {}, when))


class Schedule(unittest.TestCase):
    def test_default_is_monday_nine_in_the_machine_zone(self):
        self.assertEqual(recap.schedule({}), recap.DEFAULT)
        self.assertEqual(recap.DEFAULT, {"day": 0, "hour": 9, "minute": 0, "zone": None})
        self.assertEqual(recap.tz(None).utcoffset(datetime.datetime.now()),
                         datetime.datetime.now().astimezone().utcoffset())   # the machine's local time, not the hub zone

    def test_the_owners_last_valid_post_sets_it(self):
        feed = [{"by": "owner", "text": "schedule: tue 10:30"},
                {"by": "owner", "text": "schedule: fri 25:00"},              # invalid - not taken
                {"by": "designer", "text": "schedule: sat 07:00"},           # not the owner - not taken
                {"by": "owner", "text": "just a note"}]
        self.assertEqual(recap.schedule({"feeds": {"week-recap": feed}}), {"day": 1, "hour": 10, "minute": 30, "zone": None})
        feed.append({"by": "owner", "text": "schedule: wed 08:05 Asia/Bangkok"})
        self.assertEqual(recap.schedule({"feeds": {"week-recap": feed}})["zone"], "Asia/Bangkok")
        feed.append({"by": "owner", "text": "schedule: wed 08:05 Nowhere/City"})   # an unknown zone - not taken
        self.assertEqual(recap.schedule({"feeds": {"week-recap": feed}})["zone"], "Asia/Bangkok")

    def test_hub_session_may_set_it_for_the_owner(self):
        st = {"sessions": {"hub-core": {"kind": "hub"}}, "feeds": {"week-recap": [{"by": "hub-core", "text": "schedule: thu 18:00"}]}}
        self.assertEqual(recap.schedule(st)["day"], 3)

    def test_the_russian_words_are_accepted_whatever_the_hub_language(self):
        """A Russian hub's tab posts the Russian words, and old posts stay valid: both forms mean the same."""
        def get(text):
            return recap.schedule({"feeds": {"week-recap": [{"by": "owner", "text": text}]}})
        for en, ru in (("schedule: tue 10:30", "расписание: вт 10:30"),
                       ("schedule: sun 00:05 Asia/Bangkok", "расписание: вс 00:05 Asia/Bangkok"),
                       ("schedule: mon start", "расписание: пн старт")):
            self.assertEqual(get(en), get(ru), en)
            self.assertNotEqual(get(en), recap.DEFAULT, en)
        self.assertEqual(get("Schedule: MON 09:00"), recap.DEFAULT)   # the case does not matter
        self.assertEqual(get("schedule: someday 10:00"), recap.DEFAULT)

    def test_events_of_the_week_once_from_overlapping_journals(self):
        d = journal_dir([WEEK[:8], WEEK[5:12], WEEK[10:]])
        got = recap.events(recap.journals(d), MON, at(2026, 10, 12, 0))
        self.assertEqual([e["seq"] for e in got], [e["seq"] for e in WEEK[1:12]])


class Numbers(unittest.TestCase):
    def setUp(self):
        self.m = recap.measure(WEEK[1:12])

    def test_owner_words_forms_tasks_and_waits(self):
        n = self.m["numbers"]
        self.assertEqual(n["owner_messages"], 1)              # the answer to a form and a session's words are not the owner's request
        self.assertEqual(len(self.m["answers"]), 1)
        self.assertEqual((n["forms"], n["forms_answered"], n["form_answer_median_min"]), (1, 1, 10.0))
        self.assertEqual(n["permission_requests"], 1)
        self.assertEqual((n["tasks"], n["tasks_done"]), (1, 1))
        self.assertEqual(n["wait_median_min"], 2.0)           # to that session's first reply
        self.assertEqual(n["task_median_min"], 100.0)         # from the request to the delivery of the task made by it
        self.assertEqual(n["files_sent"], 1)
        self.assertEqual(n["busy_hours"], 0.5)
        self.assertNotIn("cost_usd", n)                       # cost_usd in the results is cumulative, the sum lies

    def test_digest_keeps_the_owner_words_in_time_order(self):
        text = recap.digest("2026-W41", MON, at(2026, 10, 12, 0), self.m, ZONE)
        self.assertIn(f"owner → hub-core (m10, reply in 2.0 min): {ASK}", text)
        self.assertIn("task d11 → analyst (for m10): report — delivered", text)
        self.assertIn("form q13 from analyst: Which month? → answered in 10 min: September", text)
        self.assertLess(text.index(ASK), text.index("form q13"))

    def test_the_answer_marker_is_the_cores_russian_wording(self):
        self.assertEqual(recap.ANSWER_MARK, "Ответ владельца на вопрос")   # spelled by code points in recap.py


class Flow(unittest.TestCase):
    def setUp(self):
        self.state = journal_dir([WEEK])
        self.home = os.path.join(self.state, "week-recap")
        self.sent, self.mine = [], {"weeks": {}}
        self.send = lambda *a: self.sent.append(a)
        pin = say("en")
        pin.start()
        self.addCleanup(pin.stop)

    def tick(self, now, sessions, feed=(BERLIN_9,)):
        return main.tick(now, self.mine, {"sessions": sessions, "feeds": {"week-recap": list(feed)}}, self.send,
                         self.state, self.home)

    def write(self, path, data, ts):
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False)
        os.utime(path, (ts, ts))   # the test's clock, not the real one

    DRAFT = {"tldr": DRAFT_TLDR, "numbers": [{"label": "Tasks done", "value": "1"}],
             "done": [{"title": "Report for September"}], "todo": {"filed": [], "not_filed": []}}

    def test_analyst_drafts_writer_humanizes_owner_gets_the_report_once(self):
        fleet = {"analyst": {"role": "agent", "state": "idle"}, "writer": {"role": "agent", "state": "idle"},
                 "hub-core": {"kind": "hub", "state": "idle"}}
        self.tick(at(2026, 10, 12, 8), fleet)                            # the schedule is remembered, before 9:00 - nothing
        self.assertEqual(self.sent, [])
        self.assertTrue(self.tick(at(2026, 10, 12, 9), fleet))
        (kind, _, data), = self.sent
        week = self.mine["weeks"]["2026-W41"]
        self.assertEqual((kind, data["to"], data["sender"]), ("message.received", "analyst", "week-recap"))
        for part in (week["digest"], week["work"], week["draft"], "atlassian", "granola", "ms365", "commits"):
            self.assertIn(part, data["text"])
        with open(week["work"]) as f:
            work = f.read()
        self.assertIn(ASK, work)                                        # the owner's discussion
        self.assertIn("task: report", work)                             # and the task made by it - with tokens
        self.assertFalse(self.tick(at(2026, 10, 12, 9, 1), fleet))       # does not send a second time
        self.write(week["draft"], {"no": "tldr"}, at(2026, 10, 12, 9, 20))   # not like a recap - wait on
        self.assertFalse(self.tick(at(2026, 10, 12, 9, 25), fleet))
        self.write(week["draft"], self.DRAFT, at(2026, 10, 12, 9, 30))
        self.assertTrue(self.tick(at(2026, 10, 12, 9, 31), fleet))
        kind, _, data = self.sent[-1]
        self.assertEqual((kind, data["to"]), ("message.received", "writer"))
        self.assertIn(week["draft"], data["text"])
        self.assertIn(week["final"], data["text"])
        final = dict(self.DRAFT, tldr=FINAL_TLDR)
        self.write(week["final"], final, at(2026, 10, 12, 10))
        self.assertTrue(self.tick(at(2026, 10, 12, 10, 1), fleet))
        kind, _, data = self.sent[-1]
        self.assertEqual((kind, data["owner"]), ("owner.notify", True))
        self.assertEqual(data["title"], "Week Oct 5–11")
        self.assertIn("Хорошая неделя", data["text"])
        self.assertIn(week["report"], data["text"])
        with open(week["report"]) as f:
            html = f.read()
        self.assertIn(FINAL_TLDR, html)
        self.assertIn("Oct 5–11", html)
        self.assertFalse(self.tick(at(2026, 10, 12, 18), fleet))
        self.assertEqual(len(self.sent), 3)
        v = main.view(self.mine)
        self.assertEqual((v["weeks"][0]["stage"], v["weeks"][0]["report"]), ("ready", "web/reports/2026-W41.html"))
        self.assertEqual(v["weeks"][0]["tiles"][0]["value"], "1")

    def test_silent_writer_the_draft_goes_out(self):
        fleet = {"analyst": {"role": "agent", "state": "idle"}, "writer": {"role": "agent", "state": "idle"}}
        self.tick(at(2026, 10, 12, 9), fleet)
        week = self.mine["weeks"]["2026-W41"]
        self.write(week["draft"], self.DRAFT, at(2026, 10, 12, 9, 30))
        self.tick(at(2026, 10, 12, 9, 31), fleet)
        self.assertFalse(self.tick(at(2026, 10, 12, 12, 30), fleet))
        self.assertTrue(self.tick(at(2026, 10, 12, 12, 32), fleet))
        self.assertIn(DRAFT_TLDR, self.sent[-1][2]["text"])

    def test_no_analyst_goes_to_the_hub(self):
        self.tick(at(2026, 10, 12, 9), {"hub-core": {"kind": "hub", "state": "idle"}})
        self.assertEqual(self.sent[0][2]["to"], "hub-core")

    def test_no_recap_in_six_hours_sends_the_numbers(self):
        self.tick(at(2026, 10, 12, 9), {"analyst": {"role": "agent", "state": "idle"}})
        self.assertFalse(self.tick(at(2026, 10, 12, 14, 59), {}))
        self.assertTrue(self.tick(at(2026, 10, 12, 15), {}))
        data = self.sent[-1][2]
        self.assertEqual(data["title"], "Week 2026-W41: numbers only")
        self.assertIn("the recap from analyst did not arrive; the week's numbers:", data["text"])
        self.assertIn("owner messages: 1", data["text"])

    def test_old_draft_is_not_this_weeks_answer(self):
        os.makedirs(os.path.join(self.home, "2026-W41"))
        self.write(os.path.join(self.home, "2026-W41", "draft.json"), self.DRAFT, at(2026, 10, 1))
        self.tick(at(2026, 10, 12, 9), {"analyst": {"role": "agent", "state": "idle"}})
        self.assertFalse(self.tick(at(2026, 10, 12, 9, 1), {}))


class Work(unittest.TestCase):
    def test_tokens_go_to_the_task_or_the_discussion_and_lively_silence_is_faded(self):
        d = {"channel": "web", "owner": True}
        evs = [ev(0, at(2026, 10, 5, 9, 30), "turn.result", "gen", tokens_in=5, tokens_out=0),   # before any request - its own
               ev(1, at(2026, 10, 5, 10), "message.received", to="gen", text="make a banner", **d),
               ev(2, at(2026, 10, 5, 10, 5), "turn.result", "gen", tokens_in=1000, tokens_out=10),
               ev(3, at(2026, 10, 5, 11), "message.received", to="gen", text="edits", **d),
               ev(4, at(2026, 10, 5, 12), "message.received", to="gen", text="one more", **d),
               ev(5, at(2026, 10, 5, 12, 5), "turn.result", "gen", tokens_in=500, tokens_out=0),
               ev(6, at(2026, 10, 6, 9), "delegation.created", session="pe", context="prompts", **{"for": "m1"}),
               ev(7, at(2026, 10, 6, 9, 30), "turn.result", "pe", tokens_in=300, tokens_out=0),
               ev(8, at(2026, 10, 6, 9, 31), "delegation.done", "d6"),
               ev(9, at(2026, 10, 6, 9, 40), "turn.result", "pe", tokens_in=7, tokens_out=0),
               ev(10, at(2026, 10, 9, 10), "message.received", to="gen", text="a new one", **d)]
        work = {x["id"]: x for x in recap.items(evs, at(2026, 10, 12, 0), {2: 1.5}, {"gen": "Generation"})}
        m1 = work["m1"]
        self.assertEqual((m1["owner_msgs"], m1["tokens"], m1["usd"], m1["where"], m1["faded"]),
                         (3, 1510, 1.5, "Generation", True))
        self.assertEqual((m1["kind"], work["d6"]["kind"], work["s:gen"]["kind"]), ("discussion", "task", "own"))
        self.assertEqual((work["d6"]["tokens"], bool(work["d6"]["done"])), (300, True))
        self.assertEqual(work["s:pe"]["tokens"], 7)                     # after the delivery - its own, not the task
        self.assertFalse(work["m10"]["faded"])                          # one word is not an active discussion
        self.assertEqual((work["s:gen"]["tokens"], work["s:gen"]["owner_msgs"]), (5, 0))   # the owner's words are not "its own"

    def test_a_tool_built_for_a_topic_counts_to_that_topic(self):
        """A hub session's task set by a working session goes to that session's topic."""
        evs = [ev(1, at(2026, 10, 6, 9), "delegation.created", session="hub-dev", context="script: upscale 2048",
                  **{"for": None, "for_session": "gen"}),
               ev(2, at(2026, 10, 6, 9, 30), "turn.result", "hub-dev", tokens_in=900, tokens_out=0),
               ev(3, at(2026, 10, 6, 9, 31), "delegation.done", "d1"),
               ev(4, at(2026, 10, 6, 10), "delegation.created", session="hub-dev", context="core: watchdog",
                  **{"for": "m0", "for_session": None})]
        where = {"gen": "Generative styles", "hub-dev": "hub-dev"}
        work = {x["id"]: x for x in recap.items(evs, at(2026, 10, 12, 0), {}, where)}
        self.assertEqual(work["d1"]["where"], "Generative styles")
        self.assertEqual(work["d4"]["where"], "hub-dev")                    # the owner's request - by session
        theirs = recap._theirs(evs[0], "Win")
        self.assertEqual(theirs["data"]["for_session"], "gen@Win")          # another hub - its session

    def test_the_prompt_splits_hub_setup_by_topic(self):
        text = recap.prompt("2026-W41", "Oct 5–9", "/d.md", "/w.md", "/draft.json", "/sample.json")
        self.assertIn("an animator - to animation", text)
        self.assertIn("belongs to the topic it serves", text)

    def test_every_project_is_in_the_work_digest_even_a_small_one(self):
        """14.10: of 118 lines of the week the first 60 by tokens were almost all work on the hub; ads went into "the rest"."""
        work = [{"id": f"m{i}", "kind": "discussion", "session": "dev", "where": "Hub", "topic": f"edit {i}",
                 "first": MON, "last": MON, "owner_msgs": 1, "tokens": 10_000_000, "usd": 1.0, "turns": 1,
                 "faded": False} for i in range(80)]
        work.append({"id": "d99", "kind": "task", "session": "ads-x", "where": "Ads", "topic": "tracker videos",
                     "first": MON, "last": MON, "owner_msgs": 0, "tokens": 1000, "usd": 0.1, "turns": 2, "done": MON})
        text = recap.work_digest("2026-W41", MON, at(2026, 10, 12, 0), work, ZONE)
        self.assertIn("## Ads", text)
        self.assertIn("tracker videos", text)
        self.assertLess(text.index("## Hub"), text.index("## Ads"))   # the big one is higher
        self.assertIn("the rest: ", text)                              # the big one has its tail folded

    def test_the_period_may_end_before_sunday(self):
        start, end, label = recap.week_of("2026-W41", ZONE, "2026-10-09")
        self.assertEqual((start, end, label), (MON, at(2026, 10, 10, 0), "2026-W41"))
        self.assertEqual(main.period({"start": start, "end": end}, ZONE), "Oct 5–9")
        self.assertEqual(main.period({"start": start, "end": end}, ZONE, "ru"), "5–9 октября")

    def test_the_other_hubs_week_joins_without_mixing_sessions(self):
        """Two hubs through GitHub (hub-sync/work/<machine>/): the recap covers the work of both."""
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "Win"))
        other = [{"type": "hub.where", "data": {"ads": "Ads"}},
                 ev(1, at(2026, 10, 6, 10), "message.received", to="ads", text="tracker video", **OWNER),
                 ev(2, at(2026, 10, 6, 10, 5), "delegation.created", session="analyst", context="research", **{"for": "m1"}),
                 ev(3, at(2026, 10, 6, 11), "turn.result", "analyst", text="1", tokens_in=700, tokens_out=0),
                 ev(4, at(2026, 10, 6, 11, 1), "delegation.done", "d2"),
                 ev(5, at(2026, 10, 13, 10), "message.received", to="ads", text="next week", **OWNER)]
        with open(os.path.join(d, "Win", "2026-W41.jsonl"), "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(e) + "\n" for e in other))
        with open(os.path.join(d, "Win", "2026-W42.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(other[0]) + "\n" + json.dumps(other[-1]) + "\n")
        evs, where = recap.other_hubs(d, MON, at(2026, 10, 12, 0), "Mac")
        self.assertEqual(where, {"ads@Win": "Ads"})
        mine = [ev(2, at(2026, 10, 6, 9), "delegation.created", session="analyst", context="own", **{"for": None}),
                ev(9, at(2026, 10, 6, 12), "turn.result", "analyst", tokens_in=5, tokens_out=0)]
        work = {x["id"]: x for x in recap.items(recap.merged(mine, evs), at(2026, 10, 12, 0), {}, where)}
        self.assertEqual((work["mWin:1"]["where"], work["mWin:1"]["topic"]), ("Ads", "tracker video"))
        self.assertEqual((work["dWin:2"]["tokens"], bool(work["dWin:2"]["done"])), (700, True))
        self.assertEqual((work["d2"]["tokens"], work["d2"]["done"]), (5, None))   # the analyst's own task - ours
        self.assertNotIn("next week", json.dumps(evs, ensure_ascii=False))
        self.assertEqual(recap.measure(recap.merged(mine, evs))["numbers"]["owner_messages"], 1)
        self.assertEqual(recap.other_hubs(d, MON, at(2026, 10, 12, 0), "Win"), ([], {}))   # our own file is not another's
        self.assertEqual(recap.other_hubs(d, MON, at(2026, 10, 12, 0), "Win.local"), ([], {}))
        note = recap.hubs_note({"Win", "Mac.local", "Old"}, evs, "Mac")
        self.assertIn("hub Win is included", note)
        self.assertIn("hub Old did not export its work", note)
        self.assertNotIn("Mac", note)
        self.assertIn(note, recap.work_digest("2026-W41", MON, at(2026, 10, 12, 0), [], ZONE, note=note))


class StartMode(unittest.TestCase):
    def test_first_start_on_monday_or_nine(self):
        when = {"day": 0, "hour": 0, "minute": 0, "zone": None, "start": True}
        self.assertIsNone(recap.due(at(2026, 10, 12, 7), ZONE, {}, when, started=at(2026, 10, 11, 20)))   # the hub up since night
        self.assertEqual(recap.due(at(2026, 10, 12, 7), ZONE, {}, when, started=at(2026, 10, 12, 6, 59)), "2026-W41")
        self.assertEqual(recap.due(at(2026, 10, 12, 9), ZONE, {}, when, started=at(2026, 10, 11, 20)), "2026-W41")
        self.assertIsNone(recap.due(at(2026, 10, 11, 23), ZONE, {"2026-W40": {}}, when, started=at(2026, 10, 11, 22)))
        feed = [{"by": "owner", "text": "schedule: mon start"}]
        self.assertEqual(recap.schedule({"feeds": {"week-recap": feed}}), when)


class Language(unittest.TestCase):
    """Prompts to the models are English and name the report language; what the owner reads is en/ru by the hub language."""

    def setUp(self):
        self.state = journal_dir([WEEK])
        self.home = os.path.join(self.state, "week-recap")
        self.sent, self.mine = [], {"weeks": {}}
        self.send = lambda *a: self.sent.append(a)

    def tick(self, now, sessions, feed=(BERLIN_9,)):
        return main.tick(now, self.mine, {"sessions": sessions, "feeds": {"week-recap": list(feed)}}, self.send,
                         self.state, self.home)

    def test_the_hub_language_comes_from_the_core_then_the_environment_then_english(self):
        with mock.patch("core.i18n.current", return_value="ru"):
            self.assertEqual(main._lang(), "ru")
        with mock.patch("core.i18n.current", side_effect=RuntimeError("no hub language")):
            with mock.patch.dict(os.environ, {"HUB_LANG": "ru"}):
                self.assertEqual(main._lang(), "ru")
            env = {k: v for k, v in os.environ.items() if k != "HUB_LANG"}
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(main._lang(), "en")

    def test_prompts_are_english_and_name_the_report_language(self):
        args = ("2026-W41", "Oct 5–11", "/d.md", "/w.md", "/draft.json", "/sample.json")
        for lang, name in (("en", "English"), ("ru", "Russian"), ("ru-RU", "Russian"), ("de", "English"), (None, "English")):
            text = recap.prompt(*args, lang)
            self.assertIn(f"in {name};", text, lang)
            self.assertNotRegex(text, CYRILLIC)                             # the prompt itself is English
            text = recap.writer_prompt("2026-W41", "/draft.json", "/recap.json", lang)
            self.assertIn(f"in {name};", text, lang)
            self.assertNotRegex(text, CYRILLIC)
        self.assertIn("in English;", recap.prompt(*args))                   # no language given - English

    def test_the_analyst_is_asked_for_the_hub_language_and_it_is_kept_for_the_week(self):
        fleet = {"analyst": {"role": "agent", "state": "idle"}, "writer": {"role": "agent", "state": "idle"}}
        with say("ru"):
            self.tick(at(2026, 10, 12, 9), fleet)
        (_, _, data), = self.sent
        week = self.mine["weeks"]["2026-W41"]
        self.assertIn("in Russian;", data["text"])
        self.assertIn("(5–11 октября)", data["text"])                       # the period label is in the report language
        self.assertEqual((week["lang"], week["period"]), ("ru", "5–11 октября"))
        draft = {"tldr": "ok", "numbers": [], "todo": {}}
        with open(week["draft"], "w", encoding="utf-8") as f:
            json.dump(draft, f)
        os.utime(week["draft"], (at(2026, 10, 12, 9, 30),) * 2)
        with say("en"):                                                     # the hub language changed meanwhile
            self.tick(at(2026, 10, 12, 9, 31), fleet)
        self.assertIn("in Russian;", self.sent[-1][2]["text"])               # the writer is asked for the same language

    def test_the_recap_for_an_english_hub(self):
        with say("en"):
            self.tick(at(2026, 10, 12, 9), {"analyst": {"role": "agent", "state": "idle"}})
        self.assertIn("in English;", self.sent[0][2]["text"])
        self.assertEqual(self.mine["weeks"]["2026-W41"]["period"], "Oct 5–11")

    def test_numbers_only_goes_out_in_the_language_of_the_moment(self):
        self.tick(at(2026, 10, 12, 9), {"analyst": {"role": "agent", "state": "idle"}})
        with say("ru"):
            self.assertTrue(self.tick(at(2026, 10, 12, 15), {}))
        data = self.sent[-1][2]
        self.assertEqual(data["title"], "Неделя 2026-W41: цифры")
        self.assertIn("рекап от analyst не пришёл; цифры недели:", data["text"])
        self.assertIn("сообщений владельца: 1", data["text"])
        self.assertIn("Сводка: `", data["text"])

    def test_nobody_to_review_in_both_languages(self):
        for lang, why in (("en", "nobody to write the recap"), ("ru", "разбирать некому")):
            self.sent, self.mine = [], {"weeks": {}}
            with say(lang):
                self.assertTrue(self.tick(at(2026, 10, 12, 9), {}))   # nobody to review - the numbers go out at once
            self.assertIn(why, self.sent[-1][2]["text"], lang)

    def test_the_report_carries_its_language_for_the_template(self):
        for lang in ("en", "ru"):
            report = os.path.join(self.home, f"report-{lang}.html")
            os.makedirs(self.home, exist_ok=True)
            w = {"report": report, "period": "x", "lang": lang}
            with say("en"):
                main.publish("2026-W41", w, {"tldr": "ok"}, 1, self.send)
            self.assertEqual(w["data"]["lang"], lang)
            with open(report, encoding="utf-8") as f:
                self.assertRegex(f.read(), r'"lang": "%s"' % lang)
        self.assertEqual(self.sent[0][2]["title"], "Week x")                 # the notification follows the hub language
        with say("ru"):
            main.publish("2026-W41", {"report": report, "period": "x"}, {"tldr": "ok"}, 1, self.send)
        self.assertEqual(self.sent[-1][2]["title"], "Неделя x")
        with open(report, encoding="utf-8") as f:
            self.assertRegex(f.read(), r'"lang": "ru"')                      # a week from an old state follows the hub

    def test_period_label_in_both_languages(self):
        for (a, b), en, ru in (((at(2026, 10, 5, 0), at(2026, 10, 12, 0)), "Oct 5–11", "5–11 октября"),
                               ((at(2026, 10, 26, 0), at(2026, 11, 3, 0)), "Oct 26 – Nov 2", "26 октября – 2 ноября")):
            w = {"start": a, "end": b}
            self.assertEqual((main.period(w, ZONE), main.period(w, ZONE, "ru"), main.period(w, ZONE, "de")), (en, ru, en))

    def test_the_view_gives_a_stage_code_not_a_text(self):
        w = {"start": 1, "end": 2, "numbers": {}, "digest": "d", "recap": "r"}
        for stage, week in (("reviewing", dict(w, to="analyst")), ("writing", dict(w, writing=1)),
                            ("numbers", dict(w, notified=1)), ("ready", dict(w, data={"tldr": "x"}))):
            self.assertEqual(main.view({"weeks": {"2026-W41": week}})["weeks"][0]["stage"], stage)
        self.assertNotIn("names", main.view({"weeks": {}}))                  # the tab words everything itself

    def test_number_names_have_both_languages(self):
        self.assertEqual(set(recap.NAMES["en"]), set(recap.NAMES["ru"]))
        self.assertEqual(set(recap.NAMES["en"]), set(recap.measure([])["numbers"]))
        self.assertEqual(set(main.STR["en"]), set(main.STR["ru"]))
        for k, v in main.STR["en"].items():
            self.assertEqual(set(re.findall(r"\{(\w+)\}", v)), set(re.findall(r"\{(\w+)\}", main.STR["ru"][k])), k)
        self.assertEqual(main.numbers_text({"turns": 3}), "session turns: 3")
        self.assertEqual(main.numbers_text({"turns": 3}, "ru"), "ходов сессий: 3")
        self.assertEqual(main.numbers_text({"turns": 3}, "de"), "session turns: 3")

    def test_what_the_models_read_has_no_russian(self):
        d = {"channel": "web", "owner": True}
        evs = [ev(1, at(2026, 10, 6, 10), "message.received", to="gen", text="make a banner", **d),
               ev(2, at(2026, 10, 6, 10, 5), "turn.result", "gen", text="done", tokens_in=1000, tokens_out=10),
               ev(3, at(2026, 10, 6, 11), "delegation.created", session="pe", context="prompts", **{"for": "m1"}),
               ev(4, at(2026, 10, 6, 12), "permission.requested", "pe", request_id="r", tool="AskUserQuestion",
                  input={"questions": [{"question": "Which size?"}]})]
        end = at(2026, 10, 12, 0)
        work = recap.items(evs, end, {}, {"gen": "Banners"})
        self.assertNotRegex(recap.work_digest("2026-W41", MON, end, work, ZONE, note=recap.hubs_note({"Old"}, [], "Mac")),
                            CYRILLIC)
        self.assertNotRegex(recap.digest("2026-W41", MON, end, recap.measure(evs), ZONE), CYRILLIC)

    def test_manifest_is_english_with_a_russian_translation(self):
        man = json.loads(read("hub-plugin.json"))
        for key in ("description", "why"):
            self.assertNotRegex(man[key], CYRILLIC, key)
            self.assertRegex(man["i18n"]["ru"][key], CYRILLIC, key)
        self.assertEqual(man["version"], "2.1.0")


def node(prog):
    exe = shutil.which("node")
    if not exe:
        raise unittest.SkipTest("no node")
    r = subprocess.run([exe, "-e", prog], capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


def tab_eval(lang, code):
    """Run the table, t() and the schedule helpers of web/main.js in node for a hub.lang; `code` is a JS expression."""
    js = read("web", "main.js")
    src = js[js.index("const STR = {"):js.index("async function load")]
    src += js[js.index("const day = "):js.index("function week(w)")]
    src += js[js.index("const two = "):js.index("function when()")]
    return node(f"const hub = {{ lang: {json.dumps(lang)} }};\n{src}\nconsole.log(JSON.stringify({code}));")


class Tab(unittest.TestCase):
    """The page tab: en by default, ru for a Russian hub, picked from hub.lang at use time."""

    def test_both_tables_have_the_same_keys_and_placeholders(self):
        en, ru = tab_eval("en", "[STR.en, STR.ru]")
        self.assertEqual(set(en), set(ru))
        for k, v in en.items():
            if isinstance(v, str):
                self.assertEqual(set(re.findall(r"\{(\w+)\}", v)), set(re.findall(r"\{(\w+)\}", ru[k])), k)
            else:
                self.assertEqual(len(v), len(ru[k]), k)

    def test_english_is_the_default(self):
        for lang in ("en", "de", "uk", ""):
            self.assertEqual(tab_eval(lang, 't("title")'), "Week", lang)
            self.assertEqual(tab_eval(lang, 'describe({ day: 1, hour: 9, minute: 5, zone: null })'),
                             "Tue 09:05, local time", lang)

    def test_russian_when_the_hub_is_russian(self):
        self.assertEqual(tab_eval("ru", 't("title")'), "Неделя")
        self.assertEqual(tab_eval("ru", 'describe({ day: 0, hour: 9, minute: 0, zone: "Asia/Bangkok" })'),
                         "пн 09:00, Asia/Bangkok")
        self.assertEqual(tab_eval("ru", 'stage({ stage: "reviewing", to: "analyst" })'), "разбирает analyst")
        self.assertEqual(tab_eval("en", 'stage({ stage: "reviewing", to: "analyst" })'), "analyst is reviewing")
        self.assertEqual(tab_eval("en", 'stage({})'), "")

    def test_the_tab_posts_the_schedule_in_its_language_and_the_process_reads_both(self):
        for lang in ("en", "ru"):
            kw, start, days = tab_eval(lang, '[t("keyword"), t("start"), list("days")]')
            for text in (f"{kw}: {days[2]} 08:05", f"{kw}: {days[0]} {start}"):
                self.assertNotEqual(recap.schedule({"feeds": {"week-recap": [{"by": "owner", "text": text}]}}),
                                    recap.DEFAULT, text)

    def test_dates_follow_the_language(self):
        ts = int(datetime.datetime(2026, 10, 5, 12, tzinfo=datetime.timezone.utc).timestamp())
        self.assertEqual(tab_eval("en", f"day({ts})"), "Oct 5")
        self.assertIn("окт", tab_eval("ru", f"day({ts})"))

    def test_language_is_read_at_use_time(self):
        js = read("web", "main.js")
        self.assertIn("hub?.lang", js)                               # read on every call, not once at module load
        self.assertNotRegex(js, r"const lang = hub")


class Template(unittest.TestCase):
    """The report page: English labels by default, Russian when the data says "lang": "ru"."""

    def strings(self):
        html = read("web", "report-template.html")
        src = html[html.index("const STR = {"):html.index("const base = ")]
        return html, node(f"{src}\nconsole.log(JSON.stringify(STR));")

    def test_both_tables_have_the_same_keys_and_placeholders(self):
        _, tables = self.strings()
        self.assertEqual(set(tables["en"]), set(tables["ru"]))
        for k, v in tables["en"].items():
            self.assertEqual(set(re.findall(r"\{(\w+)\}", v)), set(re.findall(r"\{(\w+)\}", tables["ru"][k])), k)

    def test_every_label_the_page_uses_is_in_the_table(self):
        html, tables = self.strings()
        used = set(re.findall(r'data-t(?:-aria)?="(\w+)"', html)) | set(re.findall(r'\bt\("(\w+)"', html))
        self.assertTrue(used)
        self.assertEqual(used - set(tables["en"]), set())

    def test_no_russian_outside_the_ru_table(self):
        html = read("web", "report-template.html")
        outside = html[:html.index("    ru: {")] + html[html.index("const base = "):]
        self.assertNotRegex(outside, CYRILLIC)
        self.assertIn('<html lang="en">', html)

    def test_the_language_is_taken_from_the_data_at_render_time(self):
        html = read("web", "report-template.html")
        self.assertIn("d.lang", html)
        self.assertIn("document.documentElement.lang = lang", html)
        self.assertEqual(json.loads(re.search(r'<script id="recap-data" type="application/json">(.*?)</script>', html, re.S)[1])
                         ["tldr"][:8], "The main")

    def test_the_sample_is_the_embedded_sample(self):
        html = read("web", "report-template.html")
        blob = re.search(r'<script id="recap-data" type="application/json">(.*?)</script>', html, re.S)[1]
        self.assertEqual(json.loads(blob), json.loads(read("web", "sample.json")))
        self.assertNotRegex(read("web", "sample.json"), CYRILLIC)


if __name__ == "__main__":
    unittest.main()
