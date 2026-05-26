from __future__ import annotations

import base64
import json
from urllib.parse import quote, urlencode

from models import Profile


class ProfileExportError(ValueError):
    pass


def export_text(profiles: list[Profile]) -> str:
    return "\n".join(uri_for(profile) for profile in profiles) + "\n"


def uri_for(profile: Profile) -> str:
    if profile.kind == "vless":
        return _standard_uri(profile, "vless", profile.uuid)
    if profile.kind == "trojan":
        return _standard_uri(profile, "trojan", profile.password)
    if profile.kind == "vmess":
        return _vmess_uri(profile)
    if profile.kind == "shadowsocks":
        return _shadowsocks_uri(profile)
    raise ProfileExportError(f"{profile.kind} is not supported.")


def _standard_uri(profile: Profile, scheme: str, user: str) -> str:
    if not user.strip():
        raise ProfileExportError(f"{profile.name} is missing credentials.")
    if not profile.server.strip() or profile.server_port <= 0:
        raise ProfileExportError(f"{profile.name} is missing server details.")
    query = urlencode(_query_items(profile), doseq=False, safe="/,:")
    fragment = quote(profile.name, safe="")
    return f"{scheme}://{quote(user, safe='')}@{_host_for_uri(profile.server)}:{profile.server_port}?{query}#{fragment}"


def _query_items(profile: Profile) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    security = _security_value(profile)
    items.append(("security", security))
    if profile.tls.server_name:
        items.append(("sni", profile.tls.server_name))
    if profile.tls.allow_insecure:
        items.append(("allowInsecure", "1"))
    if profile.tls.fingerprint:
        items.append(("fp", profile.tls.fingerprint))
    if profile.tls.alpn:
        items.append(("alpn", ",".join(profile.tls.alpn)))
    if profile.tls.public_key:
        items.append(("pbk", profile.tls.public_key))
    if profile.tls.short_id:
        items.append(("sid", profile.tls.short_id))
    if profile.tls.spider_x:
        items.append(("spx", profile.tls.spider_x))
    items.append(("type", profile.transport.kind))
    if profile.transport.path:
        items.append(("path", profile.transport.path))
    if profile.transport.host:
        items.append(("host", profile.transport.host))
    if profile.transport.service_name:
        items.append(("serviceName", profile.transport.service_name))
    if profile.transport.authority:
        items.append(("authority", profile.transport.authority))
    if profile.transport.header_type:
        items.append(("headerType", profile.transport.header_type))
    if profile.transport.mode:
        items.append(("mode", profile.transport.mode))
    if profile.flow:
        items.append(("flow", profile.flow))
    if profile.packet_encoding:
        items.append(("packetEncoding", profile.packet_encoding))
    return items


def _security_value(profile: Profile) -> str:
    security = (profile.tls.security or "").strip()
    if security:
        return security
    if profile.kind == "trojan":
        return "tls"
    return "tls" if profile.tls.enabled else "none"


def _vmess_uri(profile: Profile) -> str:
    if not profile.uuid.strip():
        raise ProfileExportError(f"{profile.name} is missing UUID.")
    payload = {
        "v": "2",
        "ps": profile.name,
        "add": profile.server,
        "port": str(profile.server_port),
        "id": profile.uuid,
        "aid": "0",
        "scy": "auto",
        "net": profile.transport.kind,
        "type": profile.transport.header_type or "none",
        "host": profile.transport.host,
        "path": profile.transport.path,
        "tls": "" if _security_value(profile) == "none" else _security_value(profile),
        "sni": profile.tls.server_name,
        "alpn": ",".join(profile.tls.alpn),
        "fp": profile.tls.fingerprint,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "vmess://" + base64.b64encode(raw).decode("ascii")


def _shadowsocks_uri(profile: Profile) -> str:
    if not profile.method.strip() or not profile.password.strip():
        raise ProfileExportError(f"{profile.name} is missing Shadowsocks credentials.")
    if not profile.server.strip() or profile.server_port <= 0:
        raise ProfileExportError(f"{profile.name} is missing server details.")
    credentials = base64.b64encode(f"{profile.method}:{profile.password}".encode("utf-8")).decode("ascii")
    return f"ss://{quote(credentials, safe='=')}@{_host_for_uri(profile.server)}:{profile.server_port}#{quote(profile.name, safe='')}"


def _host_for_uri(host: str) -> str:
    text = host.strip()
    if ":" in text and not text.startswith("[") and text.count(".") != 3:
        return f"[{text}]"
    return text
