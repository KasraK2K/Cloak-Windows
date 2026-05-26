# Notice

This repository is an unofficial Windows port and derivative work of:

- Project: `g3ntrix/Cloak`
- URL: https://github.com/g3ntrix/Cloak
- Upstream license: GNU General Public License version 3.0
- Audited upstream commit used during this port: `e126e0b628ca4d156bcf2b02cece130cfdbfee72`

The upstream project itself is forked from `patterniha/SNI-Spoofing`, as shown by the upstream GitHub repository metadata.

## Copied and modified upstream code

The Python listener core under `windows-app/core/` is based on upstream Cloak listener code:

- `windows-app/core/main.py`
- `windows-app/core/fake_tcp.py`
- `windows-app/core/injecter.py`
- `windows-app/core/monitor_connection.py`
- `windows-app/core/utils/packet_templates.py`
- `windows-app/core/utils/network_tools.py`

`windows-app/core/utils/network_tools.py` includes a Windows compatibility modification: `fcntl` is imported only on non-Windows platforms so the listener can start on Windows.

The Windows GUI, controller, profile parser/exporter, setup scripts, tests, and documentation in this repository were created for this Windows port, while preserving the upstream GPL-3.0 licensing requirements for the combined work.

## Distribution notes

This repository is licensed as a whole under GPL-3.0. If you distribute modified versions, keep the GPL-3.0 license, preserve this notice, make the corresponding source code available, and clearly mark your changes.

Downloaded runtime artifacts such as `xray.exe`, `geoip.dat`, and `geosite.dat` are intentionally ignored by Git and are not part of this source repository. They are distributed by the Xray project under their own license terms.

