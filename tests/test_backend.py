# -*- coding: utf-8 -*-
"""Offline smoke tests for the TG Reaction Ranker backend.

Run with:  python -m unittest discover -s tests -v
No Telegram credentials or network access required.
"""

import http.server
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT))

# Isolate all paths before importing the launcher (it resolves env at import).
_TMP = Path(tempfile.mkdtemp(prefix="telrank-test-"))
_DATA = _TMP / "data"
_IMPORTS = _TMP / "imports"
_TRASH = _TMP / "trash"
for _d in (_DATA, _IMPORTS, _TRASH):
    _d.mkdir(parents=True, exist_ok=True)
os.environ["TELERANK_DATA_DIR"] = str(_DATA)
os.environ["TELERANK_ARCHIVE_ROOT"] = str(_IMPORTS)
os.environ["TELERANK_TRASH_ROOT"] = str(_TRASH)
os.environ["TELERANK_OFFLINE"] = "1"

from telegram_bot import TelegramBotListener  # noqa: E402
from telegram_sync import TelegramSyncService  # noqa: E402
import tg_reaction_web_launcher as launcher  # noqa: E402


class BotLinkExtractionTests(unittest.TestCase):
    def test_plain_link(self):
        links = TelegramBotListener._extract_links("https://t.me/somechannel")
        self.assertIn("somechannel", links)

    def test_s_preview_link(self):
        # t.me/s/ prefix links were previously missed by the regex.
        links = TelegramBotListener._extract_links("https://t.me/s/somechannel hello")
        self.assertIn("somechannel", links)
        links = TelegramBotListener._extract_links("https://telegram.me/s/another_channel")
        self.assertIn("another_channel", links)

    def test_mention_and_dedup(self):
        text = "t.me/dup and @dup plus https://t.me/dup"
        links = TelegramBotListener._extract_links(text)
        self.assertEqual(links.count("dup"), 1)

    def test_bot_excluded(self):
        links = TelegramBotListener._extract_links("@somebot https://t.me/somebot")
        self.assertNotIn("somebot", links)


class BotConcurrencyTests(unittest.TestCase):
    def test_wrapper_releases_semaphore_and_clears_set(self):
        bot = TelegramBotListener("123456:" + "A" * 35, None)
        bot._importing.add("chan")

        def boom(_chat_id, _target):
            raise RuntimeError("boom")

        bot._import_and_track_inner = boom
        with self.assertRaises(RuntimeError):
            bot._import_and_track(1, "chan")
        self.assertNotIn("chan", bot._importing)
        self.assertEqual(bot._import_sem._value, bot._import_sem._initial_value)


class BotMessageTests(unittest.TestCase):
    class _FakeSync:
        def __init__(self, running=True):
            self.lock = threading.Lock()
            self.job = {
                "running": running, "stage": "syncing", "message": "Scanning",
                "progress": 0, "messages_scanned": 0, "media_downloaded": 0,
            }

        def quick_import(self, target):
            return {"action": "syncing"}

    class _FakeBot(TelegramBotListener):
        def __init__(self, sync):
            super().__init__("123456:" + "A" * 35, sync)
            self.messages = []
            self.edited = []

        def _api(self, method, data=None):
            return {"ok": True, "result": {"message_id": 1}}

        def _send_message(self, chat_id, text):
            self.messages.append(text)

        def _edit_message(self, chat_id, message_id, text):
            self.edited.append(text)

    def test_login_required_tells_user_to_resend(self):
        class LoginSync:
            lock = threading.Lock()
            job = {"running": False, "stage": "idle", "message": ""}

            def quick_import(self, target):
                return {"action": "login_required", "message": "需要登录"}

        bot = self._FakeBot(LoginSync())
        bot._import_and_track(1, "chan")
        self.assertTrue(any("重新发" in m for m in bot.messages), bot.messages)

    def test_long_import_sends_interim_and_completion(self):
        bot = self._FakeBot(self._FakeSync(running=True))
        original_sleep = time.sleep
        state = {"n": 0}

        def fake_sleep(_secs):
            state["n"] += 1
            if state["n"] >= 310:  # 300 fast polls + a few slow polls
                with bot._sync.lock:
                    bot._sync.job.update({
                        "running": False, "stage": "ready", "progress": 3,
                        "messages_scanned": 5, "media_downloaded": 2, "message": "",
                    })

        time.sleep = fake_sleep
        try:
            bot._import_and_track(1, "chan")
        finally:
            time.sleep = original_sleep
        combined = "\n".join(bot.messages + bot.edited)
        self.assertIn("仍在进行", combined)
        self.assertIn("下载完成", combined)


class SyncPayloadTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="telrank-sync-"))
        self.svc = TelegramSyncService(self.base, self.base / "imports", lambda v: v, lambda d: None)

    def test_payload_writer_sorts_by_id(self):
        data_file = self.base / "result.json"
        records = {5: {"id": 5}, 2: {"id": 2}, 9: {"id": 9}}
        count = self.svc._write_channel_payload(data_file, "Test", records)
        payload = json.loads(data_file.read_text(encoding="utf-8"))
        self.assertEqual(count, 3)
        self.assertEqual([m["id"] for m in payload["messages"]], [2, 5, 9])

    def test_scheduler_requires_authorized_session(self):
        svc = self.svc
        svc._schedule_tick()
        self.assertIsNone(svc._schedule_timer)  # no config yet -> not armed

        (self.base / "telegram-sync.json").write_text(
            json.dumps({"api_id": 123456, "api_hash": "a" * 32, "target": "t", "interval_minutes": 5}),
            encoding="utf-8",
        )
        svc._schedule_tick()
        self.assertIsNone(svc._schedule_timer)  # still no session file

        (self.base / "telegram-user.session").write_text("dummy", encoding="utf-8")
        svc._schedule_tick()
        self.assertIsNotNone(svc._schedule_timer)
        self.assertAlmostEqual(svc._schedule_timer.interval, 300.0, places=0)
        svc.stop_schedule()
        self.assertIsNone(svc._schedule_timer)


class ServerSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.channel = _IMPORTS / "TestChannel @handle"
        cls.channel.mkdir(exist_ok=True)
        (cls.channel / "messages.html").write_text("<html><body>test</body></html>", encoding="utf-8")
        http.server.ThreadingHTTPServer.allow_reuse_address = True
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), launcher.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as resp:
            return resp.status, resp.read()

    def _post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_index_served(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"TG Reaction Ranker", body)

    def test_imports_listed(self):
        status, body = self._get("/api/imports")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(str(_IMPORTS), data["root"])
        self.assertEqual(len(data["imports"]), 1)

    def test_delete_moves_to_trash(self):
        victim = _IMPORTS / "DeleteMe @deletehandle"
        victim.mkdir(exist_ok=True)
        (victim / "messages.html").write_text("<html></html>", encoding="utf-8")
        status, data = self._post("/api/delete-import", {"path": str(victim)})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertTrue(data["trash"].startswith(str(_TRASH)))
        self.assertTrue(Path(data["trash"]).exists())
        self.assertFalse(victim.exists())

    def test_scan_path_offline(self):
        # Re-create a channel to scan (deleted by the trash test above).
        channel = _IMPORTS / "ScanChannel @scanhandle"
        channel.mkdir(exist_ok=True)
        (channel / "messages.html").write_text("<html><body>scan</body></html>", encoding="utf-8")
        status, data = self._post("/api/scan-path", {"path": str(channel)})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["channel_handle"], "scanhandle")


class LocalGuardTests(unittest.TestCase):
    @staticmethod
    def _fake_handler(client_ip):
        h = launcher.Handler.__new__(launcher.Handler)
        h.client_address = (client_ip, 5555)
        h.sent = {}

        def send_json(payload, status=200):
            h.sent = {"payload": payload, "status": status}

        h.send_json = send_json
        h.read_json_body = lambda: {"last_import_path": "x"}
        return h

    def test_save_preferences_rejects_remote(self):
        h = self._fake_handler("192.168.1.99")
        h.handle_save_preferences()
        self.assertEqual(h.sent["status"], 403)

    def test_delete_import_rejects_remote(self):
        h = self._fake_handler("192.168.1.99")
        h.handle_delete_import()
        self.assertEqual(h.sent["status"], 403)

    def test_save_preferences_works_locally(self):
        h = self._fake_handler("127.0.0.1")
        h.handle_save_preferences()
        self.assertEqual(h.sent["status"], 200)
        self.assertTrue(h.sent["payload"]["ok"])
        prefs = json.loads((_DATA / "preferences.json").read_text(encoding="utf-8"))
        self.assertEqual(prefs["last_import_path"], "x")


if __name__ == "__main__":
    unittest.main()
