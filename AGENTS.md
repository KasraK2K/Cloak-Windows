# AGENTS.md

This file provides guidance to Codex (openai.com/codex) when working with code in this repository.

## What this project is

A Windows port of [g3ntrix/Cloak](https://github.com/g3ntrix/Cloak) — a macOS SNI-spoofing proxy client. The upstream project is mirrored under `upstream/`. The Windows implementation lives entirely in `windows-app/`.

The app starts a Python SNI listener (the "core") that intercepts TCP connections via WinDivert, injects a spoofed TLS ClientHello to bypass DPI, then forwards traffic to Xray-core which handles VLESS/Trojan/VMess/Shadowsocks proxy protocols.

**Administrator rights are required** at runtime because WinDivert (via `pydivert`) captures and injects raw packets at the kernel level.

**Tunnel mode is not implemented.** The UI shows it for parity with macOS, but connecting in tunnel mode raises a `RuntimeError`. Proxy mode is fully working.

## Setup and running

```powershell
# First-time setup — creates .venv, installs requirements, downloads xray.exe
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1 -DownloadXray

# Start the GUI (elevated)
powershell -ExecutionPolicy Bypass -File .\scripts\start_windows_app.ps1 -AsAdmin

# Or double-click the launcher (no visible console window)
Start-Cloak-Windows.vbs
```

To run directly from an already-elevated shell:
```powershell
.\.venv\Scripts\python.exe windows-app\cloak_windows.py
```

## Build a release package

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_package.ps1 -Version 0.1.0-alpha
# Output: dist\Cloak-Windows-v0.1.0-alpha.zip
```

The build script uses PyInstaller to produce:
- `Cloak-Windows.exe` (GUI, `--windowed --onedir`) from `cloak_windows.py`
- `bin\cloak-listener.exe` (console, `--onefile`) from `core\main.py`

Both are bundled with `xray.exe`, `geoip.dat`, and `geosite.dat` from `windows-app\bin\`.

## Architecture

### Windows app (`windows-app/`)

```
cloak_windows.py   — tkinter GUI (CloakWindowsApp). Single window, tab-per-view.
                     Communicates with controller via a thread-safe queue; drains
                     the queue on a 120 ms Tkinter after() timer.
controller.py      — CloakController. Owns the two subprocesses (listener + xray)
                     and all background threads for start/stop/ping/egress/bandwidth.
models.py          — Pure data: Profile, AppSettings, ListenerProjectConfig,
                     PingResult, ConfigStore. All persistence is JSON via ConfigStore.
xray_config.py     — Generates the Xray JSON config that is written to disk before
                     xray.exe is started. Handles all four protocol kinds and all
                     transport/security combinations.
system_windows.py  — Windows-specific helpers: winreg proxy toggle, WinINet refresh,
                     admin check, ShellExecute elevation, port probing, netstat
                     bandwidth sampling, curl-based ping/egress lookup.
profile_exporter.py — Serialises profiles back to their original URI schemes.
core/              — Upstream SNI bridge (asyncio). main.py is the entry point.
                     Reads config from CLOAK_CONFIG env var (a JSON file written by
                     the controller before launch). Uses pydivert + scapy for packet
                     capture/injection.
```

### Startup sequence (Proxy mode)

1. `CloakController.start()` validates settings, normalises ports, writes `listener.runtime.json`, then spawns `core/main.py` (or `bin/cloak-listener.exe` if it exists).
2. Polls `wait_for_tcp` until the listener is accepting on its LISTEN_PORT.
3. Generates an Xray JSON config via `xray_config.generate()`, writes it to `app_dir/xray.generated.json`, spawns `xray.exe`.
4. Optionally enables the Windows system proxy via `WindowsSystemProxy.enable()` (writes a backup to `app_dir/windows-proxy-backup.json` before changing registry keys).
5. On disconnect, proxy is restored from backup, both processes are terminated, counters reset.

### Ping flow

`CloakController.ping_profiles_async()` starts the listener if not already running, then for each profile spawns a temporary Xray instance on ephemeral ports and measures round-trip time via `curl_through_socks` to `http://connectivitycheck.gstatic.com/generate_204`.

### Persistent state

All state is stored in `%APPDATA%\CloakWindows\` (falls back to `%LOCALAPPDATA%\CloakWindows\` then `.\data\`):
- `settings.json` — `AppSettings`
- `profiles.json` — `list[Profile]`
- `listener-project.json` — `ListenerProjectConfig` (the Cloudflare/SNI bridge config)
- `ping-results.json` — last ping results per profile ID
- `windows-proxy-backup.json` — pre-Cloak proxy registry snapshot (deleted on clean disconnect)
- `xray.generated.json` — ephemeral, written fresh each connect

## Key constraints

- **pydivert requires WinDivert**, which needs Administrator. Do not remove the admin guard in `controller.py:start()`.
- **`core/` is upstream code.** Prefer minimal changes there. The only Windows-specific fix already applied is the import path correction in `core/__init__.py`.
- The listener is launched with `PYTHONPATH` pointing at `core/` and `CLOAK_CONFIG` pointing at the runtime JSON file — the `core/main.py` reads config exclusively from `CLOAK_CONFIG`, not from argv.
- `xray.exe` must never be committed to the repo; it is downloaded by `setup_windows.ps1 -DownloadXray` and is in `.gitignore`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
