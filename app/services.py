from __future__ import annotations

import base64
import ipaddress
import io
import json
import logging
import re
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
from .models import ClientCreate, ClientUpdate
from .routeros import RouterOSClient
from .settings import Settings


LOGGER = logging.getLogger("awg-admin")

WG_MENU = "interface/wireguard"
PEER_MENU = "interface/wireguard/peers"
CONTAINER_MENU = "container"
ENV_MENU = "container/envs"
ADDRESS_MENU = "ip/address"

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

METADATA_RE = re.compile(r"\s*\[awg-admin:v1(?:;disabled_at=([^\]]+))?\]\s*$")


@dataclass
class ServerContext:
    container: dict[str, str]
    env_records: list[dict[str, str]]
    env: dict[str, str]
    wireguard: dict[str, str]
    addresses: list[dict[str, str]]
    peers: list[dict[str, str]]

    @property
    def envlist(self) -> str:
        return self.container.get("envlist", "")


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
    suffix = f"[awg-admin:v1;disabled_at={disabled_at}]"
    return f"{clean} {suffix}".strip()


def split_values(value: str) -> list[str]:
    return [item for item in re.split(r"[,\s]+", value or "") if item]


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


def build_awg_config(plain_config: str, env: dict[str, str], endpoint: str) -> str:
    parsed = parse_conf(plain_config)
    interface = dict(parsed.get("Interface", {}))
    peer = dict(parsed.get("Peer", {}))
    if not interface.get("PrivateKey"):
        raise AppError(
            "CLIENT_CONFIG_INVALID",
            "error.clientConfigInvalid",
            technical="RouterOS client config does not contain Interface.PrivateKey",
            status_code=502,
        )
    if not peer.get("PublicKey"):
        raise AppError(
            "CLIENT_CONFIG_INVALID",
            "error.clientConfigInvalid",
            technical="RouterOS client config does not contain Peer.PublicKey",
            status_code=502,
        )
    for env_key, config_key in AWG_ENV_TO_CONFIG.items():
        value = env.get(env_key, "").strip()
        if value:
            interface[config_key] = value
    peer["Endpoint"] = endpoint

    interface_order = [
        "PrivateKey",
        "Address",
        "DNS",
        "MTU",
        "ListenPort",
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
            if key in values and values[key] != "":
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
    def __init__(self, settings: Settings, client: RouterOSClient):
        self.settings = settings
        self.client = client
        self.operation_lock = threading.RLock()
        self.last_success: str = ""
        self.runtime_error: AppError | None = None

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
                proplist=(".id", "name", "comment", "status", "tag", "envlist", "interface", "running"),
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
                raise AppError(
                    "AWG_CONTAINER_NOT_FOUND",
                    "error.containerNotFound",
                    technical=f"container {self.settings.awg_container_name!r} was not found",
                    status_code=503,
                    hint_key="error.containerNotFoundHint",
                )
            status = container.get("status", "").lower()
            running = bool_value(container.get("running", "")) or status == "running"
            if validate and not running:
                raise AppError(
                    "AWG_CONTAINER_STOPPED",
                    "error.containerStopped",
                    technical=f"container status is {status or 'unknown'}",
                    status_code=503,
                    hint_key="error.containerStoppedHint",
                )
            envlist = container.get("envlist", "")
            if not envlist:
                raise AppError(
                    "AWG_ENVLIST_MISSING",
                    "error.envlistMissing",
                    technical="container has no envlist",
                    status_code=503,
                )
            env_records = [item for item in self.client.print(ENV_MENU, proplist=(".id", "list", "key", "value")) if item.get("list") == envlist]
            env = {item.get("key", ""): item.get("value", "") for item in env_records if item.get("key")}
            missing = sorted(BASE_REQUIRED_AWG_ENV - env.keys())
            if missing:
                raise AppError(
                    "AWG_ENV_INVALID",
                    "error.awgEnvInvalid",
                    technical="missing keys: " + ", ".join(missing),
                    status_code=503,
                    hint_key="error.awgEnvInvalidHint",
                )
            if env.get("AWG_MODE", "").lower() != "server":
                raise AppError(
                    "AWG_MODE_INVALID",
                    "error.awgModeInvalid",
                    technical=f"AWG_MODE={env.get('AWG_MODE', '')!r}",
                    status_code=503,
                )
            interfaces = self.client.print(
                WG_MENU,
                proplist=(".id", "name", "listen-port", "public-key", "disabled", "running", "mtu"),
            )
            matches = [item for item in interfaces if item.get("public-key") == env.get("AWG_SERVER_PUB")]
            if len(matches) != 1:
                raise AppError(
                    "WG_INTERFACE_NOT_FOUND",
                    "error.wgInterfaceNotFound",
                    technical=f"interfaces matching AWG_SERVER_PUB: {len(matches)}",
                    status_code=503,
                    hint_key="error.wgInterfaceNotFoundHint",
                )
            wireguard = matches[0]
            if bool_value(wireguard.get("disabled", "")):
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
            if not addresses:
                raise AppError(
                    "WG_ADDRESSES_MISSING",
                    "error.wgAddressesMissing",
                    technical=f"no addresses assigned to {wireguard.get('name')}",
                    status_code=503,
                )
            remote_port = env.get("AWG_REMOTE", "").rsplit(":", 1)[-1]
            if remote_port and wireguard.get("listen-port") and remote_port != wireguard.get("listen-port"):
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
                        "allowed-address",
                        "client-address",
                        "client-dns",
                        "client-allowed-address",
                        "client-keepalive",
                        "persistent-keepalive",
                        "current-endpoint-address",
                        "current-endpoint-port",
                        "last-handshake",
                        "rx",
                        "tx",
                    ),
                )
                if item.get("interface") == wireguard.get("name")
            ]
            context = ServerContext(container, env_records, env, wireguard, addresses, peers)
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
        self.load_context(validate=True)

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
        client_address = peer.get("client-address", "") or peer.get("allowed-address", "")
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
            "endpoint": ":".join(
                filter(None, (peer.get("current-endpoint-address", ""), peer.get("current-endpoint-port", "")))
            ),
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
                "container_name": self.settings.awg_container_name,
                "container_status": context.container.get("status", "unknown"),
                "wireguard_interface": context.wireguard.get("name", ""),
                "listen_port": context.wireguard.get("listen-port", ""),
                "client_endpoint": self.settings.awg_client_endpoint,
                "last_success": self.last_success,
            },
        }

    def server_settings(self) -> dict[str, Any]:
        context = self.load_context(validate=False)
        rows: list[dict[str, Any]] = [
            {"key": "Container", "label_key": "serverSettings.container", "value": self.settings.awg_container_name, "sensitive": False},
            {"key": "Container status", "label_key": "serverSettings.containerStatus", "value": context.container.get("status", "unknown"), "sensitive": False},
            {"key": "WireGuard interface", "label_key": "serverSettings.interface", "value": context.wireguard.get("name", ""), "sensitive": False},
            {"key": "WireGuard public key", "label_key": "serverSettings.publicKey", "value": context.wireguard.get("public-key", ""), "sensitive": False},
            {"key": "WireGuard listen port", "label_key": "serverSettings.listenPort", "value": context.wireguard.get("listen-port", ""), "sensitive": False},
            {"key": "Client endpoint", "label_key": "serverSettings.clientEndpoint", "value": self.settings.awg_client_endpoint, "sensitive": False},
            {"key": "Addresses", "label_key": "serverSettings.addresses", "value": ", ".join(item.get("address", "") for item in context.addresses), "sensitive": False},
        ]
        sensitive_keys = {"AWG_HEADER_PROTECTION_KEY"}
        for key in sorted(context.env):
            rows.append({"key": key, "label_key": "", "value": context.env[key], "sensitive": key in sensitive_keys})
        return {"settings": rows, "editable": False}

    def _env_record(self, context: ServerContext, key: str) -> dict[str, str]:
        record = next((item for item in context.env_records if item.get("key") == key), None)
        if not record:
            raise AppError("AWG_ENV_INVALID", "error.awgEnvInvalid", technical=f"{key} record not found", status_code=503)
        return record

    def _set_client_keys(self, context: ServerContext, keys: set[str]) -> None:
        record = self._env_record(context, "AWG_CLIENT_PUBS")
        self.client.set(ENV_MENU, record[".id"], {"value": ",".join(sorted(keys))})

    def _restart_proxy(self, context: ServerContext) -> None:
        container_id = context.container.get(".id", "")
        containers = self.client.print(CONTAINER_MENU, proplist=(".id", "status", "running"))
        current = next((item for item in containers if item.get(".id") == container_id), None)
        last_status = current.get("status", "") if current else "missing"
        already_stopped = current and not bool_value(current.get("running", "")) and last_status.lower() == "stopped"
        if not already_stopped:
            self.client.action(CONTAINER_MENU, "stop", container_id)
            stop_deadline = time.monotonic() + 20
            while time.monotonic() < stop_deadline:
                containers = self.client.print(CONTAINER_MENU, proplist=(".id", "status", "running"))
                current = next((item for item in containers if item.get(".id") == container_id), None)
                if current:
                    last_status = current.get("status", "")
                    if not bool_value(current.get("running", "")) and last_status.lower() == "stopped":
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
                if bool_value(current.get("running", "")) or last_status.lower() == "running":
                    return
            time.sleep(1)
        raise AppError(
            "PROXY_RESTART_FAILED",
            "error.proxyRestartFailed",
            technical=f"container did not start; status: {last_status or 'unknown'}",
            status_code=502,
            hint_key="error.proxyRestartFailedHint",
        )

    def create_client(self, data: ClientCreate) -> dict[str, Any]:
        with self.operation_lock:
            context = self.load_context(validate=True)
            pool = next((item for item in self.pools(context) if item["cidr"] == data.pool), None)
            if not pool or not pool["available"]:
                raise AppError("ADDRESS_POOL_EXHAUSTED", "error.poolExhausted", technical=data.pool, status_code=409)
            network = ipaddress.ip_network(data.pool)
            address = ipaddress.ip_address(pool["free_address"])
            if any((peer.get("name") or "").lower() == data.name.lower() for peer in context.peers):
                raise AppError("CLIENT_NAME_EXISTS", "error.clientNameExists", technical=data.name, status_code=409)
            values: dict[str, object] = {
                "interface": context.wireguard.get("name", ""),
                "name": data.name,
                "comment": data.comment,
                "private-key": "auto",
                "preshared-key": "auto" if data.use_preshared_key else "none",
                "allowed-address": f"{address}/32",
                "client-address": f"{address}/{network.prefixlen}",
                "client-dns": data.dns,
                "client-allowed-address": data.allowed_ips,
                "client-keepalive": f"{data.keepalive}s",
                "responder": "yes",
            }
            peer_id = self.client.add(PEER_MENU, values)
            if not peer_id:
                refreshed = self.load_context(validate=False)
                peer = next((item for item in refreshed.peers if item.get("name") == data.name), None)
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
                LOGGER.info("client_added peer_id=%s name=%s", peer_id, data.name)
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
            if any(
                item.get(".id") != peer_id
                and (item.get("name") or "").lower() == data.name.lower()
                for item in context.peers
            ):
                raise AppError("CLIENT_NAME_EXISTS", "error.clientNameExists", technical=data.name, status_code=409)
            _, disabled_at = clean_comment_metadata(peer.get("comment", ""))
            values: dict[str, object] = {
                "name": data.name,
                "comment": comment_with_disabled_at(data.comment, disabled_at or None),
                "client-dns": data.dns,
                "client-allowed-address": data.allowed_ips,
                "client-keepalive": f"{data.keepalive}s",
            }
            if data.psk_action == "generate":
                values["preshared-key"] = "auto"
            elif data.psk_action == "remove":
                values["preshared-key"] = "none"
            self.client.set(PEER_MENU, peer_id, values)
            LOGGER.info("client_updated peer_id=%s name=%s psk_action=%s", peer_id, data.name, data.psk_action)
            return {"ok": True}

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

    def _plain_client_config(self, peer_id: str) -> str:
        records = self.client.command(
            "/interface/wireguard/peers/show-client-config", attributes={"number": peer_id}
        )
        for record in records:
            for key in ("conf", "ret", "config"):
                if record.get(key):
                    return record[key]
        raise AppError(
            "CLIENT_CONFIG_UNAVAILABLE",
            "error.clientConfigUnavailable",
            technical="show-client-config returned no conf field",
            status_code=502,
            hint_key="error.clientConfigUnavailableHint",
        )

    def client_config(self, peer_id: str) -> dict[str, Any]:
        context = self.load_context(validate=False)
        peer = self._find_peer(context, peer_id)
        plain = self._plain_client_config(peer_id)
        config = build_awg_config(plain, context.env, self.settings.awg_client_endpoint)
        vpn_uri = make_vpn_uri(config, context.env, self.settings.awg_server_name)
        return {
            "id": peer_id,
            "name": peer.get("name", "client"),
            "config": config,
            "qr": qr_data_url(config),
            "vpn_uri": vpn_uri,
            "vpn_qr": qr_data_url(vpn_uri),
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

