# Cloak for Windows

This workspace contains an unofficial Windows port of [g3ntrix/Cloak](https://github.com/g3ntrix/Cloak).

The upstream project is a macOS SwiftUI app around a Python SNI-spoofing listener and Xray. This port keeps the same core shape for Windows:

- Dashboard with connect/disconnect, active profile, routing mode, egress IP, uptime, and session traffic.
- Profile library with VLESS, Trojan, VMess, and Shadowsocks import.
- Real profile ping through temporary Xray SOCKS workers.
- Xray config generation matching the upstream builder.
- Python listener process control using the upstream SNI bridge, with a Windows import fix.
- Windows system proxy toggle through the current user's Internet Settings registry keys.
- Settings for Cloudflare listener JSON, local SOCKS/HTTP ports, LAN exposure, logs, and runtime paths.
- Logs view for listener and Xray output.

## Current Windows parity

Proxy mode is implemented. It starts the Python listener, starts Xray, and optionally points Windows proxy-aware applications at Cloak's local HTTP/SOCKS endpoints.

Tunnel mode is intentionally guarded for now. The macOS app uses NetworkExtension and a packet tunnel provider. A Windows equivalent needs a WinTun/tun2socks helper plus route and DNS lifecycle management. The UI shows the mode for parity, but connect will ask you to use Proxy mode until that Windows tunnel helper is added.

## Setup

From this folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1 -DownloadXray
```

Then start the app as Administrator:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_windows_app.ps1 -AsAdmin
```

You can also double-click `Start-Cloak-Windows.vbs` from the project root. It launches the same app with the required Administrator prompt without leaving a PowerShell window open.

`Start-Cloak-Windows.cmd` is kept as a visible-console fallback for troubleshooting.

Administrator rights are required because the SNI listener uses WinDivert through `pydivert` to capture and inject TCP packets.

If you do not use `-DownloadXray`, put `xray.exe` in `windows-app\bin\xray.exe`, or choose a custom path from Settings.

Do not double-click `windows-app\bin\xray.exe` directly. Xray is only the runtime engine; Cloak starts it with a generated config when you press Connect.

## Files

- `windows-app\cloak_windows.py` - Windows desktop client.
- `windows-app\controller.py` - listener/Xray lifecycle, ping, egress, bandwidth, proxy lifecycle.
- `windows-app\models.py` - settings, profile storage, and URL import parser.
- `windows-app\xray_config.py` - Xray JSON generator.
- `windows-app\system_windows.py` - Windows proxy, admin, curl, and network helpers.
- `windows-app\core\` - upstream Python SNI bridge with the Windows import fix.
- `scripts\setup_windows.ps1` - venv/dependency setup and optional Xray download.
- `scripts\start_windows_app.ps1` - starts the GUI, optionally elevated.

## License

This repository is a derivative work of `g3ntrix/Cloak` and is licensed as a whole under GPL-3.0, matching the upstream project. See [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md).

The downloaded Xray runtime files are not committed to this repository. If you redistribute binaries or release packages that include third-party runtime artifacts, include and comply with those projects' license terms as well.
