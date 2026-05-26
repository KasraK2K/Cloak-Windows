from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from models import AppSettings, ConfigStore, ListenerProjectConfig, PingResult, Profile
from system_windows import (
    CREATE_NO_WINDOW,
    WindowsSystemProxy,
    curl_through_socks,
    env_with_python_path,
    fetch_egress,
    first_available_port,
    is_admin,
    read_netstat_bytes,
    terminate_process_tree,
    wait_for_tcp,
)
from xray_config import generate, generate_for_ports


ROOT_DIR = Path(__file__).resolve().parent
CORE_DIR = ROOT_DIR / "core"
LISTENER_MAIN = CORE_DIR / "main.py"
DEFAULT_XRAY = ROOT_DIR / "bin" / "xray.exe"


class CloakController:
    def __init__(
        self,
        store: ConfigStore,
        settings: AppSettings,
        listener_config: ListenerProjectConfig,
        emit: Callable[[str, str], None],
        state_changed: Callable[[], None],
    ) -> None:
        self.store = store
        self.settings = settings
        self.listener_config = listener_config
        self.emit = emit
        self.state_changed = state_changed
        self.system_proxy = WindowsSystemProxy(store.proxy_backup_file)
        self.listener_process: subprocess.Popen | None = None
        self.xray_process: subprocess.Popen | None = None
        self.status = "stopped"
        self.status_message = "Disconnected"
        self.started_at: float | None = None
        self.proxy_active = False
        self.lock = threading.RLock()
        self.baseline_bytes: tuple[int, int] | None = None
        self.last_bytes: tuple[int, int] | None = None
        self.last_sample_at: float | None = None
        self.download_bps = 0.0
        self.upload_bps = 0.0
        self.session_down = 0
        self.session_up = 0

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    def set_settings(self, settings: AppSettings, listener_config: ListenerProjectConfig) -> None:
        with self.lock:
            self.settings = settings
            self.listener_config = listener_config

    def xray_path(self) -> Path:
        configured = self.settings.xray_path.strip()
        return Path(configured) if configured else DEFAULT_XRAY

    def python_path(self) -> str:
        configured = self.settings.python_path.strip()
        if configured:
            return configured
        env_python = os.environ.get("CLOAK_LISTENER_PYTHON", "").strip()
        if env_python:
            return env_python
        workspace_venv = ROOT_DIR.parent / ".venv" / "Scripts" / "python.exe"
        if workspace_venv.exists():
            return str(workspace_venv)
        return sys.executable

    def start_async(self, profile: Profile, done: Callable[[bool, str], None]) -> None:
        threading.Thread(target=self._start_worker, args=(profile, done), daemon=True).start()

    def stop_async(self, done: Callable[[bool, str], None]) -> None:
        threading.Thread(target=self._stop_worker, args=(done,), daemon=True).start()

    def _start_worker(self, profile: Profile, done: Callable[[bool, str], None]) -> None:
        try:
            self.start(profile)
        except Exception as exc:
            self._set_status("error", str(exc))
            done(False, str(exc))
            return
        done(True, "Connected")

    def _stop_worker(self, done: Callable[[bool, str], None]) -> None:
        try:
            self.stop()
        except Exception as exc:
            self._set_status("error", str(exc))
            done(False, str(exc))
            return
        done(True, "Disconnected")

    def start(self, profile: Profile) -> None:
        with self.lock:
            if self.status in ("starting", "running"):
                return
            self._set_status("starting", "Connecting...")

        if self.settings.connection_mode == "tunnel":
            raise RuntimeError(
                "Windows tunnel mode needs WinTun plus tun2socks routing. "
                "Switch to Proxy mode for the implemented Windows port."
            )
        if not is_admin():
            raise RuntimeError("Run Cloak for Windows as Administrator so WinDivert can capture and inject packets.")
        xray = self.xray_path()
        if not xray.exists():
            raise RuntimeError(f"xray.exe was not found at {xray}. Put xray.exe in windows-app\\bin or choose it in Settings.")
        if not LISTENER_MAIN.exists():
            raise RuntimeError("The Python listener core is missing.")

        self._normalize_ports()
        self.store.save_settings(self.settings)
        self.store.save_listener(self.listener_config)

        try:
            self._start_listener()
            if not wait_for_tcp(self.listener_config.resolved_dial_host, self.listener_config.LISTEN_PORT, attempts=30):
                raise RuntimeError("Listener did not accept connections in time. Check Logs for the first traceback.")
            config_path = self.store.generated_xray_file
            config_path.write_bytes(generate(self.settings, profile, self.listener_config))
            self._start_xray(config_path, self.xray_process_label())
            time.sleep(0.45)
            if self.settings.use_system_proxy:
                self.system_proxy.enable(
                    self.settings.resolved_socks_host_for_local_client,
                    self.settings.listen_port,
                    self.settings.http_port,
                )
                self.proxy_active = True
            counters = read_netstat_bytes()
            self.baseline_bytes = counters
            self.last_bytes = counters
            self.last_sample_at = time.time() if counters else None
            self.started_at = time.time()
            self._set_status("running", "Connected")
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        with self.lock:
            if self.status == "stopping":
                return
            self._set_status("stopping", "Disconnecting...")
        if self.proxy_active:
            try:
                self.system_proxy.disable()
            except Exception as exc:
                self.emit("stderr", f"[proxy restore failed] {exc}\n")
            self.proxy_active = False
        terminate_process_tree(self.xray_process)
        terminate_process_tree(self.listener_process)
        self.xray_process = None
        self.listener_process = None
        self.started_at = None
        self.baseline_bytes = None
        self.last_bytes = None
        self.last_sample_at = None
        self.download_bps = 0
        self.upload_bps = 0
        self.session_down = 0
        self.session_up = 0
        self._set_status("stopped", "Disconnected")

    def sample_bandwidth(self) -> None:
        if self.status != "running":
            return
        counters = read_netstat_bytes()
        if counters is None:
            return
        now = time.time()
        if self.baseline_bytes is not None:
            self.session_down = max(0, counters[0] - self.baseline_bytes[0])
            self.session_up = max(0, counters[1] - self.baseline_bytes[1])
        if self.last_bytes is not None and self.last_sample_at is not None:
            dt = max(0.2, now - self.last_sample_at)
            self.download_bps = max(0, counters[0] - self.last_bytes[0]) / dt
            self.upload_bps = max(0, counters[1] - self.last_bytes[1]) / dt
        self.last_bytes = counters
        self.last_sample_at = now

    def refresh_egress_async(self, done: Callable[[bool, str, str | None], None]) -> None:
        threading.Thread(target=self._refresh_egress_worker, args=(done,), daemon=True).start()

    def _refresh_egress_worker(self, done: Callable[[bool, str, str | None], None]) -> None:
        try:
            ip, country = fetch_egress(
                self.settings.resolved_socks_host_for_local_client,
                self.settings.listen_port,
            )
            done(True, ip, country)
        except Exception as exc:
            done(False, str(exc), None)

    def ping_profiles_async(
        self,
        profiles: list[Profile],
        on_result: Callable[[str, PingResult], None],
        on_done: Callable[[], None],
        cancel_event: threading.Event,
    ) -> None:
        threading.Thread(
            target=self._ping_profiles_worker,
            args=(profiles, on_result, on_done, cancel_event),
            daemon=True,
        ).start()

    def _ping_profiles_worker(
        self,
        profiles: list[Profile],
        on_result: Callable[[str, PingResult], None],
        on_done: Callable[[], None],
        cancel_event: threading.Event,
    ) -> None:
        listener_started_here = False
        try:
            if self.listener_process is None or self.listener_process.poll() is not None:
                if not is_admin():
                    for profile in profiles:
                        on_result(profile.id, PingResult(error="admin required"))
                    return
                self._start_listener()
                listener_started_here = True
                if not wait_for_tcp(self.listener_config.resolved_dial_host, self.listener_config.LISTEN_PORT, attempts=30):
                    for profile in profiles:
                        on_result(profile.id, PingResult(error="listener"))
                    return

            for index, profile in enumerate(profiles):
                if cancel_event.is_set():
                    break
                if self.status == "running" and profile.id != self.settings.active_profile_id:
                    on_result(profile.id, PingResult(error="disconnect first"))
                    continue
                socks_port = first_available_port(32000 + index * 2, "127.0.0.1", range(32000, 65530))
                http_port = first_available_port(socks_port + 1, "127.0.0.1", range(socks_port + 1, 65531))
                result = self._ping_with_temp_xray(profile, socks_port, http_port)
                on_result(profile.id, result)
        finally:
            if listener_started_here and self.status != "running":
                terminate_process_tree(self.listener_process)
                self.listener_process = None
            on_done()

    def _ping_with_temp_xray(self, profile: Profile, socks_port: int, http_port: int) -> PingResult:
        xray = self.xray_path()
        if not xray.exists():
            return PingResult(error="xray missing")
        temp_path = self.store.app_dir / f"xray-ping-{profile.id}-{uuid.uuid4()}.json"
        process: subprocess.Popen | None = None
        try:
            temp_path.write_bytes(generate_for_ports(self.settings, profile, self.listener_config, socks_port, http_port))
            process = self._spawn_process(
                [str(xray), "run", "-c", str(temp_path)],
                cwd=xray.parent,
                label=f"xray ping {profile.name}",
            )
            time.sleep(1.0)
            return curl_through_socks("127.0.0.1", socks_port)
        except Exception as exc:
            return PingResult(error=str(exc)[:48])
        finally:
            terminate_process_tree(process)
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    def _normalize_ports(self) -> None:
        preferred_socks = self.settings.listen_port
        self.settings.listen_port = first_available_port(
            preferred_socks,
            self.settings.listen_host,
            range(2079, 21999),
        )
        wanted_http = self.settings.http_port if self.settings.http_port != self.settings.listen_port else self.settings.listen_port + 1000
        self.settings.http_port = first_available_port(
            wanted_http,
            self.settings.listen_host,
            range(3079, 31999),
        )

    def _start_listener(self) -> None:
        if not self.listener_config.CONNECT_IP.strip() or not self.listener_config.FAKE_SNI.strip():
            raise RuntimeError("Listener config needs CONNECT_IP and FAKE_SNI.")
        terminate_process_tree(self.listener_process)
        runtime_config = self.store.app_dir / "listener.runtime.json"
        runtime_config.write_text(
            __import__("json").dumps(asdict(self.listener_config), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        env = env_with_python_path([CORE_DIR])
        env["CLOAK_CONFIG"] = str(runtime_config)
        self.listener_process = self._spawn_process(
            [self.python_path(), "-u", str(LISTENER_MAIN)],
            cwd=CORE_DIR,
            env=env,
            label="listener",
        )
        time.sleep(0.4)
        if self.listener_process.poll() is not None:
            raise RuntimeError("Python listener exited immediately. Install requirements and check Logs.")

    def _start_xray(self, config_path: Path, label: str) -> None:
        terminate_process_tree(self.xray_process)
        xray = self.xray_path()
        self.xray_process = self._spawn_process(
            [str(xray), "run", "-c", str(config_path)],
            cwd=xray.parent,
            label=label,
        )
        time.sleep(0.35)
        if self.xray_process.poll() is not None:
            raise RuntimeError("xray exited immediately. Check Logs for config errors.")

    def _spawn_process(
        self,
        command: list[str],
        cwd: Path,
        label: str,
        env: dict[str, str] | None = None,
    ) -> subprocess.Popen:
        self.emit("system", f"[start {label}] {' '.join(command)}\n")
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env or os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        threading.Thread(target=self._read_process_output, args=(process, label), daemon=True).start()
        return process

    def _read_process_output(self, process: subprocess.Popen, label: str) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            self.emit("stdout", f"[{label}] {line}")
        code = process.wait()
        if code != 0:
            self.emit("stderr", f"[{label} exited with status {code}]\n")

    def xray_process_label(self) -> str:
        return "xray"

    def _set_status(self, status: str, message: str) -> None:
        self.status = status
        self.status_message = message
        self.state_changed()
