"""Telegram Bot listener — receives forwarded channel links and auto-imports."""

import json
import os
import re
import shutil
import threading
import time
import urllib.request
from pathlib import Path

# macOS system proxy detection — PyInstaller apps may not auto-detect
def _build_opener():
    """Return an opener that uses the system proxy (macOS framework / env / Windows registry)."""
    try:
        # macOS System Configuration framework
        from SystemConfiguration import SCDynamicStoreCopyProxies
        proxies = SCDynamicStoreCopyProxies(None)
        http_proxy = proxies.get("HTTPProxy")
        http_port = proxies.get("HTTPPort")
        if http_proxy and http_port:
            proxy_url = f"http://{http_proxy}:{http_port}"
            handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            return urllib.request.build_opener(handler)
    except Exception:
        pass
    # fallback: check environment variables
    proxies = {}
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = os.environ.get(var, "")
        if val:
            proxies.setdefault("http", val)
            proxies.setdefault("https", val)
    # Windows: fall back to the system (WinINET) proxy, e.g. Clash/V2Ray
    if not proxies and os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            ) as key:
                enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0] or 0)
                server = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "")
            if enabled and server:
                if "=" in server:
                    for part in server.split(";"):
                        if "=" in part:
                            proto, url = part.split("=", 1)
                            if url and not url.startswith("http"):
                                url = "http://" + url
                            proxies[proto.strip()] = url
                else:
                    if not server.startswith("http"):
                        server = "http://" + server
                    proxies["http"] = server
                    proxies["https"] = server
        except Exception:
            pass
    if proxies:
        handler = urllib.request.ProxyHandler(proxies)
        return urllib.request.build_opener(handler)
    return urllib.request.build_opener()

_API_OPENER = _build_opener()


class TelegramBotListener:
    """Long-polls a Telegram Bot for channel links and triggers import."""

    def __init__(self, token, sync_service):
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{35,}", token or ""):
            raise ValueError("Invalid bot token format. Get one from @BotFather.")
        self._token = token
        self._sync = sync_service
        self._running = False
        self._thread = None
        self._offset = 0
        self._last_error = ""
        self._import_sem = threading.BoundedSemaphore(2)
        self._importing = set()
        self._importing_lock = threading.Lock()
        self._owner_id = None
        self._pending_save = {}
        self._pending_lock = threading.Lock()
        self._batch = {}

    # ── Bot API helpers ──────────────────────────────────────────

    def _api(self, method, data=None):
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        req = urllib.request.Request(
            url,
            data=json.dumps(data or {}).encode("utf-8") if data else None,
            headers={"Content-Type": "application/json"},
        )
        with _API_OPENER.open(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _send_message(self, chat_id, text, reply_markup=None):
        payload = {"chat_id": chat_id, "text": text[:4000]}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            sent = self._api("sendMessage", payload)
            return (sent.get("result") or {}).get("message_id")
        except Exception:
            return None

    def _edit_message(self, chat_id, message_id, text):
        try:
            self._api("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text[:4000],
            })
        except Exception:
            pass  # editing may fail if message unchanged

    # ── Link extraction ──────────────────────────────────────────

    @staticmethod
    def _extract_links(text):
        found = []
        for match in re.finditer(
            r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]{3,})",
            text or "",
            re.IGNORECASE,
        ):
            raw = match.group(1)
            if raw.lower().endswith("bot"):
                continue
            found.append(raw)
        for match in re.finditer(r"@([A-Za-z0-9_]{3,})", text or ""):
            handle = match.group(1)
            if handle.lower().endswith("bot"):
                continue
            found.append(handle)
        return list(dict.fromkeys(found))

    # ── Import with progress tracking ────────────────────────────

    def _import_and_track(self, chat_id, target):
        """Concurrency-limited wrapper with in-flight deduplication."""
        self._import_sem.acquire()
        try:
            self._import_and_track_inner(chat_id, target)
        finally:
            self._import_sem.release()
            with self._importing_lock:
                self._importing.discard(target)

    def _import_and_track_inner(self, chat_id, target):
        """Start import, poll progress, report back via messages."""
        try:
            result = self._sync.quick_import(target)
        except Exception as exc:
            self._send_message(chat_id, f"❌ @{target} 启动失败：{exc}")
            return

        if result.get("action") == "login_required":
            self._send_message(
                chat_id,
                f"⚠️ @{target}：需要先在 TG Reaction Ranker 中登录 Telegram 账号。登录完成后，请把这条频道链接重新发给本机器人。",
            )
            return

        # Send initial status
        sent = self._api("sendMessage", {
            "chat_id": chat_id,
            "text": f"⏳ 正在下载 @{target} 的图片和表情包…",
        })
        msg_id = sent.get("result", {}).get("message_id") if sent.get("ok") else 0

        # Poll job until complete
        last_text = ""
        for _ in range(300):  # max 10 minutes
            time.sleep(2)
            with self._sync.lock:
                job = dict(self._sync.job)
            if not job.get("running"):
                break
            stage = job.get("stage", "")
            if stage == "downloading_media":
                text = f"📥 正在下载 @{target}\n{job.get('media_current', '')}"
            elif stage == "syncing":
                progress = job.get("progress", 0)
                scanned = job.get("messages_scanned", 0)
                text = f"🔍 正在扫描 @{target}\n已扫描 {scanned} 条消息，找到 {progress} 条新内容"
            else:
                text = f"⏳ {job.get('message', '处理中…')}"
            if text != last_text and msg_id:
                self._edit_message(chat_id, msg_id, text)
                last_text = text

        # Long imports: keep watching at a slower pace instead of giving up.
        if job.get("running"):
            self._send_message(chat_id, "⏳ 导入仍在进行中（已超过 10 分钟），完成后我会再通知你。")
            while True:
                time.sleep(30)
                with self._sync.lock:
                    job = dict(self._sync.job)
                if not job.get("running"):
                    break

        # Final status
        with self._sync.lock:
            job = dict(self._sync.job)
        stage = job.get("stage", "")
        if stage == "ready":
            progress = job.get("progress", 0)
            media = job.get("media_downloaded", 0)
            text = f"✅ @{target} 下载完成！\n{progress} 条消息，{media} 个媒体文件\n可在 TG Reaction Ranker 中查看排名。"
        elif stage == "error":
            text = f"❌ @{target} 下载失败：{job.get('message', '未知错误')}"
        else:
            text = f"⚠️ @{target} 状态：{stage}"

        if msg_id:
            self._edit_message(chat_id, msg_id, text)
        else:
            self._send_message(chat_id, text)

    # ── Media save flow (video share -> choose folder -> download) ──

    @staticmethod
    def _extract_message_links(text):
        """Extract (target, message_id) pairs from t.me/xxx/123 links."""
        found = []
        for match in re.finditer(
            r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?(?:c/(\d+)/(\d+)|([A-Za-z0-9_]+)/(\d+))(?![0-9])",
            text or "",
            re.IGNORECASE,
        ):
            if match.group(1):
                found.append((match.group(1), int(match.group(2))))
            else:
                found.append((match.group(3), int(match.group(4))))
        return list(dict.fromkeys(found))

    def _download_root(self):
        env = os.environ.get("TELERANK_DOWNLOAD_ROOT", "").strip()
        if env:
            return Path(env)
        try:
            prefs = self._sync._read_json(self._sync.base_dir / "preferences.json", {})
            if prefs.get("download_root"):
                return Path(prefs["download_root"])
        except Exception:  # noqa: BLE001
            pass
        return Path(r"D:\TelegramReactionRanker\Downloads")

    def _folder_keyboard(self):
        root = self._download_root()
        folders = sorted([p for p in root.iterdir() if p.is_dir()]) if root.exists() else []
        rows = []
        for i, folder in enumerate(folders[:20]):
            rows.append([{"text": "📂 " + folder.name[:38], "callback_data": f"save:existing:{i}"}])
        rows.append([
            {"text": "📁 新建文件夹", "callback_data": "save:new"},
            {"text": "❌ 取消", "callback_data": "save:cancel"},
        ])
        return {"inline_keyboard": rows}

    BATCH_WINDOW_SECONDS = 6

    def _begin_media_save(self, chat_id, **media):
        """Collect videos forwarded within a short window into one batch."""
        with self._pending_lock:
            batch = self._batch.get(chat_id)
            if batch is None:
                batch = {"items": [], "timer": None}
                self._batch[chat_id] = batch
            batch["items"].append(media)
            if batch["timer"]:
                batch["timer"].cancel()
            batch["timer"] = threading.Timer(self.BATCH_WINDOW_SECONDS, self._flush_batch, args=(chat_id,))
            batch["timer"].daemon = True
            batch["timer"].start()

    def _flush_batch(self, chat_id):
        with self._pending_lock:
            batch = self._batch.pop(chat_id, None)
            if not batch or not batch["items"]:
                return
            items = batch["items"]
            existing = self._pending_save.get(chat_id)
            if existing:
                existing["items"].extend(items)
                question_id = existing.get("question_message_id")
            else:
                existing = {"items": items}
                self._pending_save[chat_id] = existing
                question_id = None
        count = len(existing["items"])
        if question_id:
            try:
                self._api("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": question_id,
                    "text": f"📥 收到 {count} 个视频，保存到哪里？",
                })
                return
            except Exception:  # noqa: BLE001
                pass
        msg_id = self._send_message(
            chat_id,
            f"📥 收到 {count} 个视频，保存到哪里？",
            reply_markup=self._folder_keyboard(),
        )
        if msg_id:
            with self._pending_lock:
                pending = self._pending_save.get(chat_id)
                if pending:
                    pending["question_message_id"] = msg_id

    def _start_save_to_folder(self, chat_id, pending, folder):
        with self._pending_lock:
            self._pending_save.pop(chat_id, None)
        threading.Thread(target=self._save_worker, args=(chat_id, pending, folder), daemon=True).start()

    def _save_worker(self, chat_id, pending, folder):
        items = pending.get("items") if isinstance(pending, dict) and "items" in pending else [pending]
        total = len(items)
        saved = []
        try:
            for i, item in enumerate(items, 1):
                if item.get("link"):
                    path = self._sync.download_message_media(item["link"], folder)
                elif item.get("file_id"):
                    path = self._download_bot_file(item["file_id"], folder)
                else:
                    continue
                saved.append(path)
                if total > 1:
                    self._send_message(chat_id, f"⏳ 已下载 {i}/{total}")
            if not saved:
                self._send_message(chat_id, "❌ 没有可下载的内容")
            elif total > 1:
                self._send_message(chat_id, f"✅ 批次完成：{len(saved)}/{total} 个已保存到 {folder}")
            else:
                self._send_message(chat_id, f"✅ 已保存：{saved[0]}")
        except Exception as exc:  # noqa: BLE001
            self._send_message(chat_id, f"❌ 下载失败（已完成 {len(saved)}/{total}）：{exc}")

    def _download_bot_file(self, file_id, dest_dir):
        info = self._api("getFile", {"file_id": file_id})
        if not info.get("ok"):
            raise RuntimeError(info.get("description", "getFile 失败"))
        file_path = info["result"]["file_path"]
        url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(file_path).name
        with _API_OPENER.open(url, timeout=600) as resp, open(dest, "wb") as handle:
            shutil.copyfileobj(resp, handle)
        return str(dest)

    def _handle_callback(self, callback):
        data = callback.get("data") or ""
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")
        sender = callback.get("from") or {}
        if self._owner_id is None:
            try:
                self._owner_id = self._sync.owner_id()
            except Exception:  # noqa: BLE001
                self._owner_id = None
        if not (self._owner_id and sender.get("id") == self._owner_id):
            return
        try:
            self._api("answerCallbackQuery", {"callback_query_id": callback.get("id", "")})
        except Exception:  # noqa: BLE001
            pass
        if not chat_id:
            return
        if data == "save:new":
            with self._pending_lock:
                self._pending_save.setdefault(chat_id, {})["awaiting_name"] = True
            try:
                self._api("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": "请输入新文件夹名称（不要包含 \\ / : * ? \" < > |）：",
                })
            except Exception:  # noqa: BLE001
                pass
        elif data == "save:cancel":
            with self._pending_lock:
                self._pending_save.pop(chat_id, None)
            try:
                self._api("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": "已取消。"})
            except Exception:  # noqa: BLE001
                pass
        elif data.startswith("save:existing:"):
            try:
                idx = int(data.rsplit(":", 1)[1])
            except ValueError:
                return
            root = self._download_root()
            folders = sorted([p for p in root.iterdir() if p.is_dir()]) if root.exists() else []
            if idx < 0 or idx >= len(folders):
                return
            with self._pending_lock:
                pending = self._pending_save.get(chat_id, {})
            try:
                self._api("editMessageText", {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"⏳ 正在下载到 {folders[idx].name} …",
                })
            except Exception:  # noqa: BLE001
                pass
            self._start_save_to_folder(chat_id, pending, folders[idx])

    # ── Polling loop ─────────────────────────────────────────────

    def _poll(self):
        self._running = True
        self._last_error = ""
        while self._running:
            try:
                result = self._api("getUpdates", {
                    "offset": self._offset + 1,
                    "timeout": 10,
                    "allowed_updates": ["message", "callback_query"],
                })
                if not result.get("ok"):
                    self._last_error = result.get("description", "Unknown API error")
                    time.sleep(5)
                    continue
                for update in result.get("result", []):
                    self._offset = max(self._offset, update["update_id"])
                    if "callback_query" in update:
                        self._handle_callback(update["callback_query"])
                        continue
                    msg = update.get("message") or update.get("channel_post")
                    if not msg:
                        continue
                    if not self._is_owner(msg):
                        continue
                    chat_id = msg["chat"]["id"]
                    text = msg.get("text") or msg.get("caption") or ""
                    # Pending new-folder name input
                    with self._pending_lock:
                        pending = self._pending_save.get(chat_id)
                    if pending and pending.get("awaiting_name"):
                        name = text.strip()
                        if not name or any(ch in name for ch in '\\/:*?"<>|'):
                            self._send_message(chat_id, "文件夹名称无效（不能包含 \\ / : * ? \" < > |），请重新输入：")
                            continue
                        with self._pending_lock:
                            pending.pop("awaiting_name", None)
                        root = self._download_root() / name
                        try:
                            root.mkdir(parents=True, exist_ok=True)
                        except Exception as exc:  # noqa: BLE001
                            self._send_message(chat_id, f"创建文件夹失败：{exc}")
                            continue
                        self._start_save_to_folder(chat_id, pending, root)
                        continue
                    if text.strip().startswith("/"):
                        self._handle_commands(chat_id, text)
                        continue
                    message_links = self._extract_message_links(text)
                    if message_links:
                        target, mid = message_links[0]
                        link = f"https://t.me/{target}/{mid}"
                        self._begin_media_save(chat_id, link=link)
                        continue
                    video = msg.get("video") or msg.get("document")
                    if video and (msg.get("video") or (video.get("mime_type") or "").startswith("video/")):
                        file_name = (
                            video.get("file_name")
                            or (video.get("file_unique_id", "video") + ".mp4")
                        )
                        self._begin_media_save(chat_id, file_id=video["file_id"], file_name=file_name)
                        continue
                    links = self._extract_links(text)
                    if not links:
                        continue
                    # Process each link in a background thread so polling continues
                    for target in links:
                        with self._importing_lock:
                            if target in self._importing:
                                continue
                            self._importing.add(target)
                        threading.Thread(
                            target=self._import_and_track,
                            args=(chat_id, target),
                            daemon=True,
                        ).start()
                self._last_error = ""
            except Exception as exc:
                self._last_error = str(exc)
                time.sleep(5)

    # ── Remote control commands (owner only) ─────────────────────

    def _is_owner(self, msg):
        sender_id = (msg.get("from") or {}).get("id")
        if not sender_id:
            return False
        if self._owner_id is None:
            try:
                self._owner_id = self._sync.owner_id()
            except Exception:  # noqa: BLE001
                self._owner_id = None
        return self._owner_id is not None and sender_id == self._owner_id

    def _handle_commands(self, chat_id, text):
        cmd = text.strip().split()[0].lower()
        if cmd in ("/help", "/start"):
            self._send_message(
                chat_id,
                "📋 TG Reaction Ranker 远程控制\n\n"
                "/status — 服务与下载状态\n"
                "/channels — 已导入频道列表\n"
                "/download <链接> — 下载频道\n"
                "/stop — 停止当前下载\n\n"
                "直接发送频道链接也会自动下载。",
            )
        elif cmd == "/status":
            self._send_message(chat_id, self._status_text())
        elif cmd == "/channels":
            self._send_message(chat_id, self._channels_text())
        elif cmd == "/stop":
            result = self._sync.stop_sync()
            if result.get("ok"):
                self._send_message(chat_id, "⏹ 正在停止当前任务…")
            else:
                self._send_message(chat_id, "ℹ️ " + result.get("message", "当前没有运行中的任务"))
        elif cmd == "/download":
            rest = text[len(cmd):].strip()
            targets = self._extract_links(rest)
            if not targets:
                self._send_message(chat_id, "请附上频道链接，例如：/download https://t.me/channel")
                return
            for target in targets[:3]:
                with self._importing_lock:
                    if target in self._importing:
                        continue
                    self._importing.add(target)
                threading.Thread(target=self._import_and_track, args=(chat_id, target), daemon=True).start()
        else:
            self._send_message(chat_id, f"未知命令：{cmd}\n发送 /help 查看可用命令。")

    def _status_text(self):
        try:
            st = self._sync.public_status()
        except Exception as exc:  # noqa: BLE001
            return f"状态获取失败：{exc}"
        lines = ["📊 TG Reaction Ranker 状态"]
        lines.append("账号登录：" + ("✅" if st.get("authorized") else "❌"))
        lines.append("当前目标：" + (st.get("target") or "未设置"))
        job = st.get("job") or {}
        stage = job.get("stage", "idle")
        msg = job.get("message", "")
        lines.append("任务：" + stage + (f"（{msg}）" if msg else ""))
        if job.get("progress"):
            lines.append(f"进度：{job.get('progress')} 条消息，{job.get('media_downloaded', 0)} 个媒体")
        lines.append("并行下载：" + str(st.get("download_parallel", 3)))
        lines.append("定时同步：" + ("开启" if st.get("schedule_active") else "关闭"))
        lines.append("Bot：" + ("在线 ✅" if st.get("bot", {}).get("running") else "离线 ❌"))
        return "\n".join(lines)

    def _channels_text(self):
        root = Path(self._sync.archive_root)
        if not root.exists():
            return "还没有导入任何频道。"
        dirs = [p for p in sorted(root.iterdir()) if p.is_dir()]
        if not dirs:
            return "还没有导入任何频道。"
        lines = [f"📁 已导入频道（{len(dirs)}）"]
        for path in dirs[:30]:
            count = ""
            result_file = path / "result.json"
            if result_file.exists():
                try:
                    data = json.loads(result_file.read_text(encoding="utf-8"))
                    count = f"（{len(data.get('messages', []))} 条）"
                except Exception:  # noqa: BLE001
                    pass
            lines.append("• " + path.name + count)
        if len(dirs) > 30:
            lines.append(f"…还有 {len(dirs) - 30} 个")
        return "\n".join(lines)

    # ── Public API ───────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    @property
    def status(self):
        return {
            "running": self._running,
            "error": self._last_error,
            "offset": self._offset,
        }
