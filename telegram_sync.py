"""Local Telegram user-account login and incremental channel synchronisation."""

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path


def _system_http_proxy():
    """Return an HTTP proxy URL from TELERANK_MT_PROXY or the Windows system proxy."""
    raw = os.environ.get("TELERANK_MT_PROXY", "").strip()
    if raw:
        return raw
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            ) as key:
                enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0] or 0)
                server = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "")
            if enabled and server and "=" not in server:
                return server
        except Exception:
            pass
    return ""


def _mt_proxy_tuple():
    """Build a Telethon-compatible proxy tuple for the MTProto connection."""
    raw = _system_http_proxy()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    scheme, _, rest = raw.partition("://")
    hostport = rest.split("/", 1)[0]
    host, _, port = hostport.rpartition(":")
    if not host or not port.isdigit():
        return None
    scheme = scheme.lower()
    if scheme in ("http", "https"):
        return ("http", host, int(port), True)
    if scheme in ("socks5", "socks"):
        return ("socks5", host, int(port), True)
    if scheme == "socks4":
        return ("socks4", host, int(port), True)
    return None


class TelegramSyncService:
    def __init__(self, base_dir, archive_root, safe_folder_name, save_preferences):
        self.base_dir = Path(base_dir)
        self.archive_root = Path(archive_root)
        self.safe_folder_name = safe_folder_name
        self.save_preferences = save_preferences
        self.config_file = self.base_dir / "telegram-sync.json"
        self.state_file = self.base_dir / "telegram-sync-state.json"
        self.session_file = self.base_dir / "telegram-user.session"
        self.lock = threading.Lock()
        self.pending = {}
        self.job = {"running": False, "stage": "idle", "message": "Not connected", "progress": 0, "total": 0, "messages_scanned": 0, "media_downloaded": 0, "media_current": "", "media_bytes": 0, "media_total_bytes": 0, "updated": 0}
        self.timer = None
        self._schedule_timer = None
        self._next_sync_at = None
        self._cancel_event = threading.Event()
        self._owner_id_cache = None
        self._last_progress_update = 0.0

    def _read_json(self, path, fallback):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return fallback

    def _write_json(self, path, data):
        self.base_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def config(self):
        return self._read_json(self.config_file, {})

    def public_status(self):
        with self.lock:
            job = dict(self.job)
        config = self.config()
        bot_info = self.bot_status()
        return {
            "ok": True,
            "configured": bool(config.get("api_id") and config.get("api_hash")),
            "authorized": self.session_file.exists(),
            "target": config.get("target", ""),
            "phone": config.get("phone", ""),
            "interval_minutes": config.get("interval_minutes", 15),
            "download_media": bool(config.get("download_media", True)),
            "download_parallel": config.get("download_parallel", 3),
            "schedule_active": self._schedule_timer is not None,
            "next_sync_at": self._next_sync_at,
            "job": job,
            "bot": bot_info,
        }

    # ── Bot listener ────────────────────────────────────────────

    def bot_status(self):
        config = self.config()
        bot_token = config.get("bot_token", "")
        active = getattr(self, "_bot_listener", None)
        return {
            "configured": bool(bot_token),
            "running": active is not None and active.status["running"],
            "error": active.status["error"] if active else "",
            "offset": active.status["offset"] if active else 0,
        }

    def configure_bot(self, token):
        token = (token or "").strip()
        config = self.config()
        config["bot_token"] = token
        self._write_json(self.config_file, config)
        self.stop_bot()
        if token:
            self.start_bot()
        return self.public_status()

    def start_bot(self):
        config = self.config()
        token = config.get("bot_token", "")
        if not token:
            return
        self.stop_bot()
        from telegram_bot import TelegramBotListener
        self._bot_listener = TelegramBotListener(token, self)
        self._bot_listener.start()

    def stop_bot(self):
        active = getattr(self, "_bot_listener", None)
        if active:
            active.stop()
        self._bot_listener = None

    def resume_schedule(self):
        """Resume background services after restart."""
        config = self.config()
        # auto-start bot if configured
        if config.get("bot_token"):
            threading.Timer(3, self.start_bot).start()
        # auto-start the periodic sync scheduler (guarded internally)
        threading.Timer(3, self._schedule_tick).start()

    def stop_schedule(self):
        timer = self._schedule_timer
        self._schedule_timer = None
        self._next_sync_at = None
        if timer:
            timer.cancel()

    def _schedule_tick(self):
        """(Re)arm the periodic sync timer while the account is configured/authorized."""
        config = self.config()
        if not (config.get("api_id") and config.get("api_hash") and config.get("target")):
            self.stop_schedule()
            return
        if not self.session_file.exists():
            self.stop_schedule()
            return
        try:
            interval = max(5, min(1440, int(config.get("interval_minutes", 15) or 15)))
        except (TypeError, ValueError):
            interval = 15
        self.stop_schedule()
        timer = threading.Timer(interval * 60.0, self._scheduled_sync)
        timer.daemon = True
        self._schedule_timer = timer
        self._next_sync_at = time.time() + interval * 60.0
        timer.start()

    def _scheduled_sync(self):
        try:
            with self.lock:
                if self.job["running"]:
                    return
            self.start_sync()
        finally:
            self._schedule_tick()

    def configure(self, body):
        existing = self.config()
        api_id = str(body.get("api_id", "")).strip() or str(existing.get("api_id", "")).strip()
        api_hash = str(body.get("api_hash", "")).strip() or str(existing.get("api_hash", "")).strip()
        target = self.normalize_target(body.get("target", "")) or self.normalize_target(existing.get("target", ""))
        if not api_id.isdigit() or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
            raise ValueError("A valid Telegram API ID and API hash are required")
        if not re.fullmatch(r"[A-Za-z0-9_]{3,}|-?\d+", target):
            raise ValueError("Enter a public channel username, for example @telegram")
        try:
            interval = max(5, min(1440, int(body.get("interval_minutes", existing.get("interval_minutes", 15)))))
        except (TypeError, ValueError):
            interval = 15
        try:
            parallel = max(1, min(10, int(body.get("download_parallel", existing.get("download_parallel", 3)))))
        except (TypeError, ValueError):
            parallel = 3
        config = {
            "api_id": int(api_id), "api_hash": api_hash, "target": target,
            "interval_minutes": interval, "download_media": bool(body.get("download_media", existing.get("download_media", True))),
            "download_parallel": parallel,
            "phone": str(body.get("phone", "")).strip() or str(existing.get("phone", "")).strip(),
            "bot_token": existing.get("bot_token", ""),
        }
        self._write_json(self.config_file, config)
        # Re-arm the scheduler with the new interval/target right away.
        self._schedule_tick()
        return self.public_status()

    @staticmethod
    def normalize_target(value):
        raw = str(value or "").strip()
        if not raw:
            return ""
        raw = re.sub(r"^https?://", "", raw, flags=re.IGNORECASE).strip("/")
        raw = re.sub(r"^(?:www\.)?(?:t\.me|telegram\.me)/", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"^s/", "", raw, flags=re.IGNORECASE)
        raw = raw.split("/", 1)[0].lstrip("@").strip()
        return raw

    def _client(self):
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            raise RuntimeError("Telegram support is unavailable. Reinstall the application.") from exc
        config = self.config()
        if not config.get("api_id") or not config.get("api_hash"):
            raise ValueError("Save the Telegram API configuration first")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        proxy = _mt_proxy_tuple()
        return TelegramClient(
            str(self.session_file),
            int(config["api_id"]),
            config["api_hash"],
            proxy=proxy,
        )

    def send_code(self, phone):
        phone = re.sub(r"[\s\-()]+", "", str(phone or "").strip())
        if not re.fullmatch(r"\+\d{6,20}", phone):
            raise ValueError("Use international format, for example +8613800000000")
        config = self.config()
        if config.get("phone") != phone:
            config["phone"] = phone
            self._write_json(self.config_file, config)
        return asyncio.run(self._send_code(phone))

    async def _send_code(self, phone):
        client = self._client()
        try:
            await client.connect()
            sent = await client.send_code_request(phone)
            self.pending = {"phone": phone, "hash": sent.phone_code_hash}
            return {"ok": True, "stage": "code", "message": "Verification code sent"}
        finally:
            await client.disconnect()

    def verify_code(self, code):
        if not self.pending.get("phone") or not self.pending.get("hash"):
            raise ValueError("Request a verification code first")
        return asyncio.run(self._verify_code(str(code or "").strip()))

    async def _verify_code(self, code):
        from telethon.errors import SessionPasswordNeededError
        client = self._client()
        try:
            await client.connect()
            try:
                await client.sign_in(self.pending["phone"], code, phone_code_hash=self.pending["hash"])
            except SessionPasswordNeededError:
                return {"ok": True, "stage": "password", "message": "Two-step verification password required"}
            self.pending = {}
            threading.Timer(0.2, self.start_sync).start()
            threading.Timer(0.2, self._schedule_tick).start()
            return {"ok": True, "stage": "ready", "message": "Telegram account connected"}
        finally:
            await client.disconnect()

    def verify_password(self, password):
        if not self.pending.get("phone"):
            raise ValueError("Request a verification code first")
        return asyncio.run(self._verify_password(str(password or "")))

    async def _verify_password(self, password):
        client = self._client()
        try:
            await client.connect()
            await client.sign_in(password=password)
            self.pending = {}
            threading.Timer(0.2, self.start_sync).start()
            threading.Timer(0.2, self._schedule_tick).start()
            return {"ok": True, "stage": "ready", "message": "Telegram account connected"}
        finally:
            await client.disconnect()

    def start_sync(self):
        with self.lock:
            if self.job["running"]:
                return self.public_status()
            self.job.update({"running": True, "stage": "starting", "message": "Preparing Telegram sync", "progress": 0, "total": 0, "messages_scanned": 0, "media_downloaded": 0, "media_current": "", "media_bytes": 0, "media_total_bytes": 0, "updated": int(time.time())})
        threading.Thread(target=self._run_sync, daemon=True).start()
        return self.public_status()

    def quick_import(self, target):
        """Accept one pasted Telegram link and either start import or request one-time login."""
        self.configure({"target": target})
        if self.session_file.exists():
            result = self.start_sync()
            result["action"] = "syncing"
            return result
        result = self.public_status()
        result["action"] = "login_required"
        result["message"] = "首次使用需要登录 Telegram；验证后会自动继续导入这个频道。"
        return result

    def _set_job(self, **values):
        with self.lock:
            self.job.update(values)
            self.job["updated"] = int(time.time())

    def _run_sync(self):
        try:
            asyncio.run(self._sync())
        except Exception as exc:
            self._set_job(running=False, stage="error", message=str(exc))
        else:
            if self._cancel_event.is_set():
                self._set_job(running=False, stage="stopped", message="Sync stopped by user")
            else:
                self._set_job(running=False, stage="ready", message="Sync complete")

    def stop_sync(self):
        """Cancel the currently running sync (checked between batches)."""
        with self.lock:
            running = self.job["running"]
        if not running:
            return {"ok": False, "message": "当前没有正在运行的任务"}
        self._cancel_event.set()
        self._set_job(stage="stopping", message="正在停止…")
        return {"ok": True, "message": "正在停止"}

    def owner_id(self):
        """Telegram user id of the logged-in account (used to lock the bot)."""
        if self._owner_id_cache:
            return self._owner_id_cache
        try:
            client = self._client()

            async def _fetch():
                await client.connect()
                me = await client.get_me()
                await client.disconnect()
                return me.id

            self._owner_id_cache = asyncio.run(_fetch())
        except Exception:
            self._owner_id_cache = None
        return self._owner_id_cache

    def download_message_media(self, link, dest_dir):
        """Download the media (video/photo) of a specific t.me message link.

        Accepts links like https://t.me/channel/123 or https://t.me/c/123456/123.
        Returns the saved file path.
        """
        match = re.search(r"(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]+)/(\d+)(?:[/?#]|$)", str(link or ""))
        if not match:
            raise ValueError("需要带消息编号的链接，例如 https://t.me/channel/123")
        target, msg_id = match.group(1), int(match.group(2))
        client = self._client()

        async def _run():
            try:
                await client.connect()
                if target.isdigit():
                    entity = await client.get_entity(int("-100" + target))
                else:
                    entity = await client.get_entity(target)
                message = await client.get_messages(entity, ids=msg_id)
                if not message or not getattr(message, "media", None):
                    raise ValueError("该消息没有可下载的媒体")
                Path(dest_dir).mkdir(parents=True, exist_ok=True)
                path = await client.download_media(message, file=str(dest_dir))
                if not path:
                    raise ValueError("媒体下载失败")
                return str(path)
            finally:
                await client.disconnect()

        return asyncio.run(_run())

    def _write_channel_payload(self, data_file, title, records):
        """Write the full channel result.json (sorted by message id)."""
        payload = {"name": title, "type": "channel", "messages": [records[k] for k in sorted(records)]}
        self._write_json(data_file, payload)
        return len(payload["messages"])

    @staticmethod
    def _reaction_counts(message):
        results = getattr(getattr(message, "reactions", None), "results", None) or []
        counts = []
        for item in results:
            emoji = getattr(getattr(item, "reaction", None), "emoticon", "")
            if emoji:
                counts.append({"emoji": emoji, "count": int(getattr(item, "count", 0) or 0)})
        return counts

    async def _sync(self):
        config = self.config()
        client = self._client()
        self._cancel_event.clear()
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise RuntimeError("Telegram login has expired. Connect the account again.")
            self._set_job(stage="resolving", message="Resolving channel")
            entity = await client.get_entity(config["target"])
            title = getattr(entity, "title", "") or config["target"]
            username = getattr(entity, "username", "") or config["target"]
            folder = self.archive_root / self.safe_folder_name(f"{title} @{username}")
            media_dir = folder / "media"
            folder.mkdir(parents=True, exist_ok=True)
            state = self._read_json(self.state_file, {"channels": {}})
            channels = state.setdefault("channels", {})
            key = str(getattr(entity, "id", username))
            checkpoint = int(channels.get(key, {}).get("last_id", 0) or 0)
            data_file = folder / "result.json"
            existing = self._read_json(data_file, {"name": title, "messages": []})
            records = {int(item.get("id", 0) or 0): item for item in existing.get("messages", []) if str(item.get("id", "")).isdigit()}
            records_before = len(records)
            self._set_job(stage="syncing", message="Scanning channel messages", progress=0, total=0, messages_scanned=0, media_downloaded=0)
            downloaded = 0
            scanned = 0
            media_downloaded = 0
            media_dir = folder / "media"
            if config.get("download_media"):
                media_dir.mkdir(parents=True, exist_ok=True)

            try:
                parallel = int(config.get("download_parallel", 3) or 3)
                parallel = int(os.environ.get("TELERANK_DOWNLOAD_PARALLEL", parallel))
            except (TypeError, ValueError):
                parallel = 3
            parallel = max(1, min(10, parallel))
            sem = asyncio.Semaphore(parallel)

            async def _process_message(message, has_media):
                nonlocal media_downloaded
                media = []
                if has_media and config.get("download_media"):
                    media_name = str(getattr(getattr(message, "file", None), "name", "") or f"message-{message.id}")

                    def _progress(current, total):
                        now = time.monotonic()
                        if current < total and now - self._last_progress_update < 0.15:
                            return
                        self._last_progress_update = now
                        self._set_job(stage="downloading_media", message=f"Downloading {media_downloaded+1}", messages_scanned=scanned, media_downloaded=media_downloaded, media_current=media_name, media_bytes=int(current or 0), media_total_bytes=int(total or 0))

                    async with sem:
                        try:
                            path = await client.download_media(message, file=str(media_dir), progress_callback=_progress)
                            if path:
                                media.append({"href": str(Path(path).relative_to(folder))})
                                media_downloaded += 1
                        except Exception:
                            pass
                link = f"https://t.me/{username}/{message.id}" if username else ""
                records[int(message.id)] = {
                    "id": message.id, "date": message.date.isoformat() if message.date else "",
                    "from": getattr(getattr(message, "sender", None), "first_name", "") or title,
                    "text": message.message or "", "reactions": self._reaction_counts(message),
                    "media": media, "source_link": link,
                }
                return int(message.id)

            batch = []
            batch_size = max(3, parallel * 3)
            last_checkpoint_write = 0
            async for message in client.iter_messages(entity, min_id=checkpoint, reverse=True):
                if self._cancel_event.is_set():
                    break
                if not message or not message.id:
                    continue
                scanned += 1
                msg_media = getattr(message, "media", None)
                has_media = False
                if msg_media is not None:
                    mt = type(msg_media).__name__
                    if mt == "MessageMediaPhoto":
                        has_media = True
                    elif mt == "MessageMediaDocument":
                        doc = getattr(msg_media, "document", None)
                        attrs = getattr(doc, "attributes", []) if doc else []
                        has_media = any(type(a).__name__ == "DocumentAttributeSticker" for a in attrs)
                if msg_media is not None and not has_media:
                    checkpoint = max(checkpoint, int(message.id))
                    continue
                batch.append((message, has_media))
                if len(batch) >= batch_size:
                    ids = await asyncio.gather(*(_process_message(m, hm) for m, hm in batch))
                    for mid in ids:
                        downloaded += 1
                        checkpoint = max(checkpoint, mid)
                    batch.clear()
                    self._set_job(stage="syncing", message="Scanning channel messages", progress=downloaded, messages_scanned=scanned, media_downloaded=media_downloaded, media_current="", media_bytes=0, media_total_bytes=0)
                    if downloaded - last_checkpoint_write >= 500:
                        # Crash-safe checkpoint: persist progress during long first syncs.
                        self._write_channel_payload(data_file, title, records)
                        last_checkpoint_write = downloaded
            if batch:
                ids = await asyncio.gather(*(_process_message(m, hm) for m, hm in batch))
                for mid in ids:
                    downloaded += 1
                    checkpoint = max(checkpoint, mid)
                self._set_job(stage="syncing", message="Scanning channel messages", progress=downloaded, messages_scanned=scanned, media_downloaded=media_downloaded, media_current="", media_bytes=0, media_total_bytes=0)

            # Only rewrite result.json when new content actually arrived.
            if len(records) != records_before:
                self._write_channel_payload(data_file, title, records)
            channels[key] = {"last_id": checkpoint, "path": str(folder), "target": username, "updated": int(time.time())}
            self._write_json(self.state_file, state)
            prefs = self._read_json(self.base_dir / "preferences.json", {})
            prefs["last_import_path"] = str(folder)
            self.save_preferences(prefs)
            if self._cancel_event.is_set():
                self._set_job(stage="stopped", message=f"Stopped by user after {downloaded} messages and {media_downloaded} media files", progress=downloaded, total=downloaded, messages_scanned=scanned, media_downloaded=media_downloaded, media_current="", media_bytes=0, media_total_bytes=0)
            else:
                self._set_job(stage="ready", message=f"Imported {downloaded} messages and {media_downloaded} media files", progress=downloaded, total=downloaded, messages_scanned=scanned, media_downloaded=media_downloaded, media_current="", media_bytes=0, media_total_bytes=0)
        finally:
            await client.disconnect()
