from __future__ import annotations

import base64
import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "windows-app"
sys.path.insert(0, str(APP))

from models import AppSettings, ListenerProjectConfig, import_many, parse_profile_url
from profile_exporter import export_text
from system_windows import fetch_egress, format_host_port, wait_for_tcp
from xray_config import generate


class ProfileParserTests(unittest.TestCase):
    def test_trojan_websocket_profile_matches_upstream_fields(self) -> None:
        raw = "trojan://humanity@127.0.0.1:40443?security=tls&sni=www.ignitelimit.com&type=ws&path=/assignment&host=www.ignitelimit.com#Amirstar"
        profiles, errors = import_many(raw)
        self.assertEqual(errors, [])
        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertEqual(profile.name, "Amirstar")
        self.assertEqual(profile.kind, "trojan")
        self.assertEqual(profile.password, "humanity")
        self.assertEqual(profile.transport.kind, "ws")
        self.assertEqual(profile.transport.path, "/assignment")
        self.assertEqual(profile.transport.host, "www.ignitelimit.com")
        self.assertEqual(profile.tls.server_name, "www.ignitelimit.com")

    def test_vless_profile_generates_xray_bridge_config(self) -> None:
        raw = "vless://11111111-1111-1111-1111-111111111111@example.com:443?security=tls&sni=example.com&type=ws&path=/ws&host=example.com#sample-vless"
        profile = parse_profile_url(raw)
        data = json.loads(generate(AppSettings(), profile, ListenerProjectConfig()))
        self.assertEqual(data["inbounds"][0]["protocol"], "socks")
        self.assertEqual(data["inbounds"][1]["protocol"], "http")
        outbound = data["outbounds"][0]
        self.assertEqual(outbound["protocol"], "vless")
        self.assertEqual(outbound["settings"]["vnext"][0]["address"], "127.0.0.1")
        self.assertEqual(outbound["settings"]["vnext"][0]["port"], 40443)
        self.assertEqual(outbound["streamSettings"]["network"], "ws")

    def test_vmess_profile_round_trips_through_exporter(self) -> None:
        payload = {
            "v": "2",
            "ps": "vmess sample",
            "add": "edge.example.com",
            "port": "443",
            "id": "22222222-2222-2222-2222-222222222222",
            "net": "ws",
            "host": "cdn.example.com",
            "path": "/socket",
            "tls": "tls",
            "sni": "cdn.example.com",
        }
        raw = "vmess://" + base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
        profile = parse_profile_url(raw)
        exported = export_text([profile])
        reparsed = parse_profile_url(exported.strip())
        self.assertEqual(reparsed.kind, "vmess")
        self.assertEqual(reparsed.uuid, profile.uuid)
        self.assertEqual(reparsed.transport.kind, "ws")
        self.assertEqual(reparsed.tls.server_name, "cdn.example.com")

    def test_shadowsocks_profile_export_reconstructs_uri_without_raw_url(self) -> None:
        profile = parse_profile_url("ss://YWVzLTI1Ni1nY206cGFzcw@example.com:8388#ss-sample")
        profile.raw_url = ""
        exported = export_text([profile]).strip()
        reparsed = parse_profile_url(exported)
        self.assertEqual(reparsed.kind, "shadowsocks")
        self.assertEqual(reparsed.method, "aes-256-gcm")
        self.assertEqual(reparsed.password, "pass")
        self.assertEqual(reparsed.server, "example.com")


class SystemHelperTests(unittest.TestCase):
    def test_wait_for_tcp_allows_delayed_listener_start(self) -> None:
        ready_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ready_socket.bind(("127.0.0.1", 0))
        port = ready_socket.getsockname()[1]
        ready_socket.close()

        def server() -> None:
            time.sleep(0.45)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", port))
                listener.listen(1)
                conn, _addr = listener.accept()
                conn.close()

        thread = threading.Thread(target=server, daemon=True)
        thread.start()
        self.assertTrue(wait_for_tcp("127.0.0.1", port, attempts=5, timeout=0.1))

    def test_format_host_port_wraps_ipv6_for_curl(self) -> None:
        self.assertEqual(format_host_port("::1", 2080), "[::1]:2080")
        self.assertEqual(format_host_port("127.0.0.1", 2080), "127.0.0.1:2080")

    def test_fetch_egress_falls_back_to_ip_api_payload(self) -> None:
        failed = mock.Mock(returncode=35, stderr="tls fail", stdout="")
        succeeded = mock.Mock(returncode=0, stderr="", stdout='{"query":"203.0.113.10","countryCode":"NL"}')
        with mock.patch("system_windows.subprocess.run", side_effect=[failed, succeeded]):
            self.assertEqual(fetch_egress("127.0.0.1", 2080), ("203.0.113.10", "NL"))


if __name__ == "__main__":
    unittest.main()
