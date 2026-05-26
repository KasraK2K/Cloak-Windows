from __future__ import annotations

import base64
import json
import os
import re
import uuid as uuidlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse


APP_NAME = "CloakWindows"
APP_DIR = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / APP_NAME
LOCAL_APP_DIR = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / APP_NAME
SUPPORTED_SCHEMES = ("vless://", "vmess://", "trojan://", "ss://", "shadowsocks://")
URI_RE = re.compile(r"(?i)\b(?:vless|vmess|trojan|ss|shadowsocks)://\S+")


def _new_id() -> str:
    return str(uuidlib.uuid4())


@dataclass
class TLSSettings:
    enabled: bool = True
    security: str | None = None
    server_name: str = ""
    allow_insecure: bool = False
    fingerprint: str = "chrome"
    alpn: list[str] = field(default_factory=list)
    public_key: str | None = None
    short_id: str | None = None
    spider_x: str | None = None
    enable_spoof: bool = False
    fake_sni: str = ""
    spoof_method: str = "wrong-sequence"


@dataclass
class TransportSettings:
    kind: str = "tcp"
    path: str = ""
    host: str = ""
    service_name: str = ""
    authority: str | None = None
    header_type: str | None = None
    mode: str | None = None


@dataclass
class Profile:
    name: str
    kind: str = "vless"
    server: str = ""
    server_port: int = 443
    id: str = field(default_factory=_new_id)
    vless_url_host: str | None = None
    uuid: str = ""
    password: str = ""
    method: str = ""
    flow: str = ""
    packet_encoding: str | None = None
    tls: TLSSettings = field(default_factory=TLSSettings)
    transport: TransportSettings = field(default_factory=TransportSettings)
    raw_url: str = ""

    @property
    def display_kind(self) -> str:
        return {
            "vless": "VLESS",
            "vmess": "VMess",
            "trojan": "Trojan",
            "shadowsocks": "Shadowsocks",
        }.get(self.kind, self.kind.upper())

    @property
    def subtitle(self) -> str:
        return self.tls.server_name or self.transport.host or f"{self.server}:{self.server_port}"

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Profile":
        tls = TLSSettings(**data.get("tls", {}))
        transport = TransportSettings(**data.get("transport", {}))
        payload = dict(data)
        payload["tls"] = tls
        payload["transport"] = transport
        return Profile(**payload)


@dataclass
class AppSettings:
    listen_host: str = "127.0.0.1"
    listen_port: int = 2080
    http_port: int = 3080
    use_system_proxy: bool = True
    connection_mode: str = "proxy"
    active_profile_id: str | None = None
    log_level: str = "warn"
    logs_enabled: bool = False
    appearance_mode: str = "system"
    xray_path: str = ""
    python_path: str = ""

    @property
    def exposes_to_lan(self) -> bool:
        host = self.listen_host.strip().lower()
        return host in ("0.0.0.0", "*")

    def set_exposes_to_lan(self, enabled: bool) -> None:
        self.listen_host = "0.0.0.0" if enabled else "127.0.0.1"

    @property
    def resolved_socks_host_for_local_client(self) -> str:
        host = self.listen_host.strip().lower()
        if not host or host in ("0.0.0.0", "*"):
            return "127.0.0.1"
        if host == "::":
            return "::1"
        return self.listen_host.strip()


@dataclass
class ListenerProjectConfig:
    LISTEN_HOST: str = "0.0.0.0"
    LISTEN_PORT: int = 40443
    CONNECT_IP: str = "104.19.229.21"
    CONNECT_PORT: int = 443
    FAKE_SNI: str = "hcaptcha.com"

    @property
    def resolved_dial_host(self) -> str:
        host = self.LISTEN_HOST.strip().lower()
        if not host or host in ("0.0.0.0", "*"):
            return "127.0.0.1"
        if host == "::":
            return "::1"
        return self.LISTEN_HOST.strip()

    def encode_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @staticmethod
    def default_json() -> str:
        return ListenerProjectConfig().encode_json()

    @staticmethod
    def decode(raw: str) -> "ListenerProjectConfig":
        data = json.loads(raw)
        return ListenerProjectConfig(**data)


@dataclass
class PingResult:
    millis: int | None = None
    error: str | None = None


class ConfigStore:
    def __init__(self, app_dir: Path = APP_DIR) -> None:
        self.app_dir = self._usable_app_dir(app_dir)
        self.app_dir.mkdir(parents=True, exist_ok=True)

    def _usable_app_dir(self, preferred: Path) -> Path:
        candidates = [
            preferred,
            LOCAL_APP_DIR,
            Path.cwd() / "data",
        ]
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                probe = candidate / ".write-test"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
                return candidate
            except OSError:
                continue
        raise PermissionError("Cloak for Windows could not find a writable app-data folder.")

    @property
    def settings_file(self) -> Path:
        return self.app_dir / "settings.json"

    @property
    def profiles_file(self) -> Path:
        return self.app_dir / "profiles.json"

    @property
    def listener_file(self) -> Path:
        return self.app_dir / "listener-project.json"

    @property
    def generated_xray_file(self) -> Path:
        return self.app_dir / "xray.generated.json"

    @property
    def ping_results_file(self) -> Path:
        return self.app_dir / "ping-results.json"

    @property
    def proxy_backup_file(self) -> Path:
        return self.app_dir / "windows-proxy-backup.json"

    def load_settings(self) -> AppSettings:
        if not self.settings_file.exists():
            return AppSettings()
        try:
            return AppSettings(**json.loads(self.settings_file.read_text(encoding="utf-8")))
        except Exception:
            return AppSettings()

    def save_settings(self, settings: AppSettings) -> None:
        self.settings_file.write_text(json.dumps(asdict(settings), indent=2, sort_keys=True), encoding="utf-8")

    def load_profiles(self) -> list[Profile]:
        if not self.profiles_file.exists():
            return []
        try:
            data = json.loads(self.profiles_file.read_text(encoding="utf-8"))
            return [Profile.from_dict(item) for item in data]
        except Exception:
            return []

    def save_profiles(self, profiles: list[Profile]) -> None:
        self.profiles_file.write_text(json.dumps([asdict(p) for p in profiles], indent=2, sort_keys=True), encoding="utf-8")

    def load_listener(self) -> ListenerProjectConfig:
        if not self.listener_file.exists():
            return ListenerProjectConfig()
        try:
            return ListenerProjectConfig(**json.loads(self.listener_file.read_text(encoding="utf-8")))
        except Exception:
            return ListenerProjectConfig()

    def save_listener(self, config: ListenerProjectConfig) -> None:
        self.listener_file.write_text(json.dumps(asdict(config), indent=2, sort_keys=True), encoding="utf-8")

    def load_ping_results(self) -> dict[str, PingResult]:
        if not self.ping_results_file.exists():
            return {}
        try:
            data = json.loads(self.ping_results_file.read_text(encoding="utf-8"))
            return {key: PingResult(**value) for key, value in data.items()}
        except Exception:
            return {}

    def save_ping_results(self, results: dict[str, PingResult]) -> None:
        payload = {key: asdict(value) for key, value in results.items()}
        self.ping_results_file.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


class ProfileImportError(ValueError):
    pass


def count_candidates(raw: str) -> int:
    return len(all_proxy_urls(raw))


def all_proxy_urls(raw: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith(SUPPORTED_SCHEMES):
            uri = stripped
            if uri not in seen:
                seen.add(uri)
                out.append(uri)
            continue
        for match in URI_RE.finditer(stripped):
            uri = match.group(0).rstrip("),;]")
            if uri not in seen:
                seen.add(uri)
                out.append(uri)
    if not out:
        for match in URI_RE.finditer(raw):
            uri = match.group(0).rstrip("),;]")
            if uri not in seen:
                seen.add(uri)
                out.append(uri)
    return out


def import_many(raw: str) -> tuple[list[Profile], list[str]]:
    profiles: list[Profile] = []
    errors: list[str] = []
    for index, uri in enumerate(all_proxy_urls(raw), start=1):
        try:
            profiles.append(parse_profile_url(uri))
        except Exception as exc:
            label = uri[:40] + "..." if len(uri) > 40 else uri
            errors.append(f"Line {index} ({label}): {exc}")
    return profiles, errors


def import_one(raw: str) -> Profile:
    trimmed = raw.strip()
    if not trimmed:
        raise ProfileImportError("Paste a vless://, vmess://, trojan://, or ss:// link.")
    urls = all_proxy_urls(trimmed)
    if not urls:
        raise ProfileImportError("No supported profile URL found.")
    profile = parse_profile_url(urls[0])
    blob = first_json_block(trimmed)
    if blob:
        merge_spoof_json(profile, blob)
    return profile


def first_json_block(raw: str) -> str | None:
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(raw)):
        if raw[index] == "{":
            depth += 1
        elif raw[index] == "}":
            depth -= 1
            if depth == 0:
                return raw[start : index + 1]
    return None


def merge_spoof_json(profile: Profile, raw_json: str) -> None:
    data = json.loads(raw_json)
    fake_sni = str(data.get("FAKE_SNI") or "").strip().rstrip(".")
    if fake_sni:
        profile.tls.enabled = True
        profile.tls.enable_spoof = True
        profile.tls.fake_sni = fake_sni
    connect_host = str(data.get("CONNECT_HOST") or "").strip().rstrip(".")
    if connect_host:
        profile.server = connect_host
    connect_ip = str(data.get("CONNECT_IP") or "").strip()
    if connect_ip:
        if not profile.tls.server_name and profile.server:
            profile.tls.server_name = profile.server
        profile.server = connect_ip
    connect_port = data.get("CONNECT_PORT")
    if isinstance(connect_port, int) and connect_port > 0:
        profile.server_port = connect_port


def parse_profile_url(raw: str) -> Profile:
    trimmed = raw.strip()
    parsed = urlparse(trimmed)
    scheme = (parsed.scheme or "").lower()
    if scheme == "vless":
        return _parse_standard(trimmed, parsed, "vless", "none")
    if scheme == "trojan":
        return _parse_standard(trimmed, parsed, "trojan", "tls")
    if scheme == "vmess":
        return _parse_vmess(trimmed)
    if scheme in ("ss", "shadowsocks"):
        return _parse_shadowsocks(trimmed, scheme)
    raise ProfileImportError(f"{scheme or '?'}:// is not supported.")


def _parse_standard(raw: str, parsed, kind: str, default_security: str) -> Profile:
    host = normalize_hostname(parsed.hostname or "")
    if not host:
        raise ProfileImportError("Missing host in URL.")
    if parsed.port is None:
        raise ProfileImportError("Missing port in URL.")
    user = unquote(parsed.username or "")
    if not user:
        raise ProfileImportError("Missing password." if kind == "trojan" else "Missing UUID.")
    query = _query_map(parsed.query)
    name = unquote(parsed.fragment or "").strip() or host
    profile = Profile(name=name, kind=kind, server=host, server_port=parsed.port, raw_url=raw)
    if kind == "trojan":
        profile.password = user
    else:
        profile.uuid = user
        profile.vless_url_host = host
        profile.flow = query.get("flow", "")
        profile.packet_encoding = query.get("packetencoding")
    security = normalize_security(query.get("security") or query.get("tls") or default_security, default_security)
    profile.tls.enabled = security in ("tls", "reality", "xtls")
    profile.tls.security = security
    sni = query.get("sni") or query.get("servername") or query.get("peer") or ""
    if sni:
        profile.tls.server_name = normalize_hostname(sni)
    profile.tls.allow_insecure = truthy(query.get("allowinsecure") or query.get("allow_insecure") or query.get("insecure"))
    profile.tls.fingerprint = query.get("fp") or query.get("fingerprint") or profile.tls.fingerprint
    alpn = query.get("alpn")
    if alpn:
        profile.tls.alpn = [item.strip() for item in alpn.split(",") if item.strip()]
    profile.tls.public_key = empty_to_none(query.get("pbk"))
    profile.tls.short_id = empty_to_none(query.get("sid"))
    profile.tls.spider_x = empty_to_none(query.get("spx"))
    transport = (query.get("type") or "tcp").lower()
    profile.transport.kind = transport if transport in ("tcp", "ws", "grpc", "http", "httpupgrade", "xhttp", "splithttp") else "tcp"
    profile.transport.path = unquote(query.get("path", ""))
    profile.transport.host = normalize_hostname(query.get("host", ""))
    profile.transport.service_name = query.get("servicename", "")
    profile.transport.authority = empty_to_none(query.get("authority"))
    profile.transport.header_type = empty_to_none(query.get("headertype"))
    profile.transport.mode = empty_to_none(query.get("mode"))
    return profile


def _parse_vmess(raw: str) -> Profile:
    payload = raw[len("vmess://") :]
    encoded = payload.split("#", 1)[0]
    decoded = decode_base64_url(encoded) or unquote(encoded)
    try:
        data = json.loads(decoded)
    except Exception as exc:
        raise ProfileImportError("VMess payload is not valid JSON.") from exc

    def string(keys: list[str], default: str = "") -> str:
        for key in keys:
            value = data.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return default

    host = normalize_hostname(string(["add", "address"]))
    if not host:
        raise ProfileImportError("Missing host in VMess URL.")
    try:
        port = int(string(["port"]))
    except ValueError as exc:
        raise ProfileImportError("Missing port in VMess URL.") from exc

    profile = Profile(name=string(["ps"], "VMess"), kind="vmess", server=host, server_port=port, raw_url=raw)
    profile.uuid = string(["id"])
    security = normalize_security(string(["tls"], ""), "none")
    profile.tls.enabled = security in ("tls", "reality")
    profile.tls.security = security
    profile.tls.server_name = normalize_hostname(string(["sni", "serverName", "peer"], host))
    profile.tls.allow_insecure = truthy(string(["allowInsecure", "insecure"], "0"))
    profile.tls.fingerprint = string(["fp", "fingerprint"], profile.tls.fingerprint)
    alpn = string(["alpn"])
    if alpn:
        profile.tls.alpn = [item.strip() for item in alpn.split(",") if item.strip()]
    profile.tls.public_key = empty_to_none(string(["pbk"]))
    profile.tls.short_id = empty_to_none(string(["sid"]))
    profile.tls.spider_x = empty_to_none(string(["spx"]))
    network = string(["net", "type"], "tcp").lower()
    profile.transport.kind = network if network in ("tcp", "ws", "grpc", "http", "httpupgrade", "xhttp", "splithttp") else "tcp"
    profile.transport.host = normalize_hostname(string(["host"], host))
    profile.transport.path = string(["path"], "/")
    profile.transport.service_name = string(["serviceName"])
    profile.transport.authority = empty_to_none(string(["authority"]))
    profile.transport.header_type = empty_to_none(string(["headerType"]))
    profile.transport.mode = empty_to_none(string(["mode"]))
    return profile


def _parse_shadowsocks(raw: str, scheme: str) -> Profile:
    prefix = f"{scheme}://"
    payload = raw[len(prefix) :]
    body, _, fragment = payload.partition("#")
    remark = unquote(fragment) if fragment else "Shadowsocks"
    authority_or_encoded, _, query = body.partition("?")
    authority = authority_or_encoded if "@" in authority_or_encoded else (decode_base64_url(authority_or_encoded) or authority_or_encoded)
    parsed = urlparse(f"ss://{authority}" + (f"?{query}" if query else ""))
    host = normalize_hostname(parsed.hostname or "")
    if not host or parsed.port is None:
        raise ProfileImportError("Missing host or port in Shadowsocks URL.")
    profile = Profile(name=remark, kind="shadowsocks", server=host, server_port=parsed.port, raw_url=raw)
    raw_user = unquote(parsed.username or "")
    raw_password = unquote(parsed.password or "")
    if not raw_password:
        decoded_credentials = decode_base64_url(raw_user) or raw_user
        if ":" in decoded_credentials:
            method, password = decoded_credentials.split(":", 1)
            profile.method = method
            profile.password = password
    else:
        profile.method = raw_user
        profile.password = raw_password
    profile.tls.enabled = False
    profile.tls.security = "none"
    return profile


def _query_map(query: str) -> dict[str, str]:
    return {key.lower(): value for key, value in parse_qsl(query, keep_blank_values=True)}


def truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def normalize_hostname(value: str) -> str:
    text = unquote(value or "").strip()
    while text.endswith("."):
        text = text[:-1]
    return text


def normalize_security(raw: str, default: str) -> str:
    value = (raw or "").strip().lower()
    if not value:
        return default
    if value in ("1", "true", "tls"):
        return "tls"
    if value in ("0", "false", "none"):
        return "none"
    return value


def decode_base64_url(value: str) -> str | None:
    text = (value or "").strip().replace("-", "+").replace("_", "/")
    if not text:
        return None
    text += "=" * ((4 - len(text) % 4) % 4)
    try:
        return base64.b64decode(text, validate=False).decode("utf-8")
    except Exception:
        return None


def empty_to_none(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None
