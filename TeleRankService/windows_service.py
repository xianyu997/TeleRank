# -*- coding: utf-8 -*-
"""TG Reaction Ranker - Windows background service.

Runs the same HTTP server / Telegram bot listener as
``tg_reaction_web_launcher.py`` under the Windows Service Control Manager,
so the ranking UI and bot auto-import keep running without a console window
or a logged-in desktop session.

Double-click TeleRankService.exe (or run it with no arguments):
it starts the server silently in the background (no terminal window) and opens
the HTML UI in your default browser. If the service is already running, it
simply opens the browser and exits. To stop a manually started instance, run
``stop_service.ps1`` (or ``sc.exe stop TeleRankService`` for the installed
service).

Command line (run the built EXE from an elevated prompt):
    TeleRankService.exe --install [--port=1717] [--data-dir=C:\\ProgramData\\TelegramReactionRanker]
    TeleRankService.exe --remove
    TeleRankService.exe --start | --stop | --restart | --status
    TeleRankService.exe --run [--port=1717] [--data-dir=...]   # foreground mode (also opens browser)

The installed Windows service itself stays headless in the background (it never
opens a browser); only manual launches open the browser.

The service reads optional settings from the registry key
``HKLM\\SYSTEM\\CurrentControlSet\\Services\\TeleRankService\\Parameters``:
    Port        (DWORD)  TCP port to listen on (default 1717)
    DataDir     (SZ)     folder for preferences.json / telegram-sync.json / session / logs
    ArchiveRoot (SZ)     override for the Telegram import archive root
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
import winreg
from pathlib import Path

# pywin32 service plumbing (imported explicitly so PyInstaller bundles them)
import pythoncom  # noqa: F401
import servicemanager
import win32event
import win32service
import win32serviceutil
import win32timezone  # noqa: F401

SERVICE_NAME = "TeleRankService"
DISPLAY_NAME = "TG Reaction Ranker Service"
DESCRIPTION = (
    "Background service for TG Reaction Ranker: serves the ranking web UI on the "
    "LAN and runs the Telegram bot / channel import in the background."
)

DEFAULT_DATA_DIR = r"C:\ProgramData\TelegramReactionRanker"
DEFAULT_PORT = 1717
INFO_FILE = "service-info.json"
LOG_FILE = "service.log"
PARAM_KEY = rf"SYSTEM\CurrentControlSet\Services\{SERVICE_NAME}\Parameters"

_logger = logging.getLogger("telrank.service")


def safe_print(*args, **kwargs):
    """Console output that never crashes the windowed (no-console) EXE."""
    try:
        print(*args, **kwargs)
    except Exception:  # noqa: BLE001 - stdout may be None in windowed mode
        pass


# --------------------------------------------------------------------------
# Registry parameters
# --------------------------------------------------------------------------

def read_service_parameters():
    params = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PARAM_KEY) as key:
            for name in ("Port", "DataDir", "ArchiveRoot", "TrashRoot", "FileRoot", "Proxy"):
                try:
                    params[name] = winreg.QueryValueEx(key, name)[0]
                except OSError:
                    pass
    except OSError:
        pass
    return params


def write_service_parameters(port=None, data_dir=None, archive_root=None, trash_root=None):
    try:
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, PARAM_KEY, 0, winreg.KEY_WRITE) as key:
            if port is not None:
                winreg.SetValueEx(key, "Port", 0, winreg.REG_DWORD, int(port))
            if data_dir is not None:
                winreg.SetValueEx(key, "DataDir", 0, winreg.REG_SZ, str(data_dir))
            if archive_root is not None:
                winreg.SetValueEx(key, "ArchiveRoot", 0, winreg.REG_SZ, str(archive_root))
            if trash_root is not None:
                winreg.SetValueEx(key, "TrashRoot", 0, winreg.REG_SZ, str(trash_root))
        return True
    except OSError as exc:
        safe_print(f"[{SERVICE_NAME}] Could not write service parameters: {exc}")
        return False


# --------------------------------------------------------------------------
# Logging / status file
# --------------------------------------------------------------------------

def setup_logging(data_dir):
    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    if not _logger.handlers:
        _logger.setLevel(logging.INFO)
        try:
            handler = logging.FileHandler(Path(data_dir) / LOG_FILE, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            _logger.addHandler(handler)
        except OSError:
            pass
    return _logger


def write_info_file(data_dir, state, port=None, url=None, lan_url=None, version=None, pid=None):
    payload = {
        "service": SERVICE_NAME,
        "state": state,
        "pid": pid or os.getpid(),
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if port is not None:
        payload["port"] = port
    if url:
        payload["url"] = url
    if lan_url:
        payload["lan_url"] = lan_url
    if version:
        payload["version"] = version
    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        (Path(data_dir) / INFO_FILE).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


# --------------------------------------------------------------------------
# Server startup (shared by service and foreground modes)
# --------------------------------------------------------------------------

def choose_port(preferred):
    import socket

    if preferred:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("0.0.0.0", preferred))
            return preferred
        except OSError:
            _logger.warning("Port %s is in use, picking a free port", preferred)
    import tg_reaction_web_launcher as launcher

    return launcher.free_port()


def start_server(port, logger):
    """Start the HTTP server + Telegram background services.

    ``tg_reaction_web_launcher`` must be imported after TELERANK_DATA_DIR /
    TELERANK_ARCHIVE_ROOT are set, because it resolves paths at import time.
    """
    import http.server

    import tg_reaction_web_launcher as launcher

    http.server.ThreadingHTTPServer.allow_reuse_address = True
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), launcher.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    launcher.get_telegram_service().resume_schedule()
    logger.info("Telegram service resumed (bot listener auto-start if configured)")
    return server, thread, launcher


def resolve_runtime_settings(explicit=None):
    """Merge registry params, environment and explicit CLI args."""
    params = read_service_parameters()
    explicit = explicit or {}

    data_dir = (
        explicit.get("data_dir")
        or params.get("DataDir")
        or os.environ.get("TELERANK_DATA_DIR")
        or DEFAULT_DATA_DIR
    )
    data_dir = str(Path(data_dir).expanduser())
    archive_root = explicit.get("archive_root") or params.get("ArchiveRoot") or ""
    trash_root = explicit.get("trash_root") or params.get("TrashRoot") or ""
    file_root = explicit.get("file_root") or params.get("FileRoot") or ""
    proxy = explicit.get("proxy") or params.get("Proxy") or ""
    try:
        port = int(explicit.get("port") or params.get("Port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT

    os.environ["TELERANK_DATA_DIR"] = data_dir
    if archive_root:
        os.environ["TELERANK_ARCHIVE_ROOT"] = str(archive_root)
    if trash_root:
        os.environ["TELERANK_TRASH_ROOT"] = str(trash_root)
    if file_root:
        os.environ["TELERANK_FILE_ROOT"] = str(file_root)
    if proxy:
        # LocalSystem has no user-level WinINET proxy, so pass it explicitly
        # for the bot API (urllib) and Telethon MTProto.
        os.environ["HTTPS_PROXY"] = proxy
        os.environ["HTTP_PROXY"] = proxy
        os.environ["TELERANK_MT_PROXY"] = proxy
    return data_dir, port, archive_root


# --------------------------------------------------------------------------
# Windows service
# --------------------------------------------------------------------------

class TeleRankService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = DISPLAY_NAME
    _svc_description_ = DESCRIPTION

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self._server = None
        self._server_thread = None
        self._data_dir = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001 - must log everything to event log
            _logger.exception("Service startup failed")
            servicemanager.LogErrorMsg(f"{SERVICE_NAME}: fatal error: {exc}")
            raise
        finally:
            self._cleanup()

    def _run(self):
        data_dir, port, _ = resolve_runtime_settings()
        logger = setup_logging(data_dir)
        logger.info("=== Service starting (pid=%s) ===", os.getpid())
        # The background service must never open a browser window.
        os.environ["TG_RANKER_NO_BROWSER"] = "1"

        chosen = choose_port(port)
        server, thread, launcher = start_server(chosen, logger)
        self._server = server
        self._server_thread = thread
        self._data_dir = data_dir

        local_url = launcher.app_url(chosen)
        lan = launcher.lan_url(chosen)
        logger.info("Listening: %s  (LAN: %s)  version=%s", local_url, lan, launcher.APP_VERSION)
        servicemanager.LogInfoMsg(f"{SERVICE_NAME} listening on {local_url} (LAN {lan})")
        write_info_file(
            data_dir, "running", port=chosen, url=local_url, lan_url=lan,
            version=launcher.APP_VERSION,
        )

        self.ReportServiceStatus(win32service.SERVICE_RUNNING)
        while win32event.WaitForSingleObject(self.hWaitStop, 1000) != win32event.WAIT_OBJECT_0:
            if not thread.is_alive():
                logger.error("HTTP server thread died unexpectedly; stopping service")
                break
        logger.info("Service stop requested")

    def _cleanup(self):
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:  # noqa: BLE001
                pass
        try:
            import tg_reaction_web_launcher as launcher

            launcher.get_telegram_service().stop_schedule()
        except Exception:  # noqa: BLE001
            pass
        if self._data_dir:
            write_info_file(self._data_dir, "stopped")
        _logger.info("Service stopped")


# --------------------------------------------------------------------------
# Foreground (test / manual) mode
# --------------------------------------------------------------------------

def _candidate_data_dirs():
    dirs = []
    for value in (
        os.environ.get("TELERANK_DATA_DIR", ""),
        read_service_parameters().get("DataDir", ""),
        DEFAULT_DATA_DIR,
    ):
        if value:
            dirs.append(str(Path(value).expanduser()))
    seen = set()
    return [d for d in dirs if not (d.lower() in seen or seen.add(d.lower()))]


def _find_running_instance(launcher):
    """Return the URL of an already-running instance, if any."""
    # 1) Fast path: ports recorded in service-info.json of known data dirs.
    for data_dir in _candidate_data_dirs():
        try:
            info_path = Path(data_dir) / INFO_FILE
            if info_path.exists():
                data = json.loads(info_path.read_text(encoding="utf-8"))
                if data.get("state") == "running" and data.get("port"):
                    port = int(data["port"])
                    if launcher.is_serving(port):
                        return launcher.app_url(port)
        except Exception:  # noqa: BLE001
            continue
    # 2) Probe the preferred port and a few fallbacks (service may run there).
    for port in range(launcher.PREFERRED_PORT, launcher.PREFERRED_PORT + 3):
        if launcher.is_serving(port):
            return launcher.app_url(port)
    return None


def _show_fatal_error(message):
    """Show an error dialog (windowed EXE has no console to print to)."""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "TG Reaction Ranker", 0x10)
    except Exception:  # noqa: BLE001
        pass


def run_foreground(explicit=None):
    data_dir, port, _ = resolve_runtime_settings(explicit)
    logger = setup_logging(data_dir)
    logger.info("=== Foreground run (pid=%s) ===", os.getpid())
    try:
        _run_foreground_body(data_dir, port, logger, explicit)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Foreground startup failed")
        _show_fatal_error(
            f"TG Reaction Ranker 启动失败：{exc}\n\n日志文件：{Path(data_dir) / LOG_FILE}"
        )
        raise


def _run_foreground_body(data_dir, port, logger, explicit=None):
    import tg_reaction_web_launcher as launcher

    # If the app is already running (e.g. the installed service), just open the
    # browser and exit instead of starting a second server.
    running_url = _find_running_instance(launcher)
    if running_url:
        url = running_url
        logger.info("App already running at %s; opening browser", url)
        safe_print(f"[{SERVICE_NAME}] App is already running at {url}")
        safe_print(f"[{SERVICE_NAME}] Opening browser...")
        launcher.open_url(url)
        time.sleep(3)
        return

    chosen = choose_port(port)
    server, thread, launcher = start_server(chosen, logger)
    local_url = launcher.app_url(chosen)
    lan = launcher.lan_url(chosen)
    write_info_file(
        data_dir, "running", port=chosen, url=local_url, lan_url=lan,
        version=launcher.APP_VERSION,
    )
    safe_print(f"[{SERVICE_NAME}] Listening on {local_url}")
    safe_print(f"[{SERVICE_NAME}] LAN address: {lan}")
    safe_print(f"[{SERVICE_NAME}] Data dir: {data_dir}")
    safe_print(f"[{SERVICE_NAME}] Opening browser...")
    launcher.open_url(local_url)
    safe_print(f"[{SERVICE_NAME}] Running in background. Stop it with stop_service.ps1 or sc.exe stop {SERVICE_NAME}")
    try:
        while True:
            time.sleep(1)
            if not thread.is_alive():
                safe_print(f"[{SERVICE_NAME}] Server thread died")
                break
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        write_info_file(data_dir, "stopped")
        safe_print(f"[{SERVICE_NAME}] Stopped")


# --------------------------------------------------------------------------
# CLI helpers
# --------------------------------------------------------------------------

def parse_custom_options(args):
    port = data_dir = archive_root = trash_root = None
    for arg in args:
        if arg.startswith("--port="):
            try:
                port = int(arg.split("=", 1)[1])
            except ValueError:
                pass
        elif arg.startswith("--data-dir="):
            data_dir = arg.split("=", 1)[1]
        elif arg.startswith("--archive-root="):
            archive_root = arg.split("=", 1)[1]
        elif arg.startswith("--trash-root="):
            trash_root = arg.split("=", 1)[1]
    return port, data_dir, archive_root, trash_root


def print_status():
    lines = []
    sc_output = subprocess.run(["sc.exe", "query", SERVICE_NAME], capture_output=True, text=True).stdout
    lines.append(sc_output)
    for candidate in [Path(d) / INFO_FILE for d in _candidate_data_dirs()]:
        if candidate.exists():
            try:
                lines.append(f"[{SERVICE_NAME}] status file: {candidate}")
                lines.append(candidate.read_text(encoding="utf-8"))
                break
            except OSError:
                pass
    output = "\n".join(lines)
    safe_print(output)
    # Windowed EXE has no visible console: also write to a readable file.
    try:
        status_file = Path(sys.executable).resolve().parent / f"{SERVICE_NAME}-status.txt"
        status_file.write_text(output, encoding="utf-8")
        safe_print(f"[{SERVICE_NAME}] status written to {status_file}")
    except Exception:  # noqa: BLE001
        pass


def main():
    args = sys.argv[1:]

    if "--install" in args:
        port, data_dir, archive_root, trash_root = parse_custom_options(args)
        write_service_parameters(port=port, data_dir=data_dir, archive_root=archive_root, trash_root=trash_root)

    if not args:
        # Started by SCM without arguments (or double-clicked the EXE).
        try:
            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(TeleRankService)
            servicemanager.StartServiceCtrlDispatcher()
        except Exception as exc:  # noqa: BLE001
            safe_print(f"[{SERVICE_NAME}] Not started by the Service Control Manager ({exc}); falling back to foreground mode")
            run_foreground()
        return

    first = args[0]
    if first == "--run":
        port, data_dir, archive_root, trash_root = parse_custom_options(args)
        run_foreground({"port": port, "data_dir": data_dir, "archive_root": archive_root, "trash_root": trash_root})
        return
    if first == "--service":
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(TeleRankService)
        servicemanager.StartServiceCtrlDispatcher()
        return
    if first == "--status":
        print_status()
        return

    win32serviceutil.HandleCommandLine(TeleRankService)


if __name__ == "__main__":
    main()
