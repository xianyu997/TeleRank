import http.server
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


APP_FILE = "tg_reaction_web.html"
PREFERRED_PORT = 1717
APP_VERSION = "2026-07-05-matrix-link-fix"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
ARCHIVE_ROOT = Path("D:/TelegramReactionRanker/Imports")
WINDOWS_INVALID_NAME_CHARS = '<>:"/\\|?*'
CHANNEL_NAME_HANDLE_HINTS = {
    "一姬": "yijiqwq",
    "色色前线": "yijiqwq",
}
TELEGRAM_CHANNEL_REDIRECTS = {
    "yijihimeqwq1": "yijiqwq",
    "yijiqwq": "yijiqwq",
    "yijihimeqwq": "yijiqwq",
}
SCAN_CACHE = {}
SCAN_LOCK = threading.Lock()


def app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "TGReactionRanker/1.0"

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        if path in {"/", "/index.html", f"/{APP_FILE}"}:
            self.send_file(app_dir() / APP_FILE)
            return
        if path == "/api/imports":
            self.send_json({"ok": True, "root": str(ARCHIVE_ROOT), "imports": list_archived_imports()})
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
        if path == "/__shutdown":
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
    cleaned = "".join("_" if char in WINDOWS_INVALID_NAME_CHARS or ord(char) < 32 else char for char in value)
    cleaned = normalize_space(cleaned).strip(" .")
    if not cleaned:
        cleaned = "Telegram export"
    return cleaned[:140].rstrip(" .")


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


def infer_handle_from_export(message_files):
    counts = {}
    for path in message_files[:8]:
        if path.suffix.lower() not in {".html", ".htm"}:
            continue
        text = read_text_sample(path)
        for match in re.finditer(r"https?://(?:t\.me|telegram\.me)/(?!s/)([A-Za-z0-9_]{3,})(?:/|\b)", text, re.IGNORECASE):
            raw = match.group(1)
            mapped = remap_telegram_channel(raw)
            # Prefer channels that are already part of the app's redirect rules. Exported
            # messages often contain many unrelated Telegram links in the message body.
            if raw.lower() not in TELEGRAM_CHANNEL_REDIRECTS and mapped.lower() not in TELEGRAM_CHANNEL_REDIRECTS.values():
                continue
            counts[mapped] = counts.get(mapped, 0) + 1
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def build_archive_name(root, message_files):
    title = extract_chat_title(message_files) or root.name
    handle = infer_handle_from_name(title) or infer_handle_from_paths(root)
    label = normalize_space(title)
    if handle:
        suffix = f"@{handle}"
        if suffix.lower() not in label.lower():
            label = f"{label} {suffix}"
    return safe_folder_name(label), label, handle


def list_archived_imports():
    if not ARCHIVE_ROOT.exists():
        return []
    items = []
    for path in ARCHIVE_ROOT.iterdir():
        if not path.is_dir():
            continue
        name = path.name
        handle_match = re.search(r"@([A-Za-z0-9_]{3,})", name)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0
        items.append({
            "label": name,
            "path": str(path),
            "handle": handle_match.group(1) if handle_match else "",
            "updated": mtime,
        })
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
    archive_name, display_name, handle = build_archive_name(root, message_files)
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


def free_port():
    for port in range(PREFERRED_PORT, PREFERRED_PORT + 80):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def app_url(port):
    return f"http://127.0.0.1:{port}/"


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
    try:
        os.startfile(url)
        return
    except Exception:
        pass
    try:
        webbrowser.open_new_tab(url)
    except Exception:
        pass


def main():
    if is_serving(PREFERRED_PORT):
        open_url(app_url(PREFERRED_PORT))
        return

    if shutdown_existing_server(PREFERRED_PORT):
        time.sleep(0.2)

    if is_serving(PREFERRED_PORT):
        open_url(app_url(PREFERRED_PORT))
        return

    port = free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = app_url(port)
    threading.Timer(0.35, lambda: open_url(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
