"""Where recap finds hub-sync/sync.py (its redact is the guard against secrets leaking into digests): the core's repo,
HUB_SYNC_DIR, the hub-sync folder of the hub-sync-chats entry in hub.json; not found - a clear error, nothing unredacted goes out."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["HUB_STATE_DIR"] = tempfile.mkdtemp(prefix="wr-state-")
os.environ["HUB_CONFIG"] = os.path.join(tempfile.mkdtemp(prefix="wr-config-"), "hub.json")

import recap  # noqa: E402

FAKE = "def redact(s):\n    return s.replace('SECRET', '***')\n\ndef open_snapshot(state, now, droot):\n    return {'ok': 1}\n"


def make_sync(root):
    d = os.path.join(root, "hub-sync")
    os.makedirs(d)
    path = os.path.join(d, "sync.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(FAKE)
    return path


def config(path):
    return {"extensions": {"hub-sync-chats": {"run": ["python", path, "live"], "path": "chats"}}}


class FindSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wr-sync-")
        self.core = os.path.join(self.tmp, "package-layer")   # the packaged core: no hub-sync next to it
        os.makedirs(self.core)
        self.clone = os.path.join(self.tmp, "clone")
        os.makedirs(self.clone)
        self.sync = make_sync(self.clone)

    def test_found_in_the_clone_from_hub_json(self):
        got = recap.find_sync(self.core, {}, config(self.sync))
        self.assertEqual(os.path.normcase(got), os.path.normcase(self.sync))

    def test_the_core_repo_goes_first(self):
        own = make_sync(self.core)
        got = recap.find_sync(self.core, {"HUB_SYNC_DIR": os.path.dirname(self.sync)}, config(self.sync))
        self.assertEqual(os.path.normcase(got), os.path.normcase(own))

    def test_env_goes_before_hub_json(self):
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        envsync = make_sync(other)
        got = recap.find_sync(self.core, {"HUB_SYNC_DIR": os.path.dirname(envsync)}, config(self.sync))
        self.assertEqual(os.path.normcase(got), os.path.normcase(envsync))

    def test_nowhere_gives_none(self):
        self.assertIsNone(recap.find_sync(self.core, {}, {}))

    def test_odd_hub_json_does_not_raise(self):
        for cfg in (None, [], {"extensions": None}, {"extensions": {"hub-sync-chats": "x"}},
                    {"extensions": {"hub-sync-chats": {"run": "sync.py"}}},
                    {"extensions": {"hub-sync-chats": {"run": [1, None, {}]}}},
                    {"extensions": {"hub-sync-chats": {"run": ["python", os.path.join(self.tmp, "nope", "sync.py")]}}}):
            self.assertIsNone(recap.find_sync(self.core, {"HUB_SYNC_DIR": os.path.join(self.tmp, "none")}, cfg), cfg)

    def test_a_folder_without_sync_py_is_skipped(self):
        os.makedirs(os.path.join(self.core, "hub-sync"))   # empty folder in the core
        got = recap.find_sync(self.core, {}, config(self.sync))
        self.assertEqual(os.path.normcase(got), os.path.normcase(self.sync))


class Redaction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wr-sync-")
        self.sync = make_sync(self.tmp)
        recap._SYNC = None

    def tearDown(self):
        recap._SYNC = None

    def test_import_does_not_need_sync(self):
        # the module is already imported above without hub-sync next to a core in this process: no exception at import
        self.assertTrue(hasattr(recap, "clean"))

    def test_clean_redacts_when_found(self):
        with mock.patch.object(recap, "find_sync", return_value=self.sync):
            self.assertEqual(recap.clean({"a": ["xSECRETx"]}), {"a": ["x***x"]})

    def test_not_found_is_a_clear_error_and_nothing_passes(self):
        with mock.patch.object(recap, "find_sync", return_value=None):
            with self.assertRaises(recap.SyncMissing) as cm:
                recap.clean("token SECRET")
        self.assertIn("HUB_SYNC_DIR", str(cm.exception))
        self.assertIn("sync.py", str(cm.exception))

    def test_open_now_without_sync_is_empty_and_logged(self):
        err = io.StringIO()
        with mock.patch.object(recap, "find_sync", return_value=None), contextlib.redirect_stderr(err):
            self.assertIsNone(recap.open_now({}, 0, "x"))
        self.assertIn("HUB_SYNC_DIR", err.getvalue())

    def test_open_now_with_sync(self):
        with mock.patch.object(recap, "find_sync", return_value=self.sync):
            self.assertEqual(recap.open_now({}, 0, "x"), {"ok": 1})


if __name__ == "__main__":
    unittest.main()
