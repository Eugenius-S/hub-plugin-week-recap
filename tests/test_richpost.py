"""The weekly recap as a Telegram rich message: the markdown and the two pictures built from recap.json, the limits of the
core, and what the owner gets with `telegram.rich` on and off. Run: python3 -m unittest discover -s tests (with PYTHONPATH
pointing at the hub code)."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["HUB_STATE_DIR"] = tempfile.mkdtemp(prefix="wr-state-")
os.environ["HUB_CONFIG"] = os.path.join(tempfile.mkdtemp(prefix="wr-config-"), "hub.json")   # never the live hub.json

import main      # noqa: E402
import richpost  # noqa: E402

TITLE = "Week Oct 19–25"
DATA = {
    "week": "Oct 19–25",
    "tldr": "Most tokens went to the page (60%), the rest to the bot.",
    "numbers": [{"label": "Tasks done", "value": "11", "hint": "of 56 this week"},
                {"label": "Tokens", "value": "3 400M", "hint": "all machines"},
                {"label": "Sessions", "value": "7"}, {"label": "Commits", "value": "120"}],
    "themes": [
        {"title": "The page", "tokens": "2040M", "share": 0.6,
         "items": [{"title": "Fold long answers", "state": "done", "tokens": "300M", "story": "Long answers fold.",
                    "first_try": True},
                   {"title": "Voice retry", "state": "progress", "story": "Retries after a failure",
                    "next": "check on the phone"}]},
        {"title": "The bot", "tokens": "1360M", "share": 0.4,
         "items": [{"title": "Albums", "state": "dropped"}]},
    ],
    "insights": [{"kind": "broken", "title": "Logins drop", "what": "Twice this week.", "try": "One keeper."}],
    "together": {"good": ["Short briefs"], "slow": ["Late answers"]},
    "todo": {"filed": [{"title": "Flags", "source": "Jira", "link": "https://example.com/DSN-1", "due": "Fri"}],
             "not_filed": [{"title": "Move the archive", "source": "Granola", "why": "agreed at the meeting"}]},
}
MEDIA = ("![", "](")


def long_data(n):
    big = dict(DATA)
    big["themes"] = [{"title": f"Theme {k}", "tokens": "10M", "share": 0.01,
                      "items": [{"title": f"Item {k}-{i}", "state": "done", "story": "word " * 60}
                                for i in range(6)]} for k in range(n)]
    return big


class Build(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rp-")

    def build(self, data=DATA, lang="en"):
        path, pics = richpost.build(data, lang, self.dir, TITLE)
        with open(path, encoding="utf-8") as f:
            return f.read(), pics

    def test_sections_and_two_pictures(self):
        md, pics = self.build()
        self.assertEqual(sorted(os.path.basename(p) for p in pics), ["chart-tokens.png", "numbers.png"])
        for p in pics:
            self.assertTrue(os.path.getsize(p) > 1000)
        self.assertTrue(md.startswith(f"# {TITLE}\n"))
        for part in ("Most tokens went", "numbers.png", "chart-tokens.png", "<details>", "Where the tokens went",
                     "What hurt and what to try", "Try:", "What was done by theme", "How we worked together",
                     "For this week", "[Flags](https://example.com/DSN-1)", "Not filed anywhere"):
            self.assertIn(part, md)
        self.assertLess(md.index("2\u00a0040M"), md.index("1\u00a0360M"))   # by share, the biggest first

    def test_russian(self):
        md, _ = self.build(lang="ru")
        self.assertIn("Что было больно", md)
        self.assertNotIn("What hurt", md)

    def test_unknown_language_is_english(self):
        md, _ = self.build(lang="xx")
        self.assertIn("What hurt and what to try", md)

    def test_no_font_no_pictures_the_text_stays_whole(self):
        with mock.patch.object(richpost, "fonts", lambda: None):
            md, pics = self.build()
        self.assertEqual(pics, [])
        self.assertNotIn("![", md)
        self.assertIn("**11** Tasks done: of 56 this week", md)     # the numbers as a list
        self.assertIn("| Theme | Tokens | Share | Done |", md)      # the shares as a table
        self.assertIn("The page", md)

    def test_empty_sections_are_not_shown(self):
        md, _ = self.build({"tldr": "A quiet week."})
        self.assertEqual(md.strip(), f"# {TITLE}\n\nA quiet week.")

    def test_a_broken_item_does_not_break_the_recap(self):
        data = dict(DATA, themes=[None, {"title": "T", "items": [None, {"title": ""}, "x"]}], insights=[3],
                    numbers=["x"], together=[], todo={"filed": [{"title": "A", "link": "javascript:x"}]})
        md, _ = self.build(data)
        self.assertIn("- A", md)
        self.assertNotIn("javascript", md)

    def test_long_recap_fits_the_core_limits(self):
        md, pics = self.build(long_data(30))
        self.assertLessEqual(len(md), 32768 - 500)
        self.assertLessEqual(md.count(MEDIA[0]) + len(pics), 50)
        self.assertLessEqual(max(os.path.getsize(p) for p in pics), 10 * 1024 * 1024)


class Send(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="rp-home-")
        self.sent = []
        patch = mock.patch.object(main, "HOME", self.home)
        patch.start()
        self.addCleanup(patch.stop)
        pin = mock.patch.object(main, "_lang", lambda: "en")
        pin.start()
        self.addCleanup(pin.stop)
        self.addCleanup(lambda: os.path.exists(os.environ["HUB_CONFIG"]) and os.remove(os.environ["HUB_CONFIG"]))
        reports = tempfile.mkdtemp(prefix="rp-reports-")
        patch = mock.patch.object(main, "REPORTS", reports)
        patch.start()
        self.addCleanup(patch.stop)
        self.w = {"report": os.path.join(self.home, "report.html"), "period": "Oct 19–25", "lang": "en"}

    def settings(self, telegram):
        with open(os.environ["HUB_CONFIG"], "w", encoding="utf-8") as f:
            json.dump({"telegram": telegram}, f)

    def publish(self):
        main.publish("2026-W43", self.w, dict(DATA), 1000.0, lambda *a: self.sent.append(a))

    def test_rich_off_the_short_text_as_before(self):
        self.settings({})
        self.publish()
        (kind, _, data), = self.sent
        self.assertEqual(kind, "owner.notify")
        self.assertEqual(data["title"], TITLE)
        self.assertIn(DATA["tldr"], data["text"])
        self.assertFalse(os.path.exists(os.path.join(self.home, "2026-W43", "recap.md")))
        self.assertEqual(self.w["notified"], 1000.0)

    def test_no_hub_json_the_short_text(self):
        self.publish()
        self.assertEqual([a[0] for a in self.sent], ["owner.notify"])

    def test_rich_on_one_rich_file_and_no_text(self):
        self.settings({"rich": True})
        self.publish()
        (kind, _, data), = self.sent
        self.assertEqual(kind, "owner.file")
        self.assertEqual((data["rich"], data["title"], data["caption"]), (True, TITLE, TITLE))
        folder = os.path.join(self.home, "2026-W43")
        for name in ("recap.md", "numbers.png", "chart-tokens.png"):
            self.assertTrue(os.path.exists(os.path.join(folder, name)), name)
        self.assertTrue(data["path"].endswith("recap.md"))
        self.assertEqual(self.w["notified"], 1000.0)
        self.assertEqual(self.w["text"], DATA["tldr"])
        self.assertTrue(os.path.exists(os.path.join(main.REPORTS, "2026-W43.html")))   # the page's tab keeps the report

    def test_rich_must_be_exactly_true(self):
        self.settings({"rich": "yes"})
        self.publish()
        self.assertEqual([a[0] for a in self.sent], ["owner.notify"])

    def test_not_built_the_short_text(self):
        self.settings({"rich": True})
        with mock.patch.object(richpost, "build", side_effect=OSError("disk")):
            self.publish()
        (kind, _, data), = self.sent
        self.assertEqual(kind, "owner.notify")
        self.assertIn(DATA["tldr"], data["text"])
        self.assertEqual(self.w["notified"], 1000.0)


if __name__ == "__main__":
    unittest.main()
