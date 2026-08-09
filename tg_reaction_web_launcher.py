import http.server
import difflib
import html
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from telegram_sync import TelegramSyncService

if sys.platform == "darwin":
    import rumps


APP_FILE = "tg_reaction_web.html"
PREFERRED_PORT = 1717
APP_VERSION = "2026-08-09-file-browser"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
INVALID_NAME_CHARS = '<>:"/\\|?*' if os.name == "nt" else ':/'


def default_data_dir():
    """Data directory for preferences/config/session files.

    TELERANK_DATA_DIR overrides the default (used by the Windows service so
    settings live under C:\\ProgramData instead of the service account profile).
    """
    override = os.environ.get("TELERANK_DATA_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / "TelegramReactionRanker"


def default_archive_root():
    override = os.environ.get("TELERANK_ARCHIVE_ROOT", "").strip()
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "TelegramReactionRanker" / "Imports"
    if os.name == "nt":
        return Path("D:/TelegramReactionRanker/Imports")
    return Path.home() / "TelegramReactionRanker" / "Imports"


def default_archive_trash_root():
    override = os.environ.get("TELERANK_TRASH_ROOT", "").strip()
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "TelegramReactionRanker" / "DeletedImports"
    if os.name == "nt":
        return Path("D:/TelegramReactionRanker/DeletedImports")
    return Path.home() / "TelegramReactionRanker" / "DeletedImports"


PREFS_FILE = default_data_dir() / "preferences.json"
ARCHIVE_ROOT = default_archive_root()


def default_file_root():
    """Root folder for the LAN file browser (phone access).

    Priority: TELERANK_FILE_ROOT env > preferences["file_root"] > archive root.
    """
    override = os.environ.get("TELERANK_FILE_ROOT", "").strip()
    if override:
        return Path(override)
    try:
        if PREFS_FILE.exists():
            data = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
            if data.get("file_root"):
                return Path(data["file_root"])
    except Exception:
        pass
    return ARCHIVE_ROOT


FILE_ROOT = default_file_root()
CHANNEL_NAME_HANDLE_HINTS = {
    # Add your channel name → handle mappings here
    # "Name": "handle",
}
TELEGRAM_CHANNEL_REDIRECTS = {
    # Add your handle redirects here
    # "old_handle": "new_handle",
}
SCAN_CACHE = {}
SCAN_LOCK = threading.Lock()
HANDLE_TITLE_CACHE = {}
HANDLE_TITLE_CACHE_FILE = PREFS_FILE.parent / "handle-title-cache.json"
TELEGRAM_SERVICE = None


def _load_handle_cache():
    try:
        if HANDLE_TITLE_CACHE_FILE.exists():
            data = json.loads(HANDLE_TITLE_CACHE_FILE.read_text(encoding="utf-8"))
            HANDLE_TITLE_CACHE.update({str(k).lower(): v for k, v in data.items() if isinstance(v, str)})
    except Exception:
        pass


def _save_handle_cache():
    try:
        HANDLE_TITLE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        HANDLE_TITLE_CACHE_FILE.write_text(
            json.dumps(HANDLE_TITLE_CACHE, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


_load_handle_cache()


def app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def load_preferences():
    try:
        if PREFS_FILE.exists():
            return json.loads(PREFS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"last_import_path": ""}


def save_preferences(data):
    try:
        PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PREFS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def pick_folder_native():
    if os.name == "nt":
        return pick_folder_windows()
    try:
        result = subprocess.run(
            ["osascript",
             "-e", 'tell application "System Events" to set frontApp to name of first application process whose frontmost is true',
             "-e", 'set chosen to choose folder with prompt "Select Telegram export folder:"',
             "-e", 'return POSIX path of chosen'],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None
    except Exception:
        return None


def pick_folder_windows():
    """Native folder picker for Windows (runs an STA PowerShell dialog)."""
    try:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$d.Description = 'Select Telegram export folder:'; "
            "$d.ShowNewFolderButton = $false; "
            "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.SelectedPath }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None
    except Exception:
        return None


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "TGReactionRanker/1.0"

    def is_local_client(self):
        """Only the computer running the app may open native or destructive actions."""
        return self.client_address[0] in {"127.0.0.1", "::1"}

    @staticmethod
    def is_archived_import_path(raw_path):
        """LAN viewers may only reopen exports already archived by the host app."""
        try:
            candidate = Path(raw_path).expanduser().resolve()
            archive_root = ARCHIVE_ROOT.resolve()
            return candidate == archive_root or archive_root in candidate.parents
        except (OSError, RuntimeError):
            return False

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        if path in {"/", "/index.html", f"/{APP_FILE}"}:
            self.send_file(app_dir() / APP_FILE)
            return
        if path == "/api/imports":
            self.send_json({"ok": True, "root": str(ARCHIVE_ROOT), "imports": list_archived_imports()})
            return
        if path == "/api/preferences":
            self.send_json(load_preferences())
            return
        if path == "/api/files":
            self.handle_files_browser()
            return
        if path == "/api/file":
            self.handle_file_download()
            return
        if path == "/api/zip":
            self.handle_zip_download()
            return
        if path == "/api/pick-folder":
            self.handle_pick_folder()
            return
        if path == "/api/telegram/status":
            self.handle_telegram_status()
            return
        if path.startswith("/api/local-file/"):
            self.send_cached_path(path, "files")
            return
        if path.startswith("/api/local-media/"):
            self.send_cached_path(path, "media")
            return
        self.send_error(404, "Not found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        if path == "/api/scan-path":
            self.handle_scan_path()
            return
        if path == "/api/scan-batch":
            self.handle_scan_batch()
            return
        if path == "/api/scan-directory":
            self.handle_scan_directory()
            return
        if path == "/api/delete-import":
            self.handle_delete_import()
            return
        if path == "/api/preferences":
            self.handle_save_preferences()
            return
        if path == "/api/telegram/configure":
            self.handle_telegram_configure()
            return
        if path == "/api/telegram/send-code":
            self.handle_telegram_send_code()
            return
        if path == "/api/telegram/verify-code":
            self.handle_telegram_verify_code()
            return
        if path == "/api/telegram/verify-password":
            self.handle_telegram_verify_password()
            return
        if path == "/api/telegram/sync":
            self.handle_telegram_sync()
            return
        if path == "/api/telegram/quick-import":
            self.handle_telegram_quick_import()
            return
        if path == "/api/telegram/bot-configure":
            self.handle_telegram_bot_configure()
            return
        if path == "/__shutdown":
            if not self.is_local_client():
                self.send_json({"ok": False, "error": "shutdown is only available on the host computer"}, 403)
                return
            self.send_response(204)
            self.end_headers()
            threading.Thread(target=self.shutdown_server, daemon=True).start()
            return
        self.send_error(404, "Not found")

    def send_file(self, path):
        if not path.exists():
            self.send_error(500, "App file missing")
            return
        data = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "text/html"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-TG-Ranker-Version", APP_VERSION)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-TG-Ranker-Version", APP_VERSION)
        self.end_headers()
        self.wfile.write(data)

    def read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > 1024 * 1024:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def handle_scan_path(self):
        if not self.is_local_client():
            self.send_json({"ok": False, "error": "direct path scans are only available on the host computer"}, 403)
            return
        try:
            body = self.read_json_body()
            raw_path = str(body.get("path", "")).strip().strip('"')
            if not raw_path:
                self.send_json({"ok": False, "error": "path is required"}, 400)
                return
            payload = scan_export_path(raw_path)
            self.send_json(payload)
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"ok": False, "error": f"scan failed: {exc}"}, 500)

    def handle_scan_batch(self):
        if not self.is_local_client():
            self.send_json({"ok": False, "error": "batch scans are only available on the host computer"}, 403)
            return
        try:
            body = self.read_json_body()
            paths = body.get("paths", body.get("path", []))
            if isinstance(paths, str):
                paths = [p.strip().strip('"') for p in paths.split("\n") if p.strip()]
            if not paths:
                self.send_json({"ok": False, "error": "paths array is required"}, 400)
                return
            payload = scan_export_batch(paths)
            self.send_json(payload)
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"ok": False, "error": f"batch scan failed: {exc}"}, 500)

    def handle_scan_directory(self):
        try:
            body = self.read_json_body()
            raw_path = str(body.get("path", "")).strip().strip('"')
            if not raw_path:
                self.send_json({"ok": False, "error": "path is required"}, 400)
                return
            if not self.is_local_client() and not self.is_archived_import_path(raw_path):
                # try to find the matching archived path
                for entry in list_archived_imports():
                    if entry.get("path") == raw_path:
                        raw_path = entry["path"]
                        break
                else:
                    self.send_json({"ok": False, "error": "LAN viewers can only load imports archived by this app"}, 403)
                    return
            payload = scan_export_directory(raw_path)
            self.send_json(payload)
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"ok": False, "error": f"directory scan failed: {exc}"}, 500)

    def handle_delete_import(self):
        if not self.is_local_client():
            self.send_json({"ok": False, "error": "deleting imports is only available on the host computer"}, 403)
            return
        try:
            body = self.read_json_body()
            raw_path = str(body.get("path", "")).strip().strip('"')
            if not raw_path:
                self.send_json({"ok": False, "error": "path is required"}, 400)
                return
            self.send_json(delete_archived_import(raw_path))
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"ok": False, "error": f"delete failed: {exc}"}, 500)

    def handle_pick_folder(self):
        if not self.is_local_client():
            self.send_json({"ok": False, "error": "folder selection is only available on the host computer"}, 403)
            return
        if os.environ.get("TG_RANKER_NO_BROWSER") == "1":
            # Running as a background service (session 0): a GUI dialog would be
            # invisible, so tell the user to type the path instead.
            self.send_json({"ok": False, "error": "folder picker is not available while running as a background service; type the folder path manually"}, 400)
            return
        folder = pick_folder_native()
        if folder:
            self.send_json({"ok": True, "path": folder})
        else:
            self.send_json({"ok": False, "error": "no folder selected"}, 400)

    def handle_save_preferences(self):
        if not self.is_local_client():
            self.send_json({"ok": False, "error": "saving preferences is only available on the host computer"}, 403)
            return
        try:
            body = self.read_json_body()
            success = save_preferences(body)
            self.send_json({"ok": success})
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 500)

    def _telegram_local_only(self):
        if self.is_local_client():
            return True
        self.send_json({"ok": False, "error": "Telegram account controls are only available on the host computer"}, 403)
        return False

    def handle_telegram_status(self):
        if not self._telegram_local_only():
            return
        self.send_json(get_telegram_service().public_status())

    def handle_telegram_configure(self):
        if not self._telegram_local_only():
            return
        try:
            self.send_json(get_telegram_service().configure(self.read_json_body()))
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)

    def handle_telegram_send_code(self):
        if not self._telegram_local_only():
            return
        try:
            self.send_json(get_telegram_service().send_code(self.read_json_body().get("phone")))
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)

    def handle_telegram_verify_code(self):
        if not self._telegram_local_only():
            return
        try:
            self.send_json(get_telegram_service().verify_code(self.read_json_body().get("code")))
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)

    def handle_telegram_verify_password(self):
        if not self._telegram_local_only():
            return
        try:
            self.send_json(get_telegram_service().verify_password(self.read_json_body().get("password")))
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)

    def handle_telegram_sync(self):
        if not self._telegram_local_only():
            return
        try:
            self.send_json(get_telegram_service().start_sync())
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)

    def handle_telegram_quick_import(self):
        if not self._telegram_local_only():
            return
        try:
            self.send_json(get_telegram_service().quick_import(self.read_json_body().get("target")))
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)

    def handle_telegram_bot_configure(self):
        if not self._telegram_local_only():
            return
        try:
            self.send_json(get_telegram_service().configure_bot(self.read_json_body().get("bot_token", "")))
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)

    def send_cached_path(self, request_path, bucket):
        parts = request_path.strip("/").split("/")
        if len(parts) != 4:
            self.send_error(404, "Not found")
            return
        _api, _kind, scan_id, token = parts
        with SCAN_LOCK:
            record = SCAN_CACHE.get(scan_id, {})
            target = record.get(bucket, {}).get(token)
        if not target:
            self.send_error(404, "Not found")
            return
        self.send_file(Path(target))

    def handle_files_browser(self):
        """LAN file browser: list the archive root or a subfolder."""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        raw_path = params.get("path", [""])[0]
        target = safe_file_root_path(raw_path or str(FILE_ROOT))
        if target is None:
            self.send_json({"ok": False, "error": "path is outside the archive"}, 403)
            return
        if not target.exists():
            self.send_json({"ok": False, "error": "path does not exist"}, 404)
            return
        entries = []
        if target.is_dir():
            for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if child.name.startswith("."):
                    continue
                entries.append({
                    "name": child.name,
                    "path": str(child),
                    "is_dir": child.is_dir(),
                    "size": child.stat().st_size if child.is_file() else 0,
                })
        parent = str(target.parent) if target.resolve() != FILE_ROOT.resolve() else None
        self.send_json({"ok": True, "root": str(target), "parent": parent, "entries": entries})

    def handle_file_download(self):
        """Serve any file under the archive root, with HTTP Range support for video."""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        raw_path = params.get("path", [""])[0]
        target = safe_file_root_path(raw_path)
        if target is None or not target.exists() or not target.is_file():
            self.send_json({"ok": False, "error": "file not found"}, 404)
            return
        size = target.stat().st_size
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/"):
            content_type += "; charset=utf-8"
        start, end = 0, size - 1
        status = 200
        range_header = self.headers.get("Range")
        if range_header:
            match = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if match:
                start = int(match.group(1)) if match.group(1) else 0
                end = int(match.group(2)) if match.group(2) else size - 1
                end = min(end, size - 1)
                if start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(target, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def handle_zip_download(self):
        """Zip a folder/file under the archive root for phone download."""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        raw_path = params.get("path", [""])[0]
        target = safe_file_root_path(raw_path)
        if target is None or not target.exists():
            self.send_json({"ok": False, "error": "path not found"}, 404)
            return
        import tempfile
        import zipfile

        files_to_zip = [path for path in target.rglob("*") if path.is_file()] if target.is_dir() else [target]
        total_size = sum(path.stat().st_size for path in files_to_zip)
        if total_size > 4 * 1024 * 1024 * 1024:
            self.send_json({"ok": False, "error": "folder is too large to zip (over 4GB); download files individually instead"}, 400)
            return

        fd, tmp = tempfile.mkstemp(suffix=".zip", dir=str(target.parent))
        os.close(fd)
        try:
            base = target.name or "archive"
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
                for path in sorted(files_to_zip):
                    if target.is_dir():
                        zf.write(path, arcname=str(Path(base) / path.relative_to(target)))
                    else:
                        zf.write(path, arcname=base)
            data_size = os.path.getsize(tmp)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(data_size))
            self.send_header("Content-Disposition", f'attachment; filename="{base}.zip"')
            self.end_headers()
            with open(tmp, "rb") as handle:
                shutil.copyfileobj(handle, self.wfile)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def shutdown_server(self):
        time.sleep(0.2)
        self.server.shutdown()

    def log_message(self, _format, *_args):
        return


def normalize_key(path):
    return urllib.parse.unquote(str(path).replace("\\", "/").lstrip("./")).lower()


def strip_tags(value):
    return re.sub(r"<[^>]+>", " ", value or "")


def normalize_space(value):
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def remap_telegram_channel(channel):
    value = str(channel or "").strip().lstrip("@")
    if not value:
        return ""
    return TELEGRAM_CHANNEL_REDIRECTS.get(value.lower(), value)


def safe_folder_name(value):
    cleaned = "".join("_" if char in INVALID_NAME_CHARS or ord(char) < 32 else char for char in value)
    cleaned = normalize_space(cleaned).strip(" .")
    if not cleaned:
        cleaned = "Telegram export"
    return cleaned[:140].rstrip(" .")


def get_telegram_service():
    global TELEGRAM_SERVICE
    if TELEGRAM_SERVICE is None:
        TELEGRAM_SERVICE = TelegramSyncService(PREFS_FILE.parent, ARCHIVE_ROOT, safe_folder_name, save_preferences)
    return TELEGRAM_SERVICE


def read_text_sample(path, limit=512 * 1024):
    data = path.read_bytes()[:limit]
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def extract_chat_title(message_files):
    for path in message_files[:3]:
        if path.suffix.lower() not in {".html", ".htm"}:
            continue
        text = read_text_sample(path)
        header_match = re.search(
            r'<div\s+class="page_header"[\s\S]*?<div\s+class="text bold">\s*([\s\S]*?)\s*</div>',
            text,
            re.IGNORECASE,
        )
        if header_match:
            title = normalize_space(strip_tags(header_match.group(1)))
            if title:
                return title
        from_match = re.search(r'<div\s+class="from_name">\s*([\s\S]*?)\s*</div>', text, re.IGNORECASE)
        if from_match:
            title = normalize_space(strip_tags(from_match.group(1)))
            if title:
                return title
    return ""


def infer_handle_from_name(title):
    for marker, handle in CHANNEL_NAME_HANDLE_HINTS.items():
        if marker and marker in title:
            return handle
    return ""


def infer_handle_from_paths(root):
    candidates = [root.name, str(root)]
    for text in candidates:
        match = re.search(r"@([A-Za-z0-9_]{3,})", text)
        if match:
            return remap_telegram_channel(match.group(1))
    return ""


def is_under_archive_root(root):
    try:
        root.resolve().relative_to(ARCHIVE_ROOT.resolve())
        return True
    except OSError:
        return False
    except ValueError:
        return False


def safe_archive_path(raw):
    """Resolve a path and ensure it stays inside the archive root (or is the root)."""
    if not raw:
        return None
    try:
        candidate = Path(raw).expanduser().resolve()
        root = ARCHIVE_ROOT.resolve()
        if candidate == root or root in candidate.parents:
            return candidate
    except (OSError, RuntimeError):
        pass
    return None


def safe_file_root_path(raw):
    """Resolve a path and ensure it stays inside the file-browser root (or is the root)."""
    if not raw:
        return None
    try:
        candidate = Path(raw).expanduser().resolve()
        root = FILE_ROOT.resolve()
        if candidate == root or root in candidate.parents:
            return candidate
    except (OSError, RuntimeError):
        pass
    return None


def delete_archived_import(raw_path):
    target = Path(raw_path).expanduser()
    if not target.exists():
        raise ValueError("import folder no longer exists")
    if not target.is_dir():
        raise ValueError("import path is not a folder")
    if not is_under_archive_root(target):
        raise ValueError("only archived imports can be deleted here")
    try:
        if target.resolve() == ARCHIVE_ROOT.resolve():
            raise ValueError("archive root cannot be deleted")
    except OSError as exc:
        raise ValueError(f"cannot resolve import folder: {exc}") from exc

    trash_root = default_archive_trash_root()
    try:
        trash_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"cannot create trash folder: {exc}") from exc
    destination = trash_root / target.name
    if destination.exists() or (destination.resolve() == target.resolve()):
        destination = trash_root / f"{target.name}-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        shutil.move(str(target), str(destination))
    except OSError as exc:
        raise ValueError(f"cannot move import to trash: {exc}") from exc
    return {
        "ok": True,
        "deleted": str(target),
        "trash": str(destination),
    }


def normalize_title_for_compare(value):
    text = normalize_space(value).lower()
    text = re.sub(r"telegram:\s*contact\s*@?[a-z0-9_]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"@[a-z0-9_]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text, flags=re.UNICODE)
    return text


def titles_match(export_title, remote_title):
    left = normalize_title_for_compare(export_title)
    right = normalize_title_for_compare(remote_title)
    if not left or not right:
        return False
    if left == right:
        return True
    if min(len(left), len(right)) >= 6 and (left in right or right in left):
        return True
    return difflib.SequenceMatcher(None, left, right).ratio() >= 0.72


def extract_remote_title(text):
    patterns = [
        r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:title["\']',
        r'<title>([\s\S]*?)</title>',
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            return normalize_space(strip_tags(match.group(1)))
    return ""


def fetch_telegram_public_title(handle):
    clean = remap_telegram_channel(handle)
    if not clean:
        return ""
    key = clean.lower()
    if key in HANDLE_TITLE_CACHE:
        return HANDLE_TITLE_CACHE[key]
    title = ""
    for url in (f"https://t.me/s/{clean}", f"https://t.me/{clean}"):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=5) as response:
                text = response.read(256 * 1024).decode("utf-8", errors="ignore")
            title = extract_remote_title(text)
            if title:
                break
        except Exception:
            continue
    HANDLE_TITLE_CACHE[key] = title
    _save_handle_cache()
    return title


def add_handle_candidate(candidates, handle, score):
    clean = remap_telegram_channel(handle)
    if not clean or clean.lower().endswith("bot"):
        return
    lowered = clean.lower()
    current = candidates.get(lowered)
    if not current or score > current[0]:
        candidates[lowered] = (score, clean)


def collect_handle_candidates(message_files):
    candidates = {}
    for path in message_files[:8]:
        if path.suffix.lower() not in {".html", ".htm"}:
            continue
        text = read_text_sample(path, 1024 * 1024)
        for match in re.finditer(r"https?://(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]{3,})(?:/|\b)", text, re.IGNORECASE):
            add_handle_candidate(candidates, match.group(1), 30)
        for match in re.finditer(r"@([A-Za-z0-9_]{3,})", text, re.IGNORECASE):
            add_handle_candidate(candidates, match.group(1), 12)
    return [item[1] for item in sorted(candidates.values(), key=lambda item: -item[0])]


def search_handle_candidates_by_title(title):
    if not title:
        return []
    query = urllib.parse.quote(f'"{title}" "t.me"')
    urls = [
        f"https://www.bing.com/search?q={query}",
        f"https://r.jina.ai/http://www.bing.com/search?q={query}",
    ]
    candidates = {}
    for url in urls:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=8) as response:
                text = response.read(512 * 1024).decode("utf-8", errors="ignore")
        except Exception:
            continue
        decoded = html.unescape(text)
        patterns = [
            r"(?:https?://)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]{3,})",
            r"tgstat\.com/channel/%40([A-Za-z0-9_]{3,})",
            r"telemetr\.io/(?:en/)?channels/[^\"'<>\s]*-([A-Za-z0-9_]{3,})",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, decoded, re.IGNORECASE):
                add_handle_candidate(candidates, match.group(1), 20)
    return [item[1] for item in sorted(candidates.values(), key=lambda item: -item[0])]


def infer_handle_from_verified_title(title, message_files):
    candidates = collect_handle_candidates(message_files)
    candidates.extend(search_handle_candidates_by_title(title))
    seen = set()
    for handle in candidates[:24]:
        clean = remap_telegram_channel(handle)
        lowered = clean.lower()
        if not clean or lowered in seen:
            continue
        seen.add(lowered)
        remote_title = fetch_telegram_public_title(clean)
        if titles_match(title, remote_title):
            return clean
    return ""


def infer_handle_from_export(message_files):
    counts = {}
    message_ids = set()
    samples = []
    for path in message_files[:8]:
        if path.suffix.lower() not in {".html", ".htm"}:
            continue
        text = read_text_sample(path)
        samples.append(text)
        message_ids.update(re.findall(r'id=["\']message(\d+)["\']', text, re.IGNORECASE))
    for text in samples:
        for match in re.finditer(r"https?://(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]{3,})/(\d+)(?:[/?#][^\"'<\s]*)?", text, re.IGNORECASE):
            raw = match.group(1)
            message_id = match.group(2)
            if raw.lower().endswith("bot"):
                continue
            mapped = remap_telegram_channel(raw)
            stat = counts.setdefault(mapped, {"count": 0, "matched": 0})
            stat["count"] += 1
            if message_id in message_ids:
                stat["matched"] += 1
    if not counts:
        return ""
    ranked = sorted(counts.items(), key=lambda item: (-item[1]["matched"], -item[1]["count"], item[0].lower()))
    best_handle, best = ranked[0]
    second_matched = ranked[1][1]["matched"] if len(ranked) > 1 else 0
    matched = best["matched"]
    match_ratio = matched / max(1, best["count"])
    dominant = matched >= max(8, second_matched * 4)
    strong_match = matched >= 8 and match_ratio >= 0.6 and dominant
    broad_coverage = len(message_ids) >= 50 and matched >= max(20, len(message_ids) // 50) and match_ratio >= 0.6 and dominant
    if strong_match or broad_coverage:
        return best_handle
    return ""


def build_archive_name(root, message_files, allow_remote=False):
    title = extract_chat_title(message_files) or root.name
    title_handle = infer_handle_from_name(title)
    export_handle = infer_handle_from_export(message_files)
    verified_handle = infer_handle_from_verified_title(title, message_files) if allow_remote and not (title_handle or export_handle) else ""
    path_handle = "" if is_under_archive_root(root) else infer_handle_from_paths(root)
    handle = title_handle or export_handle or verified_handle or path_handle
    label = normalize_space(title)
    if handle:
        suffix = f"@{handle}"
        if suffix.lower() not in label.lower():
            label = f"{label} {suffix}"
    return safe_folder_name(label), label, handle


def list_archived_imports():
    if not ARCHIVE_ROOT.exists():
        return []
    items_by_key = {}
    for path in ARCHIVE_ROOT.iterdir():
        if not path.is_dir():
            continue
        name = path.name
        label = name
        handle = ""
        message_files = sorted(
            [p for p in path.glob("messages*.htm*") if p.is_file() and p.stat().st_size > 0],
            key=lambda p: p.name.lower(),
        )
        if message_files:
            _archive_name, label, handle = build_archive_name(path, message_files, allow_remote=False)
        else:
            handle_match = re.search(r"@([A-Za-z0-9_]{3,})", name)
            handle = handle_match.group(1) if handle_match else ""
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0
        item = {
            "label": label,
            "path": str(path),
            "handle": handle,
            "updated": mtime,
            "has_result_json": (path / "result.json").is_file(),
        }
        key = (label.lower(), handle.lower())
        current = items_by_key.get(key)
        handle_suffix = f"@{handle}".lower() if handle else ""
        item_score = (1 if handle_suffix and handle_suffix in path.name.lower() else 0, mtime)
        current_path = Path(current["path"]) if current else None
        current_score = (
            1 if current and handle_suffix and handle_suffix in current_path.name.lower() else 0,
            current["updated"] if current else 0,
        )
        if not current or item_score > current_score:
            items_by_key[key] = item
    items = list(items_by_key.values())
    items.sort(key=lambda item: (-item["updated"], item["label"].lower()))
    return items


def copy_tree_incremental_python(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped = 0
    destination_resolved = destination.resolve()
    for current, dir_names, file_names in os.walk(source):
        current_path = Path(current)
        if current_path.resolve() == destination_resolved:
            dir_names[:] = []
            continue
        rel_dir = current_path.relative_to(source)
        target_dir = destination / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in file_names:
            src = current_path / name
            dst = target_dir / name
            try:
                src_stat = src.stat()
                if dst.exists():
                    dst_stat = dst.stat()
                    if dst_stat.st_size == src_stat.st_size and int(dst_stat.st_mtime) >= int(src_stat.st_mtime):
                        skipped += 1
                        continue
                shutil.copy2(src, dst)
                copied += 1
            except OSError:
                continue
    return {"copied": copied, "skipped": skipped}


def copy_tree_incremental(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        cmd = [
            "robocopy",
            str(source),
            str(destination),
            "/E",
            "/XO",
            "/FFT",
            "/R:1",
            "/W:1",
            "/MT:16",
            "/NFL",
            "/NDL",
            "/NJH",
            "/NJS",
            "/NP",
        ]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags, timeout=900)
        if result.returncode >= 8:
            detail = (result.stderr or result.stdout or "").strip()
            raise OSError(f"robocopy failed with code {result.returncode}: {detail}")
        return {"method": "robocopy", "code": result.returncode, "synced": True}
    stats = copy_tree_incremental_python(source, destination)
    stats["method"] = "python"
    stats["synced"] = True
    return stats


def organize_export_root(root, message_files):
    offline = os.environ.get("TELERANK_OFFLINE", "").strip().lower() in ("1", "true", "yes")
    archive_name, display_name, handle = build_archive_name(root, message_files, allow_remote=not offline)
    archive_root = ARCHIVE_ROOT
    destination = archive_root / archive_name
    try:
        same_root = root.resolve() == destination.resolve()
    except OSError:
        same_root = False
    if same_root:
        stats = {"copied": 0, "skipped": 0}
        return destination, display_name, handle, stats
    stats = copy_tree_incremental(root, destination)
    return destination, display_name, handle, stats


def scan_export_path(raw_path):
    root = Path(raw_path).expanduser()
    if not root.exists():
        raise ValueError("path does not exist")
    if not root.is_dir():
        raise ValueError("path must be a Telegram export folder")

    message_files = sorted(
        [p for p in root.glob("messages*.htm*") if p.is_file() and p.stat().st_size > 0],
        key=lambda p: p.name.lower(),
    )
    json_files = sorted(
        [p for p in root.glob("*.json") if p.is_file() and p.stat().st_size > 0],
        key=lambda p: p.name.lower(),
    )
    if json_files and len(json_files) == 1 and not message_files:
        message_files = json_files
    if not message_files:
        raise ValueError("no non-empty messages*.html or single .json export found")

    original_root = root
    if is_under_archive_root(root):
        # already archived — skip slow network lookups, extract info from folder name
        from_name = root.name
        handle_match = re.search(r"@([A-Za-z0-9_]{3,})", from_name)
        channel_handle = remap_telegram_channel(handle_match.group(1)) if handle_match else ""
        display_name = from_name
        organize_stats = {"copied": 0, "skipped": 0}
    else:
        root, display_name, channel_handle, organize_stats = organize_export_root(root, message_files)

    message_files = sorted(
        [p for p in root.glob("messages*.htm*") if p.is_file() and p.stat().st_size > 0],
        key=lambda p: p.name.lower(),
    )
    json_files = sorted(
        [p for p in root.glob("*.json") if p.is_file() and p.stat().st_size > 0],
        key=lambda p: p.name.lower(),
    )
    if json_files and len(json_files) == 1 and not message_files:
        message_files = json_files

    media_files = sorted(
        [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES],
        key=lambda p: normalize_key(p.relative_to(root)),
    )

    scan_id = uuid.uuid4().hex[:12]
    files = {}
    media = {}
    file_entries = []
    media_entries = []

    for index, path in enumerate(message_files):
        token = str(index)
        files[token] = str(path)
        file_entries.append({
            "name": path.name,
            "size": path.stat().st_size,
            "url": f"/api/local-file/{scan_id}/{token}",
        })

    for index, path in enumerate(media_files):
        token = str(index)
        media[token] = str(path)
        rel = normalize_key(path.relative_to(root))
        keys = sorted({rel, normalize_key(path.name)})
        media_entries.append({
            "keys": keys,
            "url": f"/api/local-media/{scan_id}/{token}",
        })

    with SCAN_LOCK:
        SCAN_CACHE[scan_id] = {"root": str(root), "files": files, "media": media}
        if len(SCAN_CACHE) > 8:
            for old_key in list(SCAN_CACHE)[:-8]:
                SCAN_CACHE.pop(old_key, None)

    return {
        "ok": True,
        "scan_id": scan_id,
        "source": display_name or root.name,
        "display_name": display_name or root.name,
        "channel_handle": channel_handle,
        "root": str(root),
        "original_root": str(original_root),
        "archive_root": str(ARCHIVE_ROOT),
        "organized": organize_stats,
        "files": file_entries,
        "media": media_entries,
        "counts": {
            "message_files": len(file_entries),
            "media_files": len(media_entries),
        },
    }


def find_export_subdirs(parent_path):
    root = Path(parent_path).expanduser()
    if not root.exists() or not root.is_dir():
        return []
    found = []
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        messages = list(sub.glob("messages*.htm*"))
        jsons = list(sub.glob("result*.json"))
        if messages or (len(jsons) == 1 and not messages):
            found.append(str(sub))
    return found


def scan_export_directory(parent_path):
    subdirs = find_export_subdirs(parent_path)
    if not subdirs:
        # No subdirectories with exports — try scanning the path itself
        try:
            result = scan_export_path(parent_path)
            return {"ok": True, "batch": False, "scans": [result], "counts": {"message_files": result.get("counts",{}).get("message_files",0), "media_files": result.get("counts",{}).get("media_files",0), "scanned_dirs": 1}, "errors": []}
        except Exception:
            pass
        raise ValueError("no Telegram export folders found in this directory")
    all_results = []
    errors = []
    for sub in subdirs:
        try:
            result = scan_export_path(sub)
            all_results.append(result)
        except Exception as exc:
            errors.append({"path": sub, "error": str(exc)})
    total_media = sum(r.get("counts", {}).get("media_files", 0) for r in all_results)
    total_files = sum(r.get("counts", {}).get("message_files", 0) for r in all_results)
    return {
        "ok": True,
        "batch": True,
        "parent": parent_path,
        "total": len(all_results),
        "errors": errors,
        "scans": all_results,
        "counts": {"message_files": total_files, "media_files": total_media, "scanned_dirs": len(all_results)},
    }


def scan_export_batch(paths):
    results = []
    errors = []
    for raw_path in paths:
        try:
            result = scan_export_path(raw_path)
            results.append(result)
        except Exception as exc:
            errors.append({"path": raw_path, "error": str(exc)})
    total_media = sum(r.get("counts", {}).get("media_files", 0) for r in results)
    total_files = sum(r.get("counts", {}).get("message_files", 0) for r in results)
    return {
        "ok": True,
        "batch": True,
        "total": len(results),
        "errors": errors,
        "scans": results,
        "counts": {"message_files": total_files, "media_files": total_media, "scanned_dirs": len(results)},
    }


def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def free_port():
    for port in range(PREFERRED_PORT, PREFERRED_PORT + 80):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", 0))
        return sock.getsockname()[1]


def app_url(port):
    return f"http://127.0.0.1:{port}/"


def lan_url(port):
    return f"http://{get_lan_ip()}:{port}/"


def is_serving(port):
    try:
        with urllib.request.urlopen(app_url(port), timeout=0.6) as response:
            version = response.headers.get("X-TG-Ranker-Version", "")
            return response.status == 200 and version == APP_VERSION and b"TG Reaction Ranker" in response.read(2048)
    except Exception:
        return False


def shutdown_existing_server(port):
    try:
        req = urllib.request.Request(app_url(port) + "__shutdown", method="POST")
        with urllib.request.urlopen(req, timeout=0.6) as response:
            return response.status == 204
    except Exception:
        return False


def open_url(url):
    if os.environ.get("TG_RANKER_NO_BROWSER") == "1":
        return
    if sys.platform == "darwin":
        try:
            subprocess.run(["open", url], check=False)
            return
        except Exception:
            pass
    elif os.name == "nt":
        try:
            os.startfile(url)
            return
        except Exception:
            pass
    try:
        webbrowser.open_new_tab(url)
    except Exception:
        pass


def _is_port_occupied(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            s.bind(("0.0.0.0", port))
            return False
    except OSError:
        return True


def _force_kill_stale(port):
    killed = False
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=3,
        )
        pids = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
        for pid in pids:
            try:
                os.kill(int(pid), 9)
                killed = True
            except OSError:
                pass
    except Exception:
        pass
    if killed:
        for _ in range(20):
            if not _is_port_occupied(port):
                break
            time.sleep(0.15)
    return killed


def main():
    if is_serving(PREFERRED_PORT):
        open_url(app_url(PREFERRED_PORT))
        return

    if shutdown_existing_server(PREFERRED_PORT):
        time.sleep(0.3)

    if is_serving(PREFERRED_PORT):
        open_url(app_url(PREFERRED_PORT))
        return

    if _is_port_occupied(PREFERRED_PORT):
        _force_kill_stale(PREFERRED_PORT)

    port = free_port()
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    get_telegram_service().resume_schedule()

    if sys.platform == "darwin":
        _run_macos_menu_bar(port, server)
    else:
        url = app_url(port)
        open_url(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        server.shutdown()


def _run_macos_menu_bar(port, server):
    icon_path = app_dir() / "assets" / "menu_bar_icon.png"

    class MenuBarApp(rumps.App):
        def __init__(self):
            super().__init__(
                "TGR",
                icon=str(icon_path) if icon_path.exists() else None,
                quit_button=None,
            )
            self._port = port
            self._server = server
            self.menu = [
                rumps.MenuItem("Open in Browser", callback=self._open_browser),
                rumps.MenuItem(f"Copy LAN Address", callback=self._copy_lan),
                None,
                rumps.MenuItem(f"Local: http://127.0.0.1:{port}", callback=None),
                rumps.MenuItem(f"LAN:   {lan_url(port)}", callback=None),
                None,
                rumps.MenuItem("Restart", callback=self._restart_app),
                rumps.MenuItem("Quit TG Reaction Ranker", callback=self._quit_app),
            ]
            threading.Timer(0.5, lambda: open_url(app_url(self._port))).start()

        def _open_browser(self, _):
            open_url(app_url(self._port))

        def _copy_lan(self, _):
            subprocess.run(["pbcopy"], input=lan_url(self._port).encode())

        def _restart_app(self, _):
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            if getattr(sys, "frozen", False):
                app_dir = str(Path(sys._MEIPASS).parent.parent)
                subprocess.Popen(["/usr/bin/open", "-n", app_dir], start_new_session=True)
            rumps.quit_application()

        def _quit_app(self, _):
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            rumps.quit_application()

    MenuBarApp().run()


if __name__ == "__main__":
    main()
