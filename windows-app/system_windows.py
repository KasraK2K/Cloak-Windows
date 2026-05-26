from __future__ import annotations

import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from models import PingResult

try:
    import winreg
except ImportError:  # pragma: no cover - imported only on Windows.
    winreg = None


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PROXY_REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


@dataclass
class ProxySnapshot:
    proxy_enable: int = 0
    proxy_server: str = ""
    proxy_override: str = ""


class WindowsSystemProxy:
    def __init__(self, backup_file: Path) -> None:
        self.backup_file = backup_file

    def enable(self, host: str, socks_port: int, http_port: int) -> None:
        if winreg is None:
            raise RuntimeError("Windows registry API is not available.")
        host = normalize_proxy_host(host)
        if not self.backup_file.exists():
            self.backup_file.write_text(json.dumps(asdict(self.snapshot()), indent=2), encoding="utf-8")
        proxy_server = f"http={host}:{http_port};https={host}:{http_port};socks={host}:{socks_port}"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROXY_REGISTRY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, proxy_server)
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "<local>")
        refresh_internet_settings()

    def disable(self) -> None:
        if winreg is None:
            return
        snapshot = self._read_backup()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROXY_REGISTRY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, snapshot.proxy_enable)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, snapshot.proxy_server)
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, snapshot.proxy_override)
        try:
            self.backup_file.unlink()
        except FileNotFoundError:
            pass
        refresh_internet_settings()

    def is_enabled(self) -> bool:
        if winreg is None:
            return False
        return self.snapshot().proxy_enable == 1

    def snapshot(self) -> ProxySnapshot:
        if winreg is None:
            return ProxySnapshot()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROXY_REGISTRY_PATH, 0, winreg.KEY_READ) as key:
            return ProxySnapshot(
                proxy_enable=int(_read_reg_value(key, "ProxyEnable", 0)),
                proxy_server=str(_read_reg_value(key, "ProxyServer", "")),
                proxy_override=str(_read_reg_value(key, "ProxyOverride", "")),
            )

    def _read_backup(self) -> ProxySnapshot:
        if not self.backup_file.exists():
            snapshot = self.snapshot()
            snapshot.proxy_enable = 0
            return snapshot
        try:
            return ProxySnapshot(**json.loads(self.backup_file.read_text(encoding="utf-8")))
        except Exception:
            return ProxySnapshot()


def _read_reg_value(key, name: str, default):
    try:
        value, _ = winreg.QueryValueEx(key, name)
        return value
    except OSError:
        return default


def refresh_internet_settings() -> None:
    if sys.platform != "win32":
        return
    internet_set_option = ctypes.windll.Wininet.InternetSetOptionW
    internet_set_option(0, 39, 0, 0)  # INTERNET_OPTION_SETTINGS_CHANGED
    internet_set_option(0, 37, 0, 0)  # INTERNET_OPTION_REFRESH


def normalize_proxy_host(host: str) -> str:
    text = (host or "").strip()
    lowered = text.lower()
    if not lowered or lowered in ("0.0.0.0", "*"):
        return "127.0.0.1"
    if ":" in text and text.count(".") != 3 and not text.startswith("["):
        return f"[{text}]"
    return text


def is_admin() -> bool:
    if sys.platform != "win32":
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(script_path: Path) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Administrator relaunch is only supported on Windows.")
    params = f'"{script_path}"'
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, str(script_path.parent), 1)
    if result <= 32:
        raise RuntimeError(f"ShellExecuteW failed with code {result}.")


def first_available_port(preferred: int, host: str, port_range: range) -> int:
    if is_port_available(host, preferred):
        return preferred
    for port in port_range:
        if is_port_available(host, port):
            return port
    raise RuntimeError("No available local port found.")


def is_port_available(host: str, port: int) -> bool:
    bind_host = host.strip()
    if bind_host in ("", "*"):
        bind_host = "0.0.0.0"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((bind_host, port))
            return True
    except OSError:
        return False


def wait_for_tcp(host: str, port: int, attempts: int = 30, timeout: float = 0.5) -> bool:
    for _ in range(attempts):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            time.sleep(0.35)
    return False


def read_netstat_bytes() -> tuple[int, int] | None:
    try:
        completed = subprocess.run(
            ["netstat", "-e"],
            text=True,
            capture_output=True,
            timeout=3,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        if line.strip().lower().startswith("bytes"):
            numbers = [int(item) for item in re.findall(r"\d+", line)]
            if len(numbers) >= 2:
                return numbers[0], numbers[1]
    return None


def curl_path() -> str:
    return "curl.exe" if sys.platform == "win32" else "curl"


def curl_through_socks(
    socks_host: str,
    socks_port: int,
    url: str = "http://connectivitycheck.gstatic.com/generate_204",
    timeout: int = 15,
) -> PingResult:
    endpoint = format_host_port(socks_host, socks_port)
    command = [
        curl_path(),
        "-sS",
        "-o",
        os.devnull,
        "--connect-timeout",
        str(timeout),
        "--max-time",
        str(timeout),
        "--socks5-hostname",
        endpoint,
        "-w",
        "%{http_code} %{time_total}",
        url,
    ]
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout + 2,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        return PingResult(error="curl missing")
    except subprocess.TimeoutExpired:
        return PingResult(error="timeout")

    fields = completed.stdout.strip().split()
    if completed.returncode == 0 and len(fields) == 2 and fields[0] == "204":
        try:
            millis = max(1, round(float(fields[1]) * 1000))
            return PingResult(millis=millis)
        except ValueError:
            pass
    http_code = fields[0] if fields else None
    return PingResult(error=short_curl_error(completed.stderr, completed.returncode, http_code))


def fetch_egress(socks_host: str, socks_port: int, timeout: int = 15) -> tuple[str, str | None]:
    endpoint = format_host_port(socks_host, socks_port)
    first_error: str | None = None
    for url in ("https://ipinfo.io/json", "http://ip-api.com/json"):
        command = [
            curl_path(),
            "-sS",
            "--connect-timeout",
            str(timeout),
            "--max-time",
            str(timeout),
            "--socks5-hostname",
            endpoint,
            url,
        ]
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout + 2,
            creationflags=CREATE_NO_WINDOW,
        )
        if completed.returncode != 0:
            first_error = first_error or short_curl_error(completed.stderr, completed.returncode)
            continue
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError:
            first_error = first_error or "IP lookup decode failed"
            continue
        ip = str(data.get("ip") or data.get("query") or "").strip()
        country = str(data.get("country") or data.get("countryCode") or "").strip() or None
        if ip:
            return ip, country
        first_error = first_error or "No IP returned."
    raise RuntimeError(first_error or "IP lookup through proxy failed.")


def format_host_port(host: str, port: int) -> str:
    text = (host or "").strip()
    if ":" in text and text.count(".") != 3 and not text.startswith("["):
        return f"[{text}]:{port}"
    return f"{text}:{port}"


def short_curl_error(stderr: str, exit_code: int, http_code: str | None = None) -> str:
    message = (stderr or "").strip()
    lowered = message.lower()
    if "timeout" in lowered or exit_code == 28:
        return "timeout"
    if "refused" in lowered or exit_code == 7:
        return "refused"
    if "socks" in lowered:
        return "proxy"
    if http_code and http_code not in ("000", "204"):
        return f"http {http_code}"
    if http_code == "000":
        return "no response"
    return "failed" if not message else message[:48]


def terminate_process_tree(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=4)
    except subprocess.TimeoutExpired:
        process.kill()


def env_with_python_path(paths: Iterable[Path]) -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    additions = os.pathsep.join(str(path) for path in paths)
    env["PYTHONPATH"] = additions + (os.pathsep + existing if existing else "")
    return env
