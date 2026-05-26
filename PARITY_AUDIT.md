# Cloak Windows parity audit

Audit target: upstream `g3ntrix/Cloak` at commit `e126e0b628ca4d156bcf2b02cece130cfdbfee72`.

This is an unofficial Windows port and derivative work. See `NOTICE.md` for attribution and distribution notes.

## Implemented and checked

- Python SNI listener core is copied from upstream. `main.py`, `fake_tcp.py`, and `injecter.py` match upstream exactly.
- `network_tools.py` has one intentional Windows fix: `fcntl` is imported only on non-Windows platforms.
- VLESS, Trojan, VMess, and Shadowsocks import logic mirrors upstream profile parsing.
- Xray config generation mirrors upstream: SOCKS/HTTP inbounds, bridge dial rewrite, TLS/Reality transport settings, and routing.
- Profile export reconstructs share links instead of depending on original raw URL text.
- Two upstream bundled seed profiles are added when missing.
- Egress lookup uses the same primary `ipinfo.io` path with an `ip-api.com` fallback.
- Ping behavior preserves the upstream connected-state rule: inactive profiles report `disconnect first` while connected.
- Windows system proxy mode is implemented with current-user Internet Settings registry backup and restore.
- Setup downloads Xray and installs Python dependencies into `.venv`; GUI launch uses system Python while listener subprocesses use the venv Python.

## Verified locally

- Python compile check for `windows-app` and `tests`.
- Unit tests for profile parsing, export round-trips, Xray config shape, egress fallback, IPv6 SOCKS formatting, and delayed listener readiness.
- `pydivert` and `scapy` imports from `.venv`.
- `xray.exe version` from `windows-app\bin`.
- GUI startup through `scripts\start_windows_app.ps1`; a visible `Cloak for Windows` window stayed alive past startup.

## Not fully equivalent yet

- Full tunnel mode is not implemented on Windows. The macOS app uses a root-owned `utun` plus `tun2socks` helper. Windows needs an equivalent WinTun/tun2socks route and DNS lifecycle before Tunnel mode can honestly be considered parity.
- The macOS menu-bar popover has no Windows system-tray equivalent yet. The main dashboard controls are present.

For travel-critical use today, use Proxy mode and verify with your real profiles before departure. Full-device routing still needs the Windows tunnel helper work above.
