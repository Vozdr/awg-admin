from __future__ import annotations

import base64
import hashlib
import ipaddress
import io
import json
import logging
import re
import secrets
import struct
import threading
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import qrcode
from qrcode.image.svg import SvgPathImage

from .errors import AppError
from .awg_docs import awg_environment_docs
from .models import ClientCreate, ClientUpdate, ServerSettingsUpdate
from .routeros import RouterOSClient
from .settings import ServerSettings


LOGGER = logging.getLogger("awgm-admin")

INVALID_FILENAME_CHARS = re.compile(r"[^\w .()-]+", re.UNICODE)

WG_MENU = "interface/wireguard"
PEER_MENU = "interface/wireguard/peers"
CONTAINER_MENU = "container"
ENV_MENU = "container/envs"
ADDRESS_MENU = "ip/address"


def configuration_filename(server_name: str, client_name: str) -> str:
    def safe_part(value: str, fallback: str) -> str:
        cleaned = INVALID_FILENAME_CHARS.sub("_", str(value or ""))
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
        return (cleaned[:80].rstrip(" ._") or fallback)

    return f"{safe_part(server_name, 'server')}+{safe_part(client_name, 'client')}.conf"


def extract_container_start_failure(records: list[dict[str, str]], container_name: str) -> str:
    entries: list[tuple[str, str]] = []
    for record in records:
        message = (record.get("message", "") or record.get("msg", "")).strip()
        if not message:
            continue
        message = re.sub(rf"^{re.escape(container_name)}(?:::?|)\s+", "", message, count=1)
        entries.append((record.get("time", "").strip(), message))
    if not entries:
        return ""

    end = next((index for index in range(len(entries) - 1, -1, -1) if "*** error: exited with status" in entries[index][1]), -1)
    if end < 0:
        return ""
    start = next((index for index in range(end, -1, -1) if entries[index][1] == "*** start"), -1)
    useful: list[str] = []
    for timestamp, message in entries[start + 1:end + 1]:
        if message.startswith("*** started PATH="):
            continue
        if message in {"*** start", "*** restart"}:
            continue
        useful.append(f"{timestamp} {message}".strip())
    return "\n".join(useful)[-3000:]


BASE_REQUIRED_AWG_ENV = {
    "AWG_MODE",
    "AWG_LISTEN",
    "AWG_REMOTE",
    "AWG_JC",
    "AWG_JMIN",
    "AWG_JMAX",
    "AWG_S1",
    "AWG_S2",
    "AWG_H1",
    "AWG_H2",
    "AWG_H3",
    "AWG_H4",
    "AWG_SERVER_PUB",
    "AWG_CLIENT_PUBS",
}


def required_awg_environment_keys() -> set[str]:
    return awg_environment_docs.unconditional_required_keys()


def required_environment_issues(
    records: list[dict[str, str]], required_keys: set[str]
) -> dict[str, list[str]]:
    by_key: dict[str, list[dict[str, str]]] = {}
    for record in records:
        if record.get("key"):
            by_key.setdefault(record["key"], []).append(record)
    missing: list[str] = []
    disabled: list[str] = []
    empty: list[str] = []
    for key in sorted(required_keys):
        key_records = by_key.get(key, [])
        if not key_records:
            missing.append(key)
            continue
        enabled = [record for record in key_records if not bool_value(record.get("disabled", ""))]
        if not enabled:
            disabled.append(key)
        elif not any(record.get("value", "").strip() for record in enabled):
            empty.append(key)
    return {"missing": missing, "disabled": disabled, "empty": empty}

AWG_ENV_TO_CONFIG = {
    "AWG_JC": "Jc",
    "AWG_JMIN": "Jmin",
    "AWG_JMAX": "Jmax",
    "AWG_S1": "S1",
    "AWG_S2": "S2",
    "AWG_S3": "S3",
    "AWG_S4": "S4",
    "AWG_H1": "H1",
    "AWG_H2": "H2",
    "AWG_H3": "H3",
    "AWG_H4": "H4",
    "AWG_I1": "I1",
    "AWG_I2": "I2",
    "AWG_I3": "I3",
    "AWG_I4": "I4",
    "AWG_I5": "I5",
    "AWG_HEADER_PROTECTION_KEY": "HeaderProtectionKey",
    "AWG_RANDOM_TRAILERS": "RandomTrailers",
    "AWG_DISABLE_COOKIES": "DisableCookies",
    "AWG_REJECT_AFTER": "RejectAfterTime",
    "AWG_CONTENT_PADDING": "ContentPaddingAddition",
    "AWG_REKEY_AFTER": "RekeyAfterTime",
    "AWG_REKEY_TIMEOUT": "RekeyTimeout",
    "AWG_KEEPALIVE_TIMEOUT": "KeepaliveTimeout",
    "AWG_MAX_HANDSHAKE_ATTEMPTS": "MaxHandshakeAttempts",
}

AWG_ENV_DESCRIPTION_KEYS = {
    "AWG_MODE": "serverSettings.env.mode",
    "AWG_LISTEN": "serverSettings.env.listen",
    "AWG_REMOTE": "serverSettings.env.remote",
    "AWG_JC": "serverSettings.env.jc",
    "AWG_JMIN": "serverSettings.env.jmin",
    "AWG_JMAX": "serverSettings.env.jmax",
    "AWG_S1": "serverSettings.env.s1",
    "AWG_S2": "serverSettings.env.s2",
    "AWG_S3": "serverSettings.env.s3",
    "AWG_S4": "serverSettings.env.s4",
    "AWG_H1": "serverSettings.env.h",
    "AWG_H2": "serverSettings.env.h",
    "AWG_H3": "serverSettings.env.h",
    "AWG_H4": "serverSettings.env.h",
    "AWG_I1": "serverSettings.env.i",
    "AWG_I2": "serverSettings.env.i",
    "AWG_I3": "serverSettings.env.i",
    "AWG_I4": "serverSettings.env.i",
    "AWG_I5": "serverSettings.env.i",
    "AWG_SERVER_PUB": "serverSettings.env.serverPublicKey",
    "AWG_CLIENT_PUB": "serverSettings.env.clientPublicKey",
    "AWG_CLIENT_PUBS": "serverSettings.env.clientPublicKeys",
    "AWG_CLIENT_PUBS_FILE": "serverSettings.env.clientPublicKeysFile",
    "AWG_HEADER_PROTECTION_KEY": "serverSettings.env.headerProtectionKey",
    "AWG_RANDOM_TRAILERS": "serverSettings.env.randomTrailers",
    "AWG_DISABLE_COOKIES": "serverSettings.env.disableCookies",
    "AWG_REJECT_AFTER": "serverSettings.env.acceptedNotApplied",
    "AWG_CONTENT_PADDING": "serverSettings.env.acceptedNotApplied",
    "AWG_REKEY_AFTER": "serverSettings.env.acceptedNotApplied",
    "AWG_REKEY_TIMEOUT": "serverSettings.env.acceptedNotApplied",
    "AWG_KEEPALIVE_TIMEOUT": "serverSettings.env.acceptedNotApplied",
    "AWG_MAX_HANDSHAKE_ATTEMPTS": "serverSettings.env.acceptedNotApplied",
    "AWG_LOG_LEVEL": "serverSettings.env.logLevel",
    "AWG_STATS": "serverSettings.env.stats",
    "AWG_SPIN": "serverSettings.env.spin",
}
PROTECTED_AWG_ENV_KEYS = {"AWG_CLIENT_PUB", "AWG_CLIENT_PUBS", "AWG_REMOTE", "AWG_SERVER_PUB", "AWG_HEADER_PROTECTION_KEY"}

METADATA_RE = re.compile(r"\s*\[(?:awgm-admin|awg-admin):v1(?:;disabled_at=([^\]]+))?\]\s*$")
MISSING_PRIVATE_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEA="


@dataclass
class ServerContext:
    container: dict[str, str]
    env_records: list[dict[str, str]]
    all_env_records: list[dict[str, str]]
    env: dict[str, str]
    wireguard: dict[str, str]
    addresses: list[dict[str, str]]
    peers: list[dict[str, str]]

    @property
    def envlists(self) -> list[str]:
        return split_values(self.container.get("envlists", "") or self.container.get("envlist", ""))


def bool_value(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def clean_comment_metadata(comment: str) -> tuple[str, str]:
    comment = comment or ""
    match = METADATA_RE.search(comment)
    if not match:
        return comment.strip(), ""
    return comment[: match.start()].strip(), match.group(1) or ""


def comment_with_disabled_at(comment: str, disabled_at: str | None) -> str:
    clean, _ = clean_comment_metadata(comment)
    if not disabled_at:
        return clean
    suffix = f"[awgm-admin:v1;disabled_at={disabled_at}]"
    return f"{clean} {suffix}".strip()


def split_values(value: str) -> list[str]:
    return [item for item in re.split(r"[,\s]+", value or "") if item]


def normalize_seconds(value: str | int) -> int:
    match = re.search(r"\d+", str(value or "0"))
    return int(match.group(0)) if match else 0


def format_bytes(raw: str) -> str:
    try:
        value = int(raw or 0)
    except ValueError:
        return raw or "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def qr_data_url(text: str) -> str:
    image = qrcode.make(text, image_factory=SvgPathImage)
    buffer = io.BytesIO()
    image.save(buffer)
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def decode_wireguard_key(value: str, field: str, allow_empty: bool = False) -> bytes:
    if allow_empty and not value:
        return b""
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise AppError(
            "CLIENT_KEY_INVALID",
            "error.clientKeyInvalid",
            technical=f"{field} is not valid base64",
            status_code=422,
        ) from exc
    if len(raw) != 32:
        raise AppError(
            "CLIENT_KEY_INVALID",
            "error.clientKeyInvalid",
            technical=f"{field} must contain 32 bytes",
            status_code=422,
        )
    return raw


def wireguard_public_key(private_key: str) -> str:
    scalar = bytearray(decode_wireguard_key(private_key, "PrivateKey"))
    scalar[0] &= 248
    scalar[31] &= 127
    scalar[31] |= 64
    value = int.from_bytes(scalar, "little")
    x_1 = 9
    x_2, z_2 = 1, 0
    x_3, z_3 = 9, 1
    swap = 0
    prime = 2**255 - 19
    a24 = 121665
    for position in range(254, -1, -1):
        bit = (value >> position) & 1
        swap ^= bit
        if swap:
            x_2, x_3 = x_3, x_2
            z_2, z_3 = z_3, z_2
        swap = bit
        a = (x_2 + z_2) % prime
        aa = (a * a) % prime
        b = (x_2 - z_2) % prime
        bb = (b * b) % prime
        e = (aa - bb) % prime
        c = (x_3 + z_3) % prime
        d = (x_3 - z_3) % prime
        da = (d * a) % prime
        cb = (c * b) % prime
        x_3 = ((da + cb) ** 2) % prime
        z_3 = (x_1 * ((da - cb) ** 2)) % prime
        x_2 = (aa * bb) % prime
        z_2 = (e * (aa + a24 * e)) % prime
    if swap:
        x_2, x_3 = x_3, x_2
        z_2, z_3 = z_3, z_2
    public = (x_2 * pow(z_2, prime - 2, prime)) % prime
    return base64.b64encode(public.to_bytes(32, "little")).decode("ascii")


def generate_wireguard_key_pair() -> tuple[str, str]:
    private = bytearray(secrets.token_bytes(32))
    private[0] &= 248
    private[31] &= 127
    private[31] |= 64
    private_key = base64.b64encode(bytes(private)).decode("ascii")
    return private_key, wireguard_public_key(private_key)


def parse_conf(text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    section = ""
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            result.setdefault(section, {})
            continue
        if section and "=" in line:
            key, value = line.split("=", 1)
            result[section][key.strip()] = value.strip()
    return result


def build_awg_config(
    private_key: str,
    peer_record: dict[str, str],
    wireguard: dict[str, str],
    env: dict[str, str],
    endpoint: str,
) -> str:
    server_public_key = wireguard.get("public-key", "")
    if not server_public_key:
        raise AppError(
            "CLIENT_CONFIG_INVALID",
            "error.clientConfigInvalid",
            technical="WireGuard interface does not contain public-key",
            status_code=502,
        )
    interface = {
        "PrivateKey": private_key,
        "Address": peer_record.get("allowed-address", "") or peer_record.get("client-address", ""),
        "DNS": peer_record.get("client-dns", ""),
        "MTU": wireguard.get("mtu", ""),
    }
    for env_key, config_key in AWG_ENV_TO_CONFIG.items():
        value = env.get(env_key, "").strip()
        if value:
            interface[config_key] = value
    peer = {
        "PublicKey": server_public_key,
        "PresharedKey": "" if peer_record.get("preshared-key", "").lower() == "none" else peer_record.get("preshared-key", ""),
        "Endpoint": endpoint,
        "AllowedIPs": peer_record.get("client-allowed-address", "") or "0.0.0.0/0,::/0",
        "PersistentKeepalive": str(normalize_seconds(peer_record.get("client-keepalive", "") or "0")),
    }

    interface_order = [
        "PrivateKey",
        "Address",
        "DNS",
        "MTU",
        "Jc",
        "Jmin",
        "Jmax",
        "S1",
        "S2",
        "S3",
        "S4",
        "H1",
        "H2",
        "H3",
        "H4",
        "I1",
        "I2",
        "I3",
        "I4",
        "I5",
        "HeaderProtectionKey",
        "RandomTrailers",
        "DisableCookies",
        "ContentPaddingAddition",
        "RejectAfterTime",
        "RekeyAfterTime",
        "RekeyTimeout",
        "KeepaliveTimeout",
        "MaxHandshakeAttempts",
    ]
    peer_order = ["PublicKey", "PresharedKey", "Endpoint", "AllowedIPs", "PersistentKeepalive"]

    def render(values: dict[str, str], order: list[str]) -> list[str]:
        lines: list[str] = []
        emitted: set[str] = set()
        for key in order:
            if key in values and (values[key] != "" or key == "PrivateKey"):
                lines.append(f"{key} = {values[key]}")
                emitted.add(key)
        for key, value in values.items():
            if key not in emitted and value != "":
                lines.append(f"{key} = {value}")
        return lines

    return "\n".join(["[Interface]", *render(interface, interface_order), "", "[Peer]", *render(peer, peer_order), ""])


def make_vpn_uri(config: str, env: dict[str, str], server_name: str) -> str:
    parsed = parse_conf(config)
    interface = parsed.get("Interface", {})
    peer = parsed.get("Peer", {})
    endpoint = peer.get("Endpoint", "")
    endpoint_host, endpoint_port = endpoint.rsplit(":", 1)
    addresses = split_values(interface.get("Address", ""))
    ipv4 = next((item.split("/", 1)[0] for item in addresses if ":" not in item), "")
    ipv6 = next((item.split("/", 1)[0] for item in addresses if ":" in item), "")
    allowed_ips = split_values(peer.get("AllowedIPs", ""))
    dns_values = split_values(interface.get("DNS", "")) or ["1.1.1.1"]
    dns1 = dns_values[0]
    dns2 = dns_values[1] if len(dns_values) > 1 else dns1
    inner: dict[str, Any] = {
        "H1": interface.get("H1", ""),
        "H2": interface.get("H2", ""),
        "H3": interface.get("H3", ""),
        "H4": interface.get("H4", ""),
        "Jc": interface.get("Jc", ""),
        "Jmin": interface.get("Jmin", ""),
        "Jmax": interface.get("Jmax", ""),
        "S1": interface.get("S1", ""),
        "S2": interface.get("S2", ""),
        "S3": interface.get("S3", "0"),
        "S4": interface.get("S4", "0"),
    }
    for key in ("I1", "I2", "I3", "I4", "I5", "HeaderProtectionKey", "ContentPaddingAddition"):
        if interface.get(key):
            inner[key] = interface[key]
    inner.update(
        {
            "allowed_ips": allowed_ips,
            "client_ip": ipv4,
            "client_ipv6": ipv6,
            "client_priv_key": interface.get("PrivateKey", ""),
            "config": config.rstrip(),
            "hostName": endpoint_host,
            "mtu": interface.get("MTU", "1280"),
            "persistent_keep_alive": peer.get("PersistentKeepalive", "30"),
            "port": int(endpoint_port),
            "server_pub_key": peer.get("PublicKey", ""),
        }
    )
    if peer.get("PresharedKey"):
        inner["psk_key"] = peer["PresharedKey"]
    outer = {
        "containers": [
            {
                "awg": {
                    "isThirdPartyConfig": True,
                    "last_config": json.dumps(inner, ensure_ascii=False, separators=(",", ":")),
                    "port": str(endpoint_port),
                    "protocol_version": "2",
                    "transport_proto": "udp",
                },
                "container": "amnezia-awg",
            }
        ],
        "defaultContainer": "amnezia-awg",
        "description": server_name,
        "dns1": dns1,
        "dns2": dns2,
        "hostName": endpoint_host,
    }
    raw = json.dumps(outer, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload = struct.pack(">I", len(raw)) + zlib.compress(raw)
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return "vpn://" + encoded


class AwgAdminService:
    def __init__(self, settings: ServerSettings, client: RouterOSClient):
        self.settings = settings
        self.client = client
        self.operation_lock = threading.RLock()
        self.last_success: str = ""
        self.runtime_error: AppError | None = None
        self.container_running: bool | None = None

    def _remember_success(self) -> None:
        self.last_success = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.runtime_error = None

    def _remember_error(self, error: AppError) -> None:
        self.runtime_error = error

    def ping(self) -> dict[str, str]:
        records = self.client.print("system/resource", proplist=("version", "board-name", "architecture-name"))
        self._remember_success()
        return records[0] if records else {}

    def load_context(self, validate: bool = True) -> ServerContext:
        try:
            containers = self.client.print(
                CONTAINER_MENU,
                proplist=(".id", "name", "comment", "status", "tag", "envlists", "envlist", "interface", "running"),
            )
            container = next(
                (
                    item
                    for item in containers
                    if item.get("name") == self.settings.awg_container_name
                    or item.get("comment") == self.settings.awg_container_name
                ),
                None,
            )
            if not container:
                self.container_running = False
                raise AppError(
                    "AWG_CONTAINER_NOT_FOUND",
                    "error.containerNotFound",
                    technical=f"container {self.settings.awg_container_name!r} was not found",
                    status_code=503,
                    hint_key="error.containerNotFoundHint",
                )
            self.container_running = self._container_running(container)
            all_env_records = self.client.print(
                ENV_MENU, proplist=(".id", "list", "key", "value", "comment", "disabled")
            )
            envlists = split_values(container.get("envlists", "") or container.get("envlist", ""))
            if not envlists:
                raise AppError(
                    "AWG_ENVLISTS_MISSING",
                    "error.envlistsMissing",
                    technical="container has no envlists",
                    status_code=503,
                    hint_key="error.envlistsMissingHint",
                )
            records_by_list: dict[str, list[dict[str, str]]] = {}
            for item in all_env_records:
                records_by_list.setdefault(item.get("list", ""), []).append(item)
            env_records: list[dict[str, str]] = []
            env: dict[str, str] = {}
            for list_name in envlists:
                for item in records_by_list.get(list_name, []):
                    env_records.append(item)
                    if item.get("key"):
                        env[item["key"]] = item.get("value", "")
            missing = sorted(BASE_REQUIRED_AWG_ENV - env.keys())
            if validate and missing:
                raise AppError(
                    "AWG_ENV_INVALID",
                    "error.awgEnvInvalid",
                    technical="missing keys: " + ", ".join(missing),
                    status_code=503,
                    hint_key="error.awgEnvInvalidHint",
                )
            if validate and env.get("AWG_MODE", "").lower() != "server":
                raise AppError(
                    "AWG_MODE_INVALID",
                    "error.awgModeInvalid",
                    technical=f"AWG_MODE={env.get('AWG_MODE', '')!r}",
                    status_code=503,
                )
            interfaces = self.client.print(
                WG_MENU,
                proplist=(".id", "name", "listen-port", "public-key", "private-key", "disabled", "running", "mtu"),
            )
            matches = [item for item in interfaces if item.get("public-key") == env.get("AWG_SERVER_PUB")]
            if len(matches) != 1 and not validate:
                remote_port = env.get("AWG_REMOTE", "").rsplit(":", 1)[-1]
                port_matches = [item for item in interfaces if remote_port and item.get("listen-port") == remote_port]
                if len(port_matches) == 1:
                    matches = port_matches
                elif len(interfaces) == 1:
                    matches = interfaces
            if len(matches) != 1:
                raise AppError(
                    "WG_INTERFACE_NOT_FOUND",
                    "error.wgInterfaceNotFound",
                    technical=f"interfaces matching AWG_SERVER_PUB: {len(matches)}",
                    status_code=503,
                    hint_key="error.wgInterfaceNotFoundHint",
                )
            wireguard = matches[0]
            if validate and bool_value(wireguard.get("disabled", "")):
                raise AppError(
                    "WG_INTERFACE_DISABLED",
                    "error.wgInterfaceDisabled",
                    technical=f"interface {wireguard.get('name')} is disabled",
                    status_code=503,
                )
            addresses = [
                item
                for item in self.client.print(ADDRESS_MENU, proplist=(".id", "address", "network", "interface", "disabled"))
                if item.get("interface") == wireguard.get("name") and not bool_value(item.get("disabled", ""))
            ]
            if validate and not addresses:
                raise AppError(
                    "WG_ADDRESSES_MISSING",
                    "error.wgAddressesMissing",
                    technical=f"no addresses assigned to {wireguard.get('name')}",
                    status_code=503,
                )
            remote_port = env.get("AWG_REMOTE", "").rsplit(":", 1)[-1]
            if validate and remote_port and wireguard.get("listen-port") and remote_port != wireguard.get("listen-port"):
                raise AppError(
                    "AWG_WG_PORT_MISMATCH",
                    "error.portMismatch",
                    technical=f"AWG_REMOTE port {remote_port}, WireGuard listen-port {wireguard.get('listen-port')}",
                    status_code=503,
                )
            peers = [
                item
                for item in self.client.print(
                    PEER_MENU,
                    proplist=(
                        ".id",
                        "name",
                        "comment",
                        "interface",
                        "disabled",
                        "public-key",
                        "private-key",
                        "preshared-key",
                        "allowed-address",
                        "client-address",
                        "client-dns",
                        "client-allowed-address",
                        "client-keepalive",
                        "persistent-keepalive",
                        "last-handshake",
                        "rx",
                        "tx",
                    ),
                )
                if item.get("interface") == wireguard.get("name")
            ]
            context = ServerContext(container, env_records, all_env_records, env, wireguard, addresses, peers)
            self._remember_success()
            return context
        except AppError as exc:
            self._remember_error(exc)
            raise

    def startup_validate(self) -> None:
        resource = self.ping()
        LOGGER.info(
            "Connected to MikroTik board=%s architecture=%s RouterOS=%s",
            resource.get("board-name", "unknown"),
            resource.get("architecture-name", "unknown"),
            resource.get("version", "unknown"),
        )
        context = self.load_context(validate=True)
        if not self._container_running(context.container):
            LOGGER.error(
                "AWG_CONTAINER_STOPPED: container %s is not running (status=%s); panel remains available",
                self.settings.awg_container_name,
                context.container.get("status", "unknown"),
            )

    def _used_addresses(self, context: ServerContext) -> set[ipaddress._BaseAddress]:
        used: set[ipaddress._BaseAddress] = set()
        for record in context.addresses:
            try:
                used.add(ipaddress.ip_interface(record.get("address", "")).ip)
            except ValueError:
                pass
        for peer in context.peers:
            for field in ("allowed-address", "client-address"):
                for raw in split_values(peer.get(field, "")):
                    try:
                        used.add(ipaddress.ip_interface(raw).ip)
                    except ValueError:
                        pass
        return used

    def pools(self, context: ServerContext) -> list[dict[str, Any]]:
        used = self._used_addresses(context)
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for record in context.addresses:
            try:
                interface = ipaddress.ip_interface(record.get("address", ""))
            except ValueError:
                continue
            if interface.version != 4 or interface.network.prefixlen >= 31:
                continue
            cidr = str(interface.network)
            if cidr in seen:
                continue
            seen.add(cidr)
            free = self.first_free_address(interface.network, used)
            result.append(
                {
                    "cidr": cidr,
                    "server_address": str(interface.ip),
                    "free_address": str(free) if free else "",
                    "available": free is not None,
                }
            )
        return result

    @staticmethod
    def first_free_address(network: ipaddress.IPv4Network, used: set[ipaddress._BaseAddress]) -> ipaddress.IPv4Address | None:
        current = int(network.network_address) + 1
        last = int(network.broadcast_address) - 1
        used_in_network = sorted(
            int(address)
            for address in used
            if isinstance(address, ipaddress.IPv4Address) and address in network
        )
        for used_address in used_in_network:
            if used_address < current:
                continue
            if used_address == current:
                current += 1
                continue
            break
        return ipaddress.IPv4Address(current) if current <= last else None

    def sync_status(self, context: ServerContext) -> dict[str, Any]:
        peer_keys = {item.get("public-key", "") for item in context.peers if item.get("public-key")}
        env_keys = set(split_values(context.env.get("AWG_CLIENT_PUBS", "")))
        return {
            "ok": peer_keys == env_keys,
            "missing_in_proxy": sorted(peer_keys - env_keys),
            "missing_in_wireguard": sorted(env_keys - peer_keys),
        }

    def _peer_payload(self, peer: dict[str, str]) -> dict[str, Any]:
        comment, disabled_at = clean_comment_metadata(peer.get("comment", ""))
        client_address = peer.get("allowed-address", "") or peer.get("client-address", "")
        return {
            "id": peer.get(".id", ""),
            "name": peer.get("name", "") or comment or peer.get("public-key", "")[:12],
            "comment": comment,
            "disabled": bool_value(peer.get("disabled", "")),
            "disabled_at": disabled_at,
            "public_key": peer.get("public-key", ""),
            "address": client_address,
            "allowed_address": peer.get("allowed-address", ""),
            "dns": peer.get("client-dns", ""),
            "client_allowed_ips": peer.get("client-allowed-address", "") or "0.0.0.0/0,::/0",
            "keepalive": peer.get("client-keepalive", "") or peer.get("persistent-keepalive", "") or "0s",
            "last_handshake": peer.get("last-handshake", ""),
            "rx": peer.get("rx", "0"),
            "tx": peer.get("tx", "0"),
            "rx_human": format_bytes(peer.get("rx", "0")),
            "tx_human": format_bytes(peer.get("tx", "0")),
        }

    def dashboard(self) -> dict[str, Any]:
        context = self.load_context(validate=False)
        return {
            "clients": [self._peer_payload(peer) for peer in context.peers],
            "pools": self.pools(context),
            "sync": self.sync_status(context),
            "server": {
                "id": self.settings.id,
                "name": self.settings.display_name,
                "mikrotik_host": self.settings.mikrotik_host,
                "container_name": self.settings.awg_container_name,
                "container_status": "running" if self._container_running(context.container) else "stopped",
                "wireguard_interface": context.wireguard.get("name", ""),
                "listen_port": context.wireguard.get("listen-port", ""),
                "client_endpoint": self.settings.awg_client_endpoint,
                "last_success": self.last_success,
                "client_defaults": {
                    "keepalive": self.settings.client_default_keepalive,
                    "dns": self.settings.client_default_dns,
                    "allowed_ips": self.settings.client_default_allowed_ips,
                },
            },
        }

    def server_settings(self) -> dict[str, Any]:
        context = self.load_context(validate=False)
        occupied_addresses = sorted(
            {
                peer.get("allowed-address", "") or peer.get("client-address", "")
                for peer in context.peers
                if peer.get("allowed-address", "") or peer.get("client-address", "")
            }
        )
        rows: list[dict[str, Any]] = [
            {"key": "Container", "label_key": "serverSettings.container", "value": self.settings.awg_container_name, "section": "general"},
            {"key": "Container status", "label_key": "serverSettings.containerStatus", "value_key": "server.running" if self._container_running(context.container) else "server.stopped", "section": "general"},
            {"key": "WireGuard interface", "label_key": "serverSettings.interface", "value": context.wireguard.get("name", ""), "section": "general"},
            {"key": "WireGuard listen port", "label_key": "serverSettings.listenPort", "description_key": "serverSettings.listenPortHelp", "value": context.wireguard.get("listen-port", ""), "section": "general", "editable": True, "kind": "port"},
            {"key": "Client endpoint", "label_key": "serverSettings.clientEndpoint", "value": self.settings.awg_client_endpoint, "section": "general"},
            {"key": "Addresses", "label_key": "serverSettings.addresses", "value": ", ".join(item.get("address", "") for item in context.addresses), "section": "general", "editable": True, "kind": "addresses"},
            {"key": "Occupied addresses", "label_key": "serverSettings.occupiedAddresses", "value": ", ".join(occupied_addresses), "section": "general"},
        ]
        key_counts: dict[str, int] = {}
        list_key_counts: dict[tuple[str, str], int] = {}
        for record in context.env_records:
            key = record.get("key", "")
            envlist = record.get("list", "")
            key_counts[key] = key_counts.get(key, 0) + 1
            list_key_counts[(envlist, key)] = list_key_counts.get((envlist, key), 0) + 1
        managed_keys = {"AWG_CLIENT_PUB", "AWG_CLIENT_PUBS"}
        read_only_keys = managed_keys | {"AWG_REMOTE", "AWG_HEADER_PROTECTION_KEY"}
        required_keys = required_awg_environment_keys()
        env_rows: list[dict[str, Any]] = []
        for record in sorted(context.env_records, key=lambda item: (item.get("key", ""), item.get("list", ""), item.get(".id", ""))):
            key = record.get("key", "")
            envlist = record.get("list", "")
            env_rows.append({
                "id": record.get(".id", ""),
                "key": key,
                "envlist": envlist,
                "value": record.get("value", ""),
                "comment": record.get("comment", ""),
                "disabled": bool_value(record.get("disabled", "")),
                "description_key": AWG_ENV_DESCRIPTION_KEYS.get(key, ""),
                "required": key in required_keys,
                "managed": key in managed_keys,
                "read_only": key in read_only_keys or key == "AWG_SERVER_PUB",
                "special_action": "regenerate-server-key" if key == "AWG_SERVER_PUB" else "regenerate-header-key" if key == "AWG_HEADER_PROTECTION_KEY" else "",
                "duplicate": key_counts.get(key, 0) > 1,
                "duplicate_in_list": list_key_counts.get((envlist, key), 0) > 1,
            })
        return {
            "settings": rows,
            "environment": env_rows,
            "environment_docs": awg_environment_docs.info(),
            "protected_environment_keys": sorted(PROTECTED_AWG_ENV_KEYS),
            "required_environment_keys": sorted(required_keys),
            "required_environment": required_environment_issues(context.env_records, required_keys),
            "envlists": context.envlists,
            "existing_envlists": sorted({item.get("list", "") for item in context.all_env_records if item.get("list")}),
            "addresses": [{"id": item.get(".id", ""), "address": item.get("address", "")} for item in context.addresses],
            "revision": self._settings_revision(context),
            "editable": True,
        }

    def _settings_revision(self, context: ServerContext) -> str:
        payload = {
            "envlists": context.envlists,
            "environment": sorted(
                [
                    {
                        "id": item.get(".id", ""),
                        "list": item.get("list", ""),
                        "key": item.get("key", ""),
                        "value": item.get("value", ""),
                        "comment": item.get("comment", ""),
                        "disabled": bool_value(item.get("disabled", "")),
                    }
                    for item in context.env_records
                ],
                key=lambda item: (item["id"], item["list"], item["key"]),
            ),
            "addresses": sorted(
                [{"id": item.get(".id", ""), "address": item.get("address", "")} for item in context.addresses],
                key=lambda item: item["id"],
            ),
            "wireguard": {
                "id": context.wireguard.get(".id", ""),
                "listen_port": context.wireguard.get("listen-port", ""),
                "public_key": context.wireguard.get("public-key", ""),
            },
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def validate_env_variable(self, envlist: str, key: str) -> dict[str, Any]:
        context = self.load_context(validate=False)
        existing_lists = {item.get("list", "") for item in context.all_env_records if item.get("list")}
        if envlist in existing_lists and envlist not in context.envlists:
            raise AppError(
                "ENVLIST_NOT_ASSIGNED",
                "error.envlistNotAssignedVariable",
                technical=f"envlist {envlist!r} exists but is not assigned to container {self.settings.awg_container_name!r}",
                status_code=409,
                message_args={"key": key, "envlist": envlist},
            )
        duplicates = [
            item for item in context.all_env_records
            if item.get("list") in context.envlists and item.get("key") == key
        ]
        duplicates_in_list = [item for item in duplicates if item.get("list") == envlist]
        if duplicates_in_list:
            raise AppError(
                "ENV_VARIABLE_DUPLICATE",
                "error.envVariableDuplicate",
                technical=f"variable {key!r} already exists in envlist {envlist!r}",
                status_code=409,
                message_args={"key": key, "envlist": envlist},
            )
        return {
            "ok": True,
            "new_envlist": envlist not in existing_lists,
            "duplicate": bool(duplicates),
            "duplicate_count": len(duplicates),
            "duplicate_in_list": bool(duplicates_in_list),
        }

    @staticmethod
    def _replace_endpoint_port(value: str, port: int) -> str:
        host, separator, _ = value.rpartition(":")
        if not separator or not host:
            raise AppError("AWG_REMOTE_INVALID", "error.awgRemoteInvalid", technical=value, status_code=422)
        return f"{host}:{port}"

    @staticmethod
    def _validate_interface_address(value: str) -> str:
        try:
            address = ipaddress.ip_interface(value.strip())
        except ValueError as exc:
            raise AppError("SERVER_ADDRESS_INVALID", "error.serverAddressInvalid", technical=value, status_code=422) from exc
        if address.version != 4:
            raise AppError("SERVER_ADDRESS_INVALID", "error.serverAddressInvalid", technical=value, status_code=422)
        return str(address)

    def _wait_container(self, container_id: str, running: bool, timeout: int) -> None:
        started = time.monotonic()
        deadline = started + timeout
        last_status = ""
        while time.monotonic() < deadline:
            records = self.client.print(CONTAINER_MENU, proplist=(".id", "status", "running"))
            current = next((item for item in records if item.get(".id") == container_id), None)
            if not current:
                raise AppError("AWG_CONTAINER_NOT_FOUND", "error.containerNotFound", status_code=503)
            last_status = current.get("status", "")
            if self._container_running(current) is running:
                return
            normalized_status = last_status.strip().lower()
            if running and ("error" in normalized_status or (normalized_status == "stopped" and time.monotonic() - started >= 3)):
                break
            time.sleep(1)
        raise AppError(
            "PROXY_RESTART_FAILED",
            "error.proxyRestartFailed",
            technical=f"container did not become {'running' if running else 'stopped'}; status: {last_status or 'unknown'}",
            status_code=502,
            hint_key="error.proxyRestartFailedHint",
        )

    def _container_start_failure_log(self) -> str:
        try:
            records = self.client.print(
                "container/log",
                proplist=("time", "message"),
                queries=(f"?container={self.settings.awg_container_name}",),
            )
        except Exception:
            LOGGER.exception("Unable to read RouterOS container log after failed start")
            return ""
        return extract_container_start_failure(records, self.settings.awg_container_name)

    def update_server_settings(self, data: ServerSettingsUpdate) -> dict[str, Any]:
        with self.operation_lock:
            context = self.load_context(validate=False)
            if data.revision != self._settings_revision(context):
                raise AppError(
                    "SERVER_SETTINGS_CONFLICT",
                    "error.serverSettingsConflict",
                    technical="MikroTik settings changed after the dialog was opened",
                    status_code=409,
                )
            current_by_id = {item.get(".id", ""): item for item in context.env_records}
            address_by_id = {item.get(".id", ""): item for item in context.addresses}
            protected = PROTECTED_AWG_ENV_KEYS
            required_keys = required_awg_environment_keys()
            new_lists: list[str] = []
            added_pairs: set[tuple[str, str]] = set()
            for change in data.env_changes:
                if change.action == "add":
                    pair = (change.envlist, change.key)
                    if pair in added_pairs:
                        raise AppError(
                            "ENV_VARIABLE_DUPLICATE",
                            "error.envVariableDuplicate",
                            technical=f"variable {change.key!r} is added more than once to envlist {change.envlist!r}",
                            status_code=409,
                            message_args={"key": change.key, "envlist": change.envlist},
                        )
                    self.validate_env_variable(change.envlist, change.key)
                    added_pairs.add(pair)
                    if change.key in required_keys and change.disabled:
                        raise AppError(
                            "REQUIRED_ENV_PROTECTED",
                            "error.requiredEnvProtected",
                            technical=f"required variable {change.key!r} cannot be added disabled",
                            status_code=422,
                            message_args={"key": change.key},
                        )
                    if change.key in protected:
                        raise AppError("ENV_VARIABLE_MANAGED", "error.envVariableManaged", technical=change.key, status_code=422)
                    if change.envlist not in context.envlists and change.envlist not in new_lists:
                        new_lists.append(change.envlist)
                else:
                    current = current_by_id.get(change.id)
                    if not current:
                        raise AppError("ENV_VARIABLE_NOT_FOUND", "error.envVariableNotFound", technical=change.id, status_code=409)
                    if current.get("key") in required_keys and (
                        change.action == "delete" or (change.action == "update" and change.disabled)
                    ):
                        raise AppError(
                            "REQUIRED_ENV_PROTECTED",
                            "error.requiredEnvProtected",
                            technical=f"required variable {current.get('key')!r} cannot be disabled or deleted",
                            status_code=422,
                            message_args={"key": current.get("key", "")},
                        )
                    if current.get("key") in protected:
                        raise AppError("ENV_VARIABLE_MANAGED", "error.envVariableManaged", technical=current.get("key", ""), status_code=422)
            normalized_addresses: dict[int, str] = {}
            for index, change in enumerate(data.address_changes):
                if change.action != "add" and change.id not in address_by_id:
                    raise AppError("SERVER_ADDRESS_NOT_FOUND", "error.serverAddressNotFound", technical=change.id, status_code=409)
                if change.action != "delete":
                    normalized_addresses[index] = self._validate_interface_address(change.address)
            final_addresses = {
                self._validate_interface_address(item.get("address", ""))
                for item in context.addresses
                if not any(change.id == item.get(".id") and change.action == "delete" for change in data.address_changes)
            }
            for index, value in normalized_addresses.items():
                change = data.address_changes[index]
                if change.action == "update":
                    final_addresses.discard(self._validate_interface_address(address_by_id[change.id].get("address", "")))
                if value in final_addresses:
                    raise AppError("SERVER_ADDRESS_DUPLICATE", "error.serverAddressDuplicate", technical=value, status_code=409)
                final_addresses.add(value)
            if not final_addresses:
                raise AppError("SERVER_ADDRESSES_EMPTY", "error.serverAddressesEmpty", status_code=422)

            changes_requested = bool(
                data.env_changes
                or data.address_changes
                or data.regenerate_server_key
                or data.regenerate_header_protection_key
                or data.listen_port is not None
            )
            if not changes_requested:
                return self.server_settings()
            container_id = context.container.get(".id", "")
            was_running = self._container_running(context.container)
            if was_running:
                self.client.action(CONTAINER_MENU, "stop", container_id)
                self._wait_container(container_id, False, 20)
            undo_actions: list[Any] = []
            try:
                for change in data.env_changes:
                    if change.action == "add":
                        new_id = self.client.add(ENV_MENU, {
                            "list": change.envlist,
                            "key": change.key,
                            "value": change.value,
                            "comment": change.comment,
                            "disabled": "yes" if change.disabled else "no",
                        })
                        if new_id:
                            undo_actions.append(lambda item_id=new_id: self.client.remove(ENV_MENU, item_id))
                    elif change.action == "delete":
                        old = dict(current_by_id[change.id])
                        self.client.remove(ENV_MENU, change.id)
                        undo_actions.append(lambda record=old: self.client.add(ENV_MENU, {
                            "list": record.get("list", ""), "key": record.get("key", ""),
                            "value": record.get("value", ""), "comment": record.get("comment", ""),
                            "disabled": record.get("disabled", "no"),
                        }))
                    else:
                        old = dict(current_by_id[change.id])
                        self.client.set(ENV_MENU, change.id, {
                            "value": change.value,
                            "comment": change.comment,
                            "disabled": "yes" if change.disabled else "no",
                        })
                        undo_actions.append(lambda item_id=change.id, record=old: self.client.set(ENV_MENU, item_id, {
                            "value": record.get("value", ""), "comment": record.get("comment", ""),
                            "disabled": record.get("disabled", "no"),
                        }))
                candidate_envlists = context.envlists + [item for item in new_lists if item not in context.envlists]
                remaining_env_records = self.client.print(ENV_MENU, proplist=("list",))
                non_empty_envlists = {item.get("list", "") for item in remaining_env_records if item.get("list")}
                envlists = [item for item in candidate_envlists if item in non_empty_envlists]
                if envlists != context.envlists:
                    self.client.set(CONTAINER_MENU, container_id, {"envlists": ",".join(envlists)})
                    undo_actions.append(lambda: self.client.set(CONTAINER_MENU, container_id, {"envlists": ",".join(context.envlists)}))
                for index, change in enumerate(data.address_changes):
                    if change.action == "add":
                        new_id = self.client.add(ADDRESS_MENU, {"address": normalized_addresses[index], "interface": context.wireguard.get("name", "")})
                        if new_id:
                            undo_actions.append(lambda item_id=new_id: self.client.remove(ADDRESS_MENU, item_id))
                    elif change.action == "delete":
                        old = dict(address_by_id[change.id])
                        self.client.remove(ADDRESS_MENU, change.id)
                        undo_actions.append(lambda record=old: self.client.add(ADDRESS_MENU, {
                            "address": record.get("address", ""), "interface": record.get("interface", context.wireguard.get("name", "")),
                            "disabled": record.get("disabled", "no"),
                        }))
                    else:
                        old_address = address_by_id[change.id].get("address", "")
                        self.client.set(ADDRESS_MENU, change.id, {"address": normalized_addresses[index]})
                        undo_actions.append(lambda item_id=change.id, value=old_address: self.client.set(ADDRESS_MENU, item_id, {"address": value}))
                if data.listen_port is not None and str(data.listen_port) != context.wireguard.get("listen-port", ""):
                    self.client.set(WG_MENU, context.wireguard.get(".id", ""), {"listen-port": data.listen_port})
                    undo_actions.append(lambda: self.client.set(WG_MENU, context.wireguard.get(".id", ""), {"listen-port": context.wireguard.get("listen-port", "")}))
                    for record in self._env_records(context, "AWG_REMOTE"):
                        self.client.set(ENV_MENU, record[".id"], {"value": self._replace_endpoint_port(record.get("value", ""), data.listen_port)})
                        undo_actions.append(lambda item_id=record[".id"], value=record.get("value", ""): self.client.set(ENV_MENU, item_id, {"value": value}))
                if data.regenerate_server_key:
                    private_key, public_key = generate_wireguard_key_pair()
                    self.client.set(WG_MENU, context.wireguard.get(".id", ""), {"private-key": private_key})
                    if context.wireguard.get("private-key"):
                        undo_actions.append(lambda: self.client.set(WG_MENU, context.wireguard.get(".id", ""), {"private-key": context.wireguard.get("private-key", "")}))
                    for record in self._env_records(context, "AWG_SERVER_PUB"):
                        self.client.set(ENV_MENU, record[".id"], {"value": public_key})
                        undo_actions.append(lambda item_id=record[".id"], value=record.get("value", ""): self.client.set(ENV_MENU, item_id, {"value": value}))
                if data.regenerate_header_protection_key:
                    header_key, _ = generate_wireguard_key_pair()
                    for record in self._env_records(context, "AWG_HEADER_PROTECTION_KEY"):
                        self.client.set(ENV_MENU, record[".id"], {"value": header_key})
                        undo_actions.append(lambda item_id=record[".id"], value=record.get("value", ""): self.client.set(ENV_MENU, item_id, {"value": value}))
                if was_running:
                    self.client.action(CONTAINER_MENU, "start", container_id)
                    self._wait_container(container_id, True, 30)
            except Exception as exc:
                start_failure = (
                    self._container_start_failure_log()
                    if isinstance(exc, AppError) and exc.code == "PROXY_RESTART_FAILED"
                    else ""
                )
                rollback_ok = True
                for undo in reversed(undo_actions):
                    try:
                        undo()
                    except Exception:
                        rollback_ok = False
                        LOGGER.exception("Failed to roll back an AWG server settings change")
                restored_running_state = not was_running
                if was_running:
                    try:
                        self.client.action(CONTAINER_MENU, "start", container_id)
                        self._wait_container(container_id, True, 30)
                        restored_running_state = True
                    except Exception:
                        rollback_ok = False
                        LOGGER.exception("Failed to restart AWG container after settings update error")
                if isinstance(exc, AppError) and exc.code == "PROXY_RESTART_FAILED" and rollback_ok and restored_running_state:
                    technical = "AWG container rejected the proposed settings; previous settings were restored."
                    if start_failure:
                        technical += f"\n\nLast failed start:\n{start_failure}"
                    else:
                        technical += f"\n\n{exc.technical}"
                    raise AppError(
                        "SERVER_SETTINGS_ROLLED_BACK",
                        "error.serverSettingsRolledBack",
                        technical=technical,
                        status_code=422,
                    ) from exc
                raise
            self._remember_success()
            return self.server_settings()

    def _env_records(self, context: ServerContext, key: str) -> list[dict[str, str]]:
        records = [item for item in context.env_records if item.get("key") == key]
        if not records:
            raise AppError("AWG_ENV_INVALID", "error.awgEnvInvalid", technical=f"{key} record not found", status_code=503)
        return records

    def _set_client_keys(self, context: ServerContext, keys: set[str]) -> None:
        value = ",".join(sorted(keys))
        for record in self._env_records(context, "AWG_CLIENT_PUBS"):
            self.client.set(ENV_MENU, record[".id"], {"value": value})

    @staticmethod
    def _container_running(container: dict[str, str]) -> bool:
        return bool_value(container.get("running", "")) or container.get("status", "").lower() == "running"

    def _restart_proxy(self, context: ServerContext) -> bool:
        container_id = context.container.get(".id", "")
        containers = self.client.print(CONTAINER_MENU, proplist=(".id", "status", "running"))
        current = next((item for item in containers if item.get(".id") == container_id), None)
        if not current:
            raise AppError(
                "AWG_CONTAINER_NOT_FOUND",
                "error.containerNotFound",
                technical="container disappeared before restart",
                status_code=503,
                hint_key="error.containerNotFoundHint",
            )
        last_status = current.get("status", "")
        if not self._container_running(current):
            LOGGER.error(
                "AWG_CONTAINER_STOPPED: configuration was updated but container %s was not restarted (status=%s)",
                self.settings.awg_container_name,
                last_status or "unknown",
            )
            return False

        self.client.action(CONTAINER_MENU, "stop", container_id)
        stop_deadline = time.monotonic() + 20
        while time.monotonic() < stop_deadline:
            containers = self.client.print(CONTAINER_MENU, proplist=(".id", "status", "running"))
            current = next((item for item in containers if item.get(".id") == container_id), None)
            if current:
                last_status = current.get("status", "")
                if not self._container_running(current):
                    break
            time.sleep(1)
        else:
            raise AppError(
                "PROXY_RESTART_FAILED",
                "error.proxyRestartFailed",
                technical=f"container did not stop; status: {last_status or 'unknown'}",
                status_code=502,
                hint_key="error.proxyRestartFailedHint",
            )

        self.client.action(CONTAINER_MENU, "start", container_id)
        start_deadline = time.monotonic() + 30
        while time.monotonic() < start_deadline:
            containers = self.client.print(CONTAINER_MENU, proplist=(".id", "status", "running"))
            current = next((item for item in containers if item.get(".id") == container_id), None)
            if current:
                last_status = current.get("status", "")
                if self._container_running(current):
                    return True
            time.sleep(1)
        raise AppError(
            "PROXY_RESTART_FAILED",
            "error.proxyRestartFailed",
            technical=f"container did not start; status: {last_status or 'unknown'}",
            status_code=502,
            hint_key="error.proxyRestartFailedHint",
        )

    @staticmethod
    def generate_client_keys() -> dict[str, str]:
        private_key, public_key = generate_wireguard_key_pair()
        return {
            "private_key": private_key,
            "public_key": public_key,
            "preshared_key": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        }

    def _validated_keys(
        self,
        public_key: str,
        store_private_key: bool,
        private_key: str,
        use_preshared_key: bool,
        preshared_key: str,
    ) -> tuple[str, str, str]:
        public_key = public_key.strip()
        decode_wireguard_key(public_key, "PublicKey")
        private_key = private_key.strip() if store_private_key else ""
        if private_key:
            decode_wireguard_key(private_key, "PrivateKey")
            if wireguard_public_key(private_key) != public_key:
                raise AppError(
                    "CLIENT_KEY_PAIR_MISMATCH",
                    "error.clientKeyPairMismatch",
                    technical="PublicKey does not match PrivateKey",
                    status_code=422,
                )
        elif store_private_key:
            raise AppError(
                "CLIENT_KEY_INVALID",
                "error.clientKeyInvalid",
                technical="PrivateKey is empty while private key storage is enabled",
                status_code=422,
            )
        preshared_key = preshared_key.strip() if use_preshared_key else ""
        if preshared_key:
            decode_wireguard_key(preshared_key, "PresharedKey")
        elif use_preshared_key:
            raise AppError(
                "CLIENT_KEY_INVALID",
                "error.clientKeyInvalid",
                technical="PresharedKey is empty while preshared key is enabled",
                status_code=422,
            )
        return public_key, private_key, preshared_key

    def _address_values(
        self,
        context: ServerContext,
        pool_cidr: str,
        mode: str,
        requested: str,
        current_peer: dict[str, str] | None = None,
    ) -> dict[str, str]:
        if mode == "keep":
            return {}
        pool = next((item for item in self.pools(context) if item["cidr"] == pool_cidr), None)
        if not pool:
            raise AppError("ADDRESS_POOL_INVALID", "error.addressPoolInvalid", technical=pool_cidr, status_code=422)
        network = ipaddress.ip_network(pool_cidr)
        if mode == "next":
            if not pool["available"]:
                raise AppError("ADDRESS_POOL_EXHAUSTED", "error.poolExhausted", technical=pool_cidr, status_code=409)
            address = ipaddress.ip_address(pool["free_address"])
        else:
            try:
                address = ipaddress.ip_interface(requested.strip()).ip
            except ValueError as exc:
                raise AppError("ADDRESS_INVALID", "error.addressInvalid", technical=requested, status_code=422) from exc
            if not isinstance(address, ipaddress.IPv4Address) or address not in network:
                raise AppError("ADDRESS_INVALID", "error.addressInvalid", technical=f"{address} not in {network}", status_code=422)
            if address in {network.network_address, network.broadcast_address}:
                raise AppError("ADDRESS_INVALID", "error.addressInvalid", technical=f"reserved address {address}", status_code=422)
            current_addresses: set[ipaddress._BaseAddress] = set()
            if current_peer:
                for field in ("allowed-address", "client-address"):
                    for value in split_values(current_peer.get(field, "")):
                        try:
                            current_addresses.add(ipaddress.ip_interface(value).ip)
                        except ValueError:
                            pass
            if address in self._used_addresses(context) and address not in current_addresses:
                raise AppError("ADDRESS_IN_USE", "error.addressInUse", technical=str(address), status_code=409)
        return {
            "allowed-address": f"{address}/32",
            "client-address": f"{address}/{network.prefixlen}",
        }

    def create_client(self, data: ClientCreate) -> dict[str, Any]:
        with self.operation_lock:
            context = self.load_context(validate=True)
            if data.name and any((peer.get("name") or "").lower() == data.name.lower() for peer in context.peers):
                raise AppError("CLIENT_NAME_EXISTS", "error.clientNameExists", technical=data.name, status_code=409)
            public_key, private_key, preshared_key = self._validated_keys(
                data.public_key,
                data.store_private_key,
                data.private_key,
                data.use_preshared_key,
                data.preshared_key,
            )
            values: dict[str, object] = {
                "interface": context.wireguard.get("name", ""),
                "comment": data.comment,
                "preshared-key": preshared_key or "none",
                "client-dns": data.dns,
                "client-allowed-address": data.allowed_ips,
                "client-keepalive": f"{data.keepalive}s",
                "responder": "yes",
            }
            if private_key:
                values["private-key"] = private_key
            else:
                values["private-key"] = "none"
                values["public-key"] = public_key
            if data.name:
                values["name"] = data.name
            values.update(self._address_values(context, data.pool, data.address_mode, data.address))
            peer_id = self.client.add(PEER_MENU, values)
            if not peer_id:
                refreshed = self.load_context(validate=False)
                peer = next((item for item in refreshed.peers if item.get("public-key") == public_key), None)
                peer_id = peer.get(".id", "") if peer else ""
            if not peer_id:
                raise AppError("CLIENT_CREATE_FAILED", "error.clientCreateFailed", technical="RouterOS returned no peer id", status_code=502)
            old_keys = set(split_values(context.env.get("AWG_CLIENT_PUBS", "")))
            try:
                refreshed = self.load_context(validate=False)
                peer = next((item for item in refreshed.peers if item.get(".id") == peer_id), None)
                if not peer or not peer.get("public-key"):
                    raise AppError("CLIENT_CREATE_FAILED", "error.clientCreateFailed", technical="created peer has no public key", status_code=502)
                new_keys = set(old_keys)
                new_keys.add(peer["public-key"])
                self._set_client_keys(refreshed, new_keys)
                self._restart_proxy(refreshed)
                LOGGER.info("client_added peer_id=%s name=%s", peer_id, data.name or "(RouterOS generated)")
                return self.client_config(peer_id)
            except Exception:
                try:
                    rollback_context = self.load_context(validate=False)
                    self._set_client_keys(rollback_context, old_keys)
                    self.client.remove(PEER_MENU, peer_id)
                    self._restart_proxy(rollback_context)
                    LOGGER.warning("client_add_rolled_back peer_id=%s", peer_id)
                except Exception as rollback_error:
                    LOGGER.exception("client_add_rollback_failed peer_id=%s error=%s", peer_id, rollback_error)
                raise

    def _find_peer(self, context: ServerContext, peer_id: str) -> dict[str, str]:
        peer = next((item for item in context.peers if item.get(".id") == peer_id), None)
        if not peer:
            raise AppError("CLIENT_NOT_FOUND", "error.clientNotFound", technical=peer_id, status_code=404)
        return peer

    def update_client(self, peer_id: str, data: ClientUpdate) -> dict[str, Any]:
        with self.operation_lock:
            context = self.load_context(validate=False)
            peer = self._find_peer(context, peer_id)
            if data.name and any(
                item.get(".id") != peer_id
                and (item.get("name") or "").lower() == data.name.lower()
                for item in context.peers
            ):
                raise AppError("CLIENT_NAME_EXISTS", "error.clientNameExists", technical=data.name, status_code=409)
            public_key, private_key, preshared_key = self._validated_keys(
                data.public_key,
                data.store_private_key,
                data.private_key,
                data.use_preshared_key,
                data.preshared_key,
            )
            _, disabled_at = clean_comment_metadata(peer.get("comment", ""))
            values: dict[str, object] = {
                "name": data.name,
                "comment": comment_with_disabled_at(data.comment, disabled_at or None),
                "preshared-key": preshared_key or "none",
                "client-dns": data.dns,
                "client-allowed-address": data.allowed_ips,
                "client-keepalive": f"{data.keepalive}s",
            }
            if private_key:
                values["private-key"] = private_key
            else:
                values["private-key"] = "none"
                values["public-key"] = public_key
            values.update(self._address_values(context, data.pool, data.address_mode, data.address, peer))
            old_values = {
                key: peer.get(key, "") or ("none" if key in {"private-key", "preshared-key"} else "")
                for key in values
            }
            if not peer.get("private-key", "") or peer.get("private-key") == MISSING_PRIVATE_KEY:
                old_values["private-key"] = "none"
                old_values["public-key"] = peer.get("public-key", "")
            old_public_key = peer.get("public-key", "")
            old_env_keys = set(split_values(context.env.get("AWG_CLIENT_PUBS", "")))
            self.client.set(PEER_MENU, peer_id, values)
            try:
                refreshed = self.load_context(validate=False)
                updated_peer = self._find_peer(refreshed, peer_id)
                actual_public_key = updated_peer.get("public-key", "")
                if not actual_public_key:
                    raise AppError(
                        "CLIENT_UPDATE_FAILED",
                        "error.clientUpdateFailed",
                        technical="updated peer has no public key",
                        status_code=502,
                    )
                if actual_public_key != old_public_key:
                    new_env_keys = set(old_env_keys)
                    new_env_keys.discard(old_public_key)
                    new_env_keys.add(actual_public_key)
                    self._set_client_keys(refreshed, new_env_keys)
                    self._restart_proxy(refreshed)
                LOGGER.info("client_updated peer_id=%s name=%s", peer_id, data.name or "(RouterOS generated)")
                return {"ok": True}
            except Exception:
                try:
                    rollback_context = self.load_context(validate=False)
                    self.client.set(PEER_MENU, peer_id, old_values)
                    self._set_client_keys(rollback_context, old_env_keys)
                    self._restart_proxy(rollback_context)
                    LOGGER.warning("client_update_rolled_back peer_id=%s", peer_id)
                except Exception as rollback_error:
                    LOGGER.exception("client_update_rollback_failed peer_id=%s error=%s", peer_id, rollback_error)
                raise

    def toggle_client(self, peer_id: str, disabled: bool) -> dict[str, Any]:
        with self.operation_lock:
            context = self.load_context(validate=False)
            peer = self._find_peer(context, peer_id)
            comment, _ = clean_comment_metadata(peer.get("comment", ""))
            disabled_at = datetime.now(timezone.utc).isoformat(timespec="seconds") if disabled else None
            self.client.set(
                PEER_MENU,
                peer_id,
                {"disabled": "yes" if disabled else "no", "comment": comment_with_disabled_at(comment, disabled_at)},
            )
            LOGGER.info("client_%s peer_id=%s", "disabled" if disabled else "enabled", peer_id)
            return {"ok": True, "disabled": disabled, "disabled_at": disabled_at or ""}

    def delete_client(self, peer_id: str) -> dict[str, Any]:
        with self.operation_lock:
            context = self.load_context(validate=True)
            peer = self._find_peer(context, peer_id)
            old_keys = set(split_values(context.env.get("AWG_CLIENT_PUBS", "")))
            new_keys = set(old_keys)
            new_keys.discard(peer.get("public-key", ""))
            self._set_client_keys(context, new_keys)
            try:
                self._restart_proxy(context)
            except Exception:
                self._set_client_keys(context, old_keys)
                try:
                    self._restart_proxy(context)
                except Exception as rollback_error:
                    LOGGER.exception("client_delete_rollback_failed peer_id=%s error=%s", peer_id, rollback_error)
                raise
            try:
                self.client.remove(PEER_MENU, peer_id)
            except Exception:
                self._set_client_keys(context, old_keys)
                try:
                    self._restart_proxy(context)
                except Exception as rollback_error:
                    LOGGER.exception("client_delete_remove_rollback_failed peer_id=%s error=%s", peer_id, rollback_error)
                raise
            LOGGER.info("client_deleted peer_id=%s", peer_id)
            return {"ok": True}

    def _private_key_from_client_config(self, peer_id: str) -> str:
        records = self.client.command(
            "/interface/wireguard/peers/show-client-config", attributes={"number": peer_id}
        )
        for record in records:
            for key in ("conf", "ret", "config"):
                if record.get(key):
                    return parse_conf(record[key]).get("Interface", {}).get("PrivateKey", "")
        raise AppError(
            "CLIENT_CONFIG_UNAVAILABLE",
            "error.clientConfigUnavailable",
            technical="show-client-config returned no conf field",
            status_code=502,
            hint_key="error.clientConfigUnavailableHint",
        )

    def client_details(self, peer_id: str) -> dict[str, Any]:
        context = self.load_context(validate=False)
        peer = self._find_peer(context, peer_id)
        private_key = peer.get("private-key", "") or self._private_key_from_client_config(peer_id)
        if private_key == MISSING_PRIVATE_KEY or private_key.lower() == "none":
            private_key = ""
        preshared_key = peer.get("preshared-key", "")
        if preshared_key.lower() == "none":
            preshared_key = ""
        return {
            **self._peer_payload(peer),
            "private_key": private_key,
            "store_private_key": bool(private_key),
            "preshared_key": preshared_key,
            "use_preshared_key": bool(preshared_key),
        }

    def client_config(self, peer_id: str) -> dict[str, Any]:
        context = self.load_context(validate=False)
        peer = self._find_peer(context, peer_id)
        private_key = self._private_key_from_client_config(peer_id)
        private_key_missing = not private_key or private_key == MISSING_PRIVATE_KEY
        config = build_awg_config(
            "" if private_key_missing else private_key,
            peer,
            context.wireguard,
            context.env,
            self.settings.awg_client_endpoint,
        )
        vpn_uri = make_vpn_uri(config, context.env, self.settings.awg_server_name)
        return {
            "id": peer_id,
            "name": peer.get("name", "client"),
            "config": config,
            "qr": qr_data_url(config),
            "vpn_uri": vpn_uri,
            "vpn_qr": qr_data_url(vpn_uri),
            "private_key_missing": private_key_missing,
        }

    def sync_preview(self) -> dict[str, Any]:
        context = self.load_context(validate=False)
        status = self.sync_status(context)
        return {**status, "target_keys": sorted(item.get("public-key", "") for item in context.peers if item.get("public-key"))}

    def sync_apply(self) -> dict[str, Any]:
        with self.operation_lock:
            context = self.load_context(validate=True)
            keys = {item.get("public-key", "") for item in context.peers if item.get("public-key")}
            self._set_client_keys(context, keys)
            self._restart_proxy(context)
            LOGGER.info("client_keys_synchronized count=%s", len(keys))
            return {"ok": True, "count": len(keys)}

