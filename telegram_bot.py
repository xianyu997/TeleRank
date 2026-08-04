"""Telegram Bot listener — receives forwarded channel links and auto-imports."""

import json
import os
import re
import threading
import time
import urllib.request

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

    def _send_message(self, chat_id, text):
        try:
            self._api("sendMessage", {"chat_id": chat_id, "text": text[:4000]})
        except Exception:
            pass

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

    # ── Polling loop ─────────────────────────────────────────────

    def _poll(self):
        self._running = True
        self._last_error = ""
        while self._running:
            try:
                result = self._api("getUpdates", {
                    "offset": self._offset + 1,
                    "timeout": 10,
                    "allowed_updates": ["message"],
                })
                if not result.get("ok"):
                    self._last_error = result.get("description", "Unknown API error")
                    time.sleep(5)
                    continue
                for update in result.get("result", []):
                    self._offset = max(self._offset, update["update_id"])
                    msg = update.get("message") or update.get("channel_post")
                    if not msg:
                        continue
                    chat_id = msg["chat"]["id"]
                    text = msg.get("text") or msg.get("caption") or ""
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
