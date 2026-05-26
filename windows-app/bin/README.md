# Runtime binaries

Place `xray.exe` in this folder, or choose a custom `xray.exe` path from the app's Settings tab.

The setup script can download the latest Windows x64 Xray release for you:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1 -DownloadXray
```

`xray.exe`, `geoip.dat`, and `geosite.dat` are not committed here. They are distributed by the Xray project under its own license.

