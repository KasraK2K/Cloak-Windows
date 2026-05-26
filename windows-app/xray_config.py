from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from models import AppSettings, ListenerProjectConfig, Profile


class XrayConfigError(ValueError):
    pass


def generate(settings: AppSettings, profile: Profile, bridge: ListenerProjectConfig) -> bytes:
    dial_host = bridge.resolved_dial_host
    dial_port = bridge.LISTEN_PORT
    if dial_port <= 0 or dial_port > 65535:
        raise XrayConfigError("Listener config has an invalid LISTEN_PORT.")

    outbound: dict[str, Any] = {
        "tag": "proxy",
        "streamSettings": _stream_settings(profile),
    }

    if profile.kind in ("vless", "vmess"):
        if not profile.uuid.strip():
            raise XrayConfigError(f"{profile.display_kind} profile is missing a UUID.")
        outbound["protocol"] = "vless" if profile.kind == "vless" else "vmess"
        user: dict[str, Any] = {"id": profile.uuid, "encryption": "none"}
        if profile.kind == "vmess":
            user = {"id": profile.uuid, "security": "auto"}
        if profile.flow:
            user["flow"] = profile.flow
        if profile.kind == "vless" and profile.packet_encoding:
            user["packetEncoding"] = profile.packet_encoding
        outbound["settings"] = {
            "vnext": [
                {
                    "address": dial_host,
                    "port": dial_port,
                    "users": [user],
                }
            ]
        }
    elif profile.kind == "trojan":
        if not profile.password.strip():
            raise XrayConfigError("Trojan profile is missing a password.")
        outbound["protocol"] = "trojan"
        outbound["settings"] = {
            "servers": [
                {
                    "address": dial_host,
                    "port": dial_port,
                    "password": profile.password,
                }
            ]
        }
    elif profile.kind == "shadowsocks":
        if not profile.method.strip() or not profile.password.strip():
            raise XrayConfigError("Shadowsocks profile is missing method or password.")
        outbound["protocol"] = "shadowsocks"
        outbound["settings"] = {
            "servers": [
                {
                    "address": dial_host,
                    "port": dial_port,
                    "method": profile.method,
                    "password": profile.password,
                }
            ]
        }
        outbound.pop("streamSettings", None)
    else:
        raise XrayConfigError(f"Unsupported profile kind: {profile.kind}.")

    root: dict[str, Any] = {
        "log": {"loglevel": _xray_log_level(settings.log_level)},
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": settings.listen_host,
                "port": settings.listen_port,
                "protocol": "socks",
                "settings": {"udp": True},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            },
            {
                "tag": "http-in",
                "listen": settings.listen_host,
                "port": settings.http_port,
                "protocol": "http",
                "settings": {"allowTransparent": False},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            },
        ],
        "outbounds": [
            outbound,
            {"protocol": "freedom", "tag": "direct", "settings": {}},
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["socks-in", "http-in"],
                    "outboundTag": "proxy",
                }
            ],
        },
    }
    return json.dumps(root, indent=2, sort_keys=True).encode("utf-8")


def generate_for_ports(
    base_settings: AppSettings,
    profile: Profile,
    bridge: ListenerProjectConfig,
    socks_port: int,
    http_port: int,
) -> bytes:
    probe_settings = replace(
        base_settings,
        listen_host="127.0.0.1",
        listen_port=socks_port,
        http_port=http_port,
        use_system_proxy=False,
        connection_mode="proxy",
    )
    return generate(probe_settings, profile, bridge)


def _xray_log_level(level: str) -> str:
    normalized = (level or "").lower()
    if normalized in ("trace", "debug"):
        return "debug"
    if normalized == "info":
        return "info"
    if normalized == "warn":
        return "warning"
    return "error"


def _stream_settings(profile: Profile) -> dict[str, Any]:
    security = _security_mode(profile)
    settings: dict[str, Any] = {}
    transport = profile.transport

    if transport.kind == "tcp":
        settings["network"] = "tcp"
    elif transport.kind == "ws":
        ws: dict[str, Any] = {}
        if transport.path:
            ws["path"] = transport.path
        if transport.host:
            ws["headers"] = {"Host": transport.host}
        settings["network"] = "ws"
        settings["wsSettings"] = ws
    elif transport.kind == "grpc":
        grpc: dict[str, Any] = {
            "serviceName": transport.service_name,
            "multiMode": transport.mode == "multi",
        }
        if transport.authority:
            grpc["authority"] = transport.authority
        settings["network"] = "grpc"
        settings["grpcSettings"] = grpc
    elif transport.kind == "http":
        http: dict[str, Any] = {}
        if transport.path:
            http["path"] = transport.path
        if transport.host:
            http["host"] = [transport.host]
        settings["network"] = "http"
        settings["httpSettings"] = http
    elif transport.kind == "httpupgrade":
        http_upgrade: dict[str, Any] = {}
        if transport.path:
            http_upgrade["path"] = transport.path
        if transport.host:
            http_upgrade["host"] = transport.host
        settings["network"] = "httpupgrade"
        settings["httpupgradeSettings"] = http_upgrade
    elif transport.kind in ("xhttp", "splithttp"):
        xhttp: dict[str, Any] = {}
        if transport.path:
            xhttp["path"] = transport.path
        if transport.host:
            xhttp["host"] = transport.host
        if transport.mode:
            xhttp["mode"] = transport.mode
        settings["network"] = transport.kind
        settings["xhttpSettings"] = xhttp
    else:
        settings["network"] = "tcp"

    if security == "tls":
        settings["security"] = "tls"
        settings["tlsSettings"] = _tls_settings(profile)
    elif security == "reality":
        settings["security"] = "reality"
        settings["realitySettings"] = _reality_settings(profile)
    elif security == "none":
        settings["security"] = "none"
    else:
        raise XrayConfigError(f"Unsupported security mode: {security}.")

    return settings


def _security_mode(profile: Profile) -> str:
    explicit = (profile.tls.security or "").strip().lower()
    if explicit:
        return explicit
    if profile.kind == "trojan":
        return "tls"
    return "tls" if profile.tls.enabled else "none"


def _tls_settings(profile: Profile) -> dict[str, Any]:
    server_name = profile.tls.server_name or profile.transport.host or profile.server
    settings: dict[str, Any] = {"allowInsecure": profile.tls.allow_insecure}
    if server_name:
        settings["serverName"] = server_name
    if profile.tls.alpn:
        settings["alpn"] = profile.tls.alpn
    if profile.tls.fingerprint:
        settings["fingerprint"] = profile.tls.fingerprint
    return settings


def _reality_settings(profile: Profile) -> dict[str, Any]:
    settings = _tls_settings(profile)
    if profile.tls.public_key:
        settings["publicKey"] = profile.tls.public_key
    if profile.tls.short_id:
        settings["shortId"] = profile.tls.short_id
    if profile.tls.spider_x:
        settings["spiderX"] = profile.tls.spider_x
    return settings
