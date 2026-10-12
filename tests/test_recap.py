"""Разбор недели на подставном журнале: границы недели, повторы из архивов, цифры, кому и когда уходит, что владельцу."""
import datetime
import json
import os
import sys
import tempfile
import unittest
import zoneinfo

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("HUB_STATE_DIR", tempfile.mkdtemp(prefix="wr-state-"))

import main   # noqa: E402
import recap  # noqa: E402

ZONE = zoneinfo.ZoneInfo("Europe/Berlin")


def at(y, mo, d, h=12, mi=0):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=ZONE).timestamp()


MON = at(2026, 10, 5, 0)
BERLIN_9 = {"id": "p1", "by": "owner", "text": "расписание: пн 09:00 Europe/Berlin", "ts": 0}     # неделя 2026-W41: 5.10 0:00 — 12.10 0:00


def ev(seq, ts, type_, entity=None, **data):
    return {"seq": seq, "ts": ts, "src": "x", "type": type_, "entity": entity, "data": data}


OWNER = {"channel": "web", "owner": True}
WEEK = [
    ev(1, MON - 60, "message.received", to="hub-core", text="прошлая неделя", **OWNER),
    ev(10, at(2026, 10, 6, 10), "message.received", to="hub-core", text="сделай отчёт", **OWNER),
    ev(11, at(2026, 10, 6, 10, 1), "delegation.created", session="analyst", **{"for": "m10"}, context="отчёт"),
    ev(12, at(2026, 10, 6, 10, 2), "turn.result", "hub-core", text="поручил analyst", duration_ms=60000, cost_usd=5),
    ev(13, at(2026, 10, 6, 10, 20), "permission.requested", "analyst", request_id="r1", tool="AskUserQuestion",
       input={"questions": [{"question": "За какой месяц?"}]}),
    ev(14, at(2026, 10, 6, 10, 30), "permission.answered", "analyst", request_id="r1", answers={"За какой месяц?": "сентябрь"},
       **OWNER),
    ev(15, at(2026, 10, 6, 10, 31), "message.received", to="analyst",
       text="Ответ владельца на вопрос q13: сентябрь", **OWNER),
    ev(16, at(2026, 10, 6, 11), "permission.requested", "analyst", request_id="r2", tool="Bash", input={}),
    ev(17, at(2026, 10, 6, 11, 30), "turn.result", "analyst", text="отчёт готов", duration_ms=1800000, cost_usd=9),
    ev(18, at(2026, 10, 6, 11, 40), "delegation.done", "d11"),
    ev(19, at(2026, 10, 6, 11, 41), "owner.file", path="/x/report.pdf"),
    ev(20, at(2026, 10, 7, 9), "message.received", to="designer", text="сессия написала владельцу", channel="session",
       owner=False, sender="designer"),
    ev(30, at(2026, 10, 12, 0, 1), "message.received", to="hub-core", text="уже следующая", **OWNER),
]


def journal_dir(parts):
    """Папка состояния: архивы и текущий журнал, части перекрываются, как после ротации."""
    d = tempfile.mkdtemp(prefix="wr-")
    os.makedirs(os.path.join(d, "journal-archive"))
    for i, part in enumerate(parts):
        name = "journal.jsonl" if i == len(parts) - 1 else os.path.join("journal-archive", f"journal-{i}.jsonl")
        with open(os.path.join(d, name), "w") as f:
            f.write("".join(json.dumps(e) + "\n" for e in part))
    return d


class Week(unittest.TestCase):
    def test_last_week_is_monday_to_monday_in_the_hub_zone(self):
        start, end, label = recap.last_week(at(2026, 10, 12, 9, 5), ZONE)
        self.assertEqual((start, end, label), (MON, at(2026, 10, 12, 0), "2026-W41"))
        self.assertEqual(recap.week_of("2026-W41", ZONE), (start, end, label))

    def test_due_on_monday_from_nine_once(self):
        self.assertIsNone(recap.due(at(2026, 10, 12, 8, 59), ZONE, {}))
        self.assertEqual(recap.due(at(2026, 10, 12, 9), ZONE, {}), "2026-W41")
        self.assertEqual(recap.due(at(2026, 10, 14, 15), ZONE, {}), "2026-W41")   # хаб лежал в понедельник
        self.assertIsNone(recap.due(at(2026, 10, 12, 9), ZONE, {"2026-W41": {}}))

    def test_due_on_the_chosen_day_and_time(self):
        when = {"day": 1, "hour": 10, "minute": 30, "zone": None}    # вторник 10:30
        self.assertIsNone(recap.due(at(2026, 10, 13, 10, 29), ZONE, {}, when))
        self.assertEqual(recap.due(at(2026, 10, 13, 10, 30), ZONE, {}, when), "2026-W41")
        self.assertIsNone(recap.due(at(2026, 10, 12, 23), ZONE, {}, when))


class Schedule(unittest.TestCase):
    def test_default_is_monday_nine_in_the_machine_zone(self):
        self.assertEqual(recap.schedule({}), recap.DEFAULT)
        self.assertEqual(recap.DEFAULT, {"day": 0, "hour": 9, "minute": 0, "zone": None})
        self.assertEqual(recap.tz(None).utcoffset(datetime.datetime.now()),
                         datetime.datetime.now().astimezone().utcoffset())   # местное время машины, не пояс хаба

    def test_the_owners_last_valid_post_sets_it(self):
        feed = [{"by": "owner", "text": "расписание: вт 10:30"},
                {"by": "owner", "text": "расписание: пт 25:00"},              # неверное — не берётся
                {"by": "designer", "text": "расписание: сб 07:00"},          # не владелец — не берётся
                {"by": "owner", "text": "просто заметка"}]
        self.assertEqual(recap.schedule({"feeds": {"week-recap": feed}}), {"day": 1, "hour": 10, "minute": 30, "zone": None})
        feed.append({"by": "owner", "text": "расписание: ср 08:05 Asia/Bangkok"})
        self.assertEqual(recap.schedule({"feeds": {"week-recap": feed}})["zone"], "Asia/Bangkok")
        feed.append({"by": "owner", "text": "расписание: ср 08:05 Nowhere/City"})   # неизвестный пояс — не берётся
        self.assertEqual(recap.schedule({"feeds": {"week-recap": feed}})["zone"], "Asia/Bangkok")

    def test_hub_session_may_set_it_for_the_owner(self):
        st = {"sessions": {"hub-core": {"kind": "hub"}}, "feeds": {"week-recap": [{"by": "hub-core", "text": "расписание: чт 18:00"}]}}
        self.assertEqual(recap.schedule(st)["day"], 3)

    def test_describe(self):
        self.assertEqual(recap.describe(recap.DEFAULT), "пн 09:00, местное время")
        self.assertEqual(recap.describe({"day": 2, "hour": 8, "minute": 5, "zone": "Asia/Bangkok"}), "ср 08:05, Asia/Bangkok")

    def test_events_of_the_week_once_from_overlapping_journals(self):
        d = journal_dir([WEEK[:8], WEEK[5:12], WEEK[10:]])
        got = recap.events(recap.journals(d), MON, at(2026, 10, 12, 0))
        self.assertEqual([e["seq"] for e in got], [e["seq"] for e in WEEK[1:12]])


class Numbers(unittest.TestCase):
    def setUp(self):
        self.m = recap.measure(WEEK[1:12])

    def test_owner_words_forms_tasks_and_waits(self):
        n = self.m["numbers"]
        self.assertEqual(n["owner_messages"], 1)              # ответ на форму и слова сессии — не просьба владельца
        self.assertEqual(len(self.m["answers"]), 1)
        self.assertEqual((n["forms"], n["forms_answered"], n["form_answer_median_min"]), (1, 1, 10.0))
        self.assertEqual(n["permission_requests"], 1)
        self.assertEqual((n["tasks"], n["tasks_done"]), (1, 1))
        self.assertEqual(n["wait_median_min"], 2.0)           # до первого ответа той сессии
        self.assertEqual(n["task_median_min"], 100.0)         # от просьбы до сдачи задачи по ней
        self.assertEqual(n["files_sent"], 1)
        self.assertEqual(n["busy_hours"], 0.5)
        self.assertNotIn("cost_usd", n)                       # cost_usd в итогах — нарастающий, сумма врёт

    def test_digest_keeps_the_owner_words_in_time_order(self):
        text = recap.digest("2026-W41", MON, at(2026, 10, 12, 0), self.m, ZONE)
        self.assertIn("владелец → hub-core (m10, ответ через 2.0 мин): сделай отчёт", text)
        self.assertIn("задача d11 → analyst (по m10): отчёт — сдана", text)
        self.assertIn("форма q13 от analyst: За какой месяц? → ответ через 10 мин: сентябрь", text)
        self.assertLess(text.index("сделай отчёт"), text.index("форма q13"))


class Flow(unittest.TestCase):
    def setUp(self):
        self.state = journal_dir([WEEK])
        self.home = os.path.join(self.state, "week-recap")
        self.sent, self.mine = [], {"weeks": {}}
        self.send = lambda *a: self.sent.append(a)

    def tick(self, now, sessions, feed=(BERLIN_9,)):
        return main.tick(now, self.mine, {"sessions": sessions, "feeds": {"week-recap": list(feed)}}, self.send,
                         self.state, self.home)

    def test_monday_goes_to_analyst_then_the_recap_to_the_owner_once(self):
        fleet = {"analyst": {"role": "agent", "state": "idle"}, "hub-core": {"kind": "hub", "state": "idle"}}
        self.tick(at(2026, 10, 12, 8), fleet)                            # расписание запомнено, до 9:00 — ничего
        self.assertEqual(self.sent, [])
        self.assertFalse(self.tick(at(2026, 10, 12, 8, 30), fleet))
        self.assertTrue(self.tick(at(2026, 10, 12, 9), fleet))
        (kind, _, data), = self.sent
        week = self.mine["weeks"]["2026-W41"]
        self.assertEqual((kind, data["to"], data["sender"]), ("message.received", "analyst", "week-recap"))
        self.assertIn(week["digest"], data["text"])
        self.assertIn(week["recap"], data["text"])
        self.assertIn("atlassian", data["text"])
        self.assertIn("вся работа владельца с хабом", data["text"])   # не одна область разбирающего
        self.assertTrue(os.path.getsize(week["digest"]))
        self.assertFalse(self.tick(at(2026, 10, 12, 9, 1), fleet))       # второй раз не шлёт
        with open(week["recap"], "w") as f:
            f.write("TL;DR: неделя ок\n\n## Рекомендации\n- завести агента")
        os.utime(week["recap"], (at(2026, 10, 12, 9, 30),) * 2)   # часы теста, не настоящие
        self.assertTrue(self.tick(at(2026, 10, 12, 10), fleet))
        kind, _, data = self.sent[-1]
        self.assertEqual((kind, data["title"], data["owner"]), ("owner.notify", "Неделя 2026-W41", True))
        self.assertIn("TL;DR: неделя ок", data["text"])
        self.assertFalse(self.tick(at(2026, 10, 12, 18), fleet))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(main.view(self.mine)["schedule"], "пн 09:00, Europe/Berlin")
        self.assertEqual(main.view(self.mine)["weeks"][0]["text"][:6], "TL;DR:")

    def test_no_analyst_goes_to_the_hub(self):
        self.tick(at(2026, 10, 12, 9), {"hub-core": {"kind": "hub", "state": "idle"}})
        self.assertEqual(self.sent[0][2]["to"], "hub-core")

    def test_no_recap_in_six_hours_sends_the_numbers(self):
        self.tick(at(2026, 10, 12, 9), {"analyst": {"role": "agent", "state": "idle"}})
        self.assertFalse(self.tick(at(2026, 10, 12, 14, 59), {}))
        self.assertTrue(self.tick(at(2026, 10, 12, 15), {}))
        data = self.sent[-1][2]
        self.assertEqual(data["title"], "Неделя 2026-W41: цифры")
        self.assertIn("сообщений владельца: 1", data["text"])

    def test_old_recap_file_is_not_this_weeks_answer(self):
        os.makedirs(os.path.join(self.home, "2026-W41"))
        path = os.path.join(self.home, "2026-W41", "recap.md")
        with open(path, "w") as f:
            f.write("старый разбор")
        os.utime(path, (at(2026, 10, 1), at(2026, 10, 1)))
        self.tick(at(2026, 10, 12, 9), {"analyst": {"role": "agent", "state": "idle"}})
        self.assertFalse(self.tick(at(2026, 10, 12, 9, 1), {}))


if __name__ == "__main__":
    unittest.main()
