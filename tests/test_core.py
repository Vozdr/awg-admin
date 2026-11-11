from __future__ import annotations

import base64
import ipaddress
import json
import os
import subprocess
import sys
import struct
import zlib

import pytest

import app.awg_docs as awg_docs_module
from app.errors import AppError, redact
from app.awg_docs import AwgEnvironmentDocs, parse_environment_document
from app.registry import ServerRegistry
from app.routeros import decode_length, encode_length, parse_sentence
from app.services import (
    AwgAdminService,
    build_awg_config,
    clean_comment_metadata,
    comment_with_disabled_at,
    configuration_filename,
    extract_container_start_failure,
    generate_wireguard_key_pair,
    make_vpn_uri,
    parse_conf,
    qr_data_url,
    wireguard_public_key,
)
from app.settings import Settings, SettingsError
from app.models import ServerSettingsUpdate


class FakeRouterOS:
    def __init__(self):
        self.env = {
            "AWG_MODE": "server", "AWG_LISTEN": "0.0.0.0:51820", "AWG_REMOTE": "0.0.0.0:51820",
            "AWG_JC": "7", "AWG_JMIN": "10", "AWG_JMAX": "50", "AWG_S1": "20", "AWG_S2": "30",
            "AWG_H1": "1", "AWG_H2": "2", "AWG_H3": "3", "AWG_H4": "4",
            "AWG_SERVER_PUB": "server-public", "AWG_CLIENT_PUB": "legacy-fallback", "AWG_CLIENT_PUBS": "client-public",
            "AWG_HEADER_PROTECTION_KEY": "visible-header-protection-key",
        }
    def print(self, menu, proplist=None):
        if menu == "system/resource":
            return [{"version": "7.22", "board-name": "CHR", "architecture-name": "x86_64"}]
        if menu == "container":
            return [{".id": "*1", "name": "wg-awg-server-1", "status": "running", "running": "true", "envlists": "awg-base,awg-clients"}]
        if menu == "container/envs":
            return [
                {".id": f"*e{index}", "list": "awg-clients" if key == "AWG_CLIENT_PUBS" else "awg-base", "key": key, "value": value}
                for index, (key, value) in enumerate(self.env.items())
            ]
        if menu == "interface/wireguard":
            return [{".id": "*2", "name": "wg-awg", "listen-port": "51820", "public-key": "server-public", "disabled": "false", "running": "true"}]
        if menu == "ip/address":
            return [{".id": "*3", "address": "192.168.177.1/24", "network": "192.168.177.0", "interface": "wg-awg", "disabled": "false"}]
        if menu == "interface/wireguard/peers":
            return [{".id": "*4", "name": "Alice", "comment": "Phone", "interface": "wg-awg", "disabled": "false", "public-key": "client-public", "allowed-address": "192.168.177.2/32", "client-address": "192.168.177.2/24", "rx": "1024", "tx": "2048"}]
        raise AssertionError(menu)


def test_configuration_filename_uses_server_and_client_names():
    assert configuration_filename("AWG Home", "peer-phone") == "AWG Home+peer-phone.conf"
    assert configuration_filename('Домашний/AWG:*?', 'Телефон+Иван/1') == "Домашний_AWG+Телефон_Иван_1.conf"
    assert configuration_filename("***", "///") == "server+client.conf"


def test_parse_awg_environment_document_expands_ranges_and_required_notes():
    document = """
| Переменная | Обязательная | По умолчанию | Описание |
|---|---|---|---|
| `AWG_LISTEN` | Да | -- | Адрес прослушивания |
| `AWG_REMOTE` | Да | -- | Адрес сервера |
| `AWG_MODE` | Нет | `normal` | Режим работы |
| `AWG_SERVER_PUB` | Да | -- | Публичный ключ |
| `AWG_H1`--`AWG_H4` | Да | -- | Типы сообщений |
| `AWG_CLIENT_PUB` | Да* | -- | Ключ клиента |
| `AWG_FB2_*`, `AWG_FB3_*` | Нет | `0` | Резервные профили |

`*` В server режиме должен быть задан один из вариантов ключа.
"""

    parsed = parse_environment_document(document)

    assert all(f"AWG_H{index}" in parsed for index in range(1, 5))
    assert parsed["AWG_MODE"]["default"] == "normal"
    assert parsed["AWG_LISTEN"]["default"] is None
    assert parsed["AWG_CLIENT_PUB"]["required"] is True
    assert "server режиме" in parsed["AWG_CLIENT_PUB"]["required_note"]
    assert "AWG_FB2_*" in parsed


def test_awg_environment_docs_keep_bundled_copy_when_refresh_fails(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle"
    cache = tmp_path / "cache"
    bundle.mkdir()
    keys = ["AWG_LISTEN", "AWG_REMOTE", "AWG_MODE", "AWG_SERVER_PUB"] + [f"AWG_TEST_{index}" for index in range(25)]
    table = "| Variable | Required | Default | Description |\n|---|---|---|---|\n" + "\n".join(
        f"| `{key}` | {'Yes' if key in {'AWG_LISTEN', 'AWG_REMOTE', 'AWG_SERVER_PUB'} else 'No'} | -- | Description for {key} |"
        for key in keys
    )
    (bundle / "env.ru.md").write_text(table, encoding="utf-8")
    (bundle / "env.en.md").write_text(table, encoding="utf-8")
    monkeypatch.setattr(awg_docs_module, "BUNDLED_DIR", bundle)
    monkeypatch.setattr(awg_docs_module, "CACHE_DIR", cache)
    docs = AwgEnvironmentDocs()
    monkeypatch.setattr(docs, "_download", lambda _url: (_ for _ in ()).throw(OSError("offline")))

    docs.refresh()

    info = docs.info()
    assert info["sources"] == {"ru": "bundled", "en": "bundled"}
    assert len(info["catalog"]["ru"]) == len(keys)


def test_extract_container_start_failure_returns_last_safe_error_block():
    records = [
        {"time": "07:55:00", "message": "AWG-Server-v3 *** start"},
        {"time": "07:55:00", "message": "AWG-Server-v3 FATAL: old failure"},
        {"time": "07:55:00", "message": "AWG-Server-v3 *** error: exited with status 1"},
        {"time": "07:56:22", "message": "AWG-Server-v3 *** start"},
        {"time": "07:56:22", "message": "AWG-Server-v3 *** started PATH=/usr/bin AWG_SECRET=hidden /awg-proxy"},
        {"time": "07:56:22", "message": "AWG-Server-v3:: FATAL: AWG_S4: must be >= 12"},
        {"time": "07:56:22", "message": "AWG-Server-v3 *** error: exited with status 1"},
    ]

    result = extract_container_start_failure(records, "AWG-Server-v3")

    assert "AWG_S4: must be >= 12" in result
    assert "exited with status 1" in result
    assert "old failure" not in result
    assert "PATH=" not in result


class MutableRouterOS(FakeRouterOS):
    def __init__(self):
        super().__init__()
        self.envlists = ["awg-base", "awg-clients"]
        self.env_records = [
            {".id": f"*e{index}", "list": "awg-clients" if key == "AWG_CLIENT_PUBS" else "awg-base", "key": key, "value": value, "comment": "", "disabled": "no"}
            for index, (key, value) in enumerate(self.env.items())
        ]
        self.addresses = [{".id": "*3", "address": "192.168.177.1/24", "network": "192.168.177.0", "interface": "wg-awg", "disabled": "false"}]
        self.wireguard = {".id": "*2", "name": "wg-awg", "listen-port": "51820", "public-key": "server-public", "private-key": "", "disabled": "false", "running": "true"}
        self.container_status = "running"
        self.next_id = 100

    def print(self, menu, proplist=None):
        if menu == "container":
            return [{".id": "*1", "name": "wg-awg-server-1", "status": self.container_status, "running": str(self.container_status == "running").lower(), "envlists": ",".join(self.envlists)}]
        if menu == "container/envs":
            return [dict(item) for item in self.env_records]
        if menu == "interface/wireguard":
            return [dict(self.wireguard)]
        if menu == "ip/address":
            return [dict(item) for item in self.addresses]
        return super().print(menu, proplist)

    def set(self, menu, item_id, values):
        if menu == "container":
            self.envlists = [item for item in str(values["envlists"]).split(",") if item]
            return
        if menu == "container/envs":
            next(item for item in self.env_records if item[".id"] == item_id).update({key: str(value) for key, value in values.items()})
            return
        if menu == "interface/wireguard":
            if "private-key" in values:
                self.wireguard["private-key"] = str(values["private-key"])
                self.wireguard["public-key"] = wireguard_public_key(str(values["private-key"]))
            if "listen-port" in values:
                self.wireguard["listen-port"] = str(values["listen-port"])
            return
        if menu == "ip/address":
            next(item for item in self.addresses if item[".id"] == item_id).update({key: str(value) for key, value in values.items()})
            return
        raise AssertionError(menu)

    def add(self, menu, values):
        self.next_id += 1
        item_id = f"*n{self.next_id}"
        record = {".id": item_id, **{key: str(value) for key, value in values.items()}}
        if menu == "container/envs":
            self.env_records.append(record)
        elif menu == "ip/address":
            record.setdefault("disabled", "no")
            self.addresses.append(record)
        else:
            raise AssertionError(menu)
        return item_id

    def remove(self, menu, item_id):
        target = self.env_records if menu == "container/envs" else self.addresses if menu == "ip/address" else None
        if target is None:
            raise AssertionError(menu)
        target[:] = [item for item in target if item[".id"] != item_id]

    def action(self, menu, action, item_id):
        assert menu == "container"
        self.container_status = "stopped" if action == "stop" else "running"
        return []


class FailingSettingsRouterOS(MutableRouterOS):
    def add(self, menu, values):
        if menu == "ip/address":
            raise AppError("MIKROTIK_API_ERROR", "error.mikrotikApiError", technical="simulated failure", status_code=502)
        return super().add(menu, values)


class FakeContainerStateRouterOS(FakeRouterOS):
    def __init__(self, status="stopped"):
        super().__init__()
        self.status = status
        self.actions = []

    def print(self, menu, proplist=None):
        if menu == "container":
            return [{
                ".id": "*1",
                "name": "wg-awg-server-1",
                "status": self.status,
                "running": "true" if self.status == "running" else "false",
                "envlists": "awg-base,awg-clients",
            }]
        return super().print(menu, proplist)

    def action(self, menu, action, item_id):
        self.actions.append((menu, action, item_id))
        self.status = "stopped" if action == "stop" else "running"
        return []


class FakeCustomContainerNameRouterOS(FakeRouterOS):
    def print(self, menu, proplist=None):
        if menu == "container":
            return [{
                ".id": "*1",
                "name": "custom-awg",
                "status": "running",
                "running": "true",
                "envlists": "awg-base,awg-clients",
            }]
        return super().print(menu, proplist)


class FakeClientConfigRouterOS(FakeRouterOS):
    def __init__(self, private_key="client-private"):
        super().__init__()
        self.private_key = private_key

    def command(self, path, attributes=None):
        assert path == "/interface/wireguard/peers/show-client-config"
        return [{"conf": f"""[Interface]
PrivateKey = {self.private_key}
Address = 192.0.2.99/24
ListenPort = 51820

[Peer]
PublicKey = incorrect-server-key
"""}]


@pytest.mark.parametrize("value", [0, 1, 127, 128, 16383, 16384, 0x1FFFFF, 0x200000, 0x0FFFFFFF, 0x10000000])
def test_routeros_length_roundtrip(value):
    payload = bytearray(encode_length(value))

    def read_exact(size):
        result = bytes(payload[:size])
        del payload[:size]
        return result

    assert decode_length(read_exact) == value
    assert not payload


def test_parse_sentence():
    assert parse_sentence(["!re", "=.id=*1", "=name=alice", "=comment=a=b"]) == (
        "!re",
        {".id": "*1", "name": "alice", "comment": "a=b"},
    )


def test_first_free_address_skips_used_without_scanning_whole_network():
    network = ipaddress.ip_network("10.0.0.0/8")
    used = {ipaddress.ip_address("10.0.0.1"), ipaddress.ip_address("10.0.0.2"), ipaddress.ip_address("192.168.1.1")}
    assert AwgAdminService.first_free_address(network, used) == ipaddress.ip_address("10.0.0.3")


def test_comment_metadata_roundtrip():
    value = comment_with_disabled_at("Office laptop", "2026-11-09T10:30:00+00:00")
    assert clean_comment_metadata(value) == ("Office laptop", "2026-11-09T10:30:00+00:00")
    assert "[awgm-admin:v1;" in value
    legacy = "Office laptop [awg-admin:v1;disabled_at=2026-11-09T10:30:00+00:00]"
    assert clean_comment_metadata(legacy) == ("Office laptop", "2026-11-09T10:30:00+00:00")
    assert comment_with_disabled_at(value, None) == "Office laptop"


def sample_plain_config():
    return """[Interface]
PrivateKey = client-private
Address = 192.168.177.2/24
DNS = 1.1.1.1, 8.8.8.8

[Peer]
PublicKey = server-public
PresharedKey = shared-secret
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 30
"""


def sample_env():
    return {"AWG_JC": "7", "AWG_JMIN": "10", "AWG_JMAX": "50", "AWG_S1": "20", "AWG_S2": "30", "AWG_H1": "1", "AWG_H2": "2", "AWG_H3": "3", "AWG_H4": "4"}


def test_build_config_and_vpn_uri_are_recoverable():
    config = build_awg_config(
        "client-private",
        {
            "client-address": "10.8.1.3/32",
            "client-dns": "1.1.1.1, 8.8.8.8",
            "client-allowed-address": "0.0.0.0/0, ::/0",
            "client-keepalive": "30s",
            "preshared-key": "shared-secret",
        },
        {"public-key": "server-public", "mtu": "1420"},
        sample_env(),
        "vpn.example.org:51820",
    )
    parsed = parse_conf(config)
    assert parsed["Interface"]["Jc"] == "7"
    assert parsed["Interface"]["Address"] == "10.8.1.3/32"
    assert "ListenPort" not in parsed["Interface"]
    assert parsed["Peer"]["Endpoint"] == "vpn.example.org:51820"
    assert parsed["Peer"]["PresharedKey"] == "shared-secret"

    uri = make_vpn_uri(config, sample_env(), "My AWG")
    encoded = uri.removeprefix("vpn://")
    packed = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    expected_size = struct.unpack(">I", packed[:4])[0]
    outer_raw = zlib.decompress(packed[4:])
    assert len(outer_raw) == expected_size
    outer = json.loads(outer_raw)
    inner = json.loads(outer["containers"][0]["awg"]["last_config"])
    assert outer["description"] == "My AWG"
    assert inner["client_priv_key"] == "client-private"
    assert inner["psk_key"] == "shared-secret"
    assert inner["port"] == 51820


def test_generated_wireguard_key_pair_is_valid():
    private_key, public_key = generate_wireguard_key_pair()
    assert len(base64.b64decode(private_key)) == 32
    assert len(base64.b64decode(public_key)) == 32


def test_x25519_public_key_matches_rfc_7748_vector():
    private_key = base64.b64encode(bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")).decode()
    expected = bytes.fromhex("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
    assert base64.b64decode(wireguard_public_key(private_key)) == expected


def test_svg_qr_does_not_require_pillow():
    data = qr_data_url("hello")
    assert data.startswith("data:image/svg+xml;base64,")
    assert b"<svg" in base64.b64decode(data.split(",", 1)[1])


def test_redaction():
    value = redact("PrivateKey = topsecret, password=hunter2 dckr_pat_abc123")
    assert "topsecret" not in value
    assert "hunter2" not in value
    assert "dckr_pat" not in value


def test_settings_defaults_and_validation(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "192.0.2.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    settings = Settings.from_env()
    assert settings.mikrotik_api_port == 8728
    assert settings.admin_username == "admin"
    assert settings.admin_password == "admin"
    assert settings.awg_container_name == "wg-awg-server-1"
    assert settings.awg_server_name == "wg-awg-server-1"
    assert settings.client_default_dns == "1.1.1.1,8.8.8.8"
    assert settings.log_level == "ERROR"
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "invalid")
    with pytest.raises(SettingsError):
        Settings.from_env()


def test_server_name_defaults_to_matching_container_name(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "192.0.2.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("AWG_CONTAINER_NAME", "home-awg")
    monkeypatch.setenv("AWG_SERVER_NAME", "   ")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")

    settings = Settings.from_env()

    assert settings.awg_server_name == "home-awg"


def test_settings_discovers_independent_suffixed_servers(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "192.0.2.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "primary")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "primary-secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "one.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("MIKROTIK_HOST_3", "192.0.2.3")
    monkeypatch.setenv("MIKROTIK_USERNAME_3", "third")
    monkeypatch.setenv("MIKROTIK_PASSWORD_3", "third-secret")
    monkeypatch.setenv("MIKROTIK_API_SSL_3", "yes")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT_3", "three.example.org:51821")
    monkeypatch.setenv("AWG_CONTAINER_NAME_3", "awg-three")
    monkeypatch.setenv("AWG_SERVER_NAME_3", "Branch office")
    monkeypatch.setenv("CLIENT_DEFAULT_DNS_3", "9.9.9.9")

    settings = Settings.from_env()

    assert [server.id for server in settings.servers] == ["1", "3"]
    assert settings.servers[1].mikrotik_api_port == 8729
    assert settings.servers[1].awg_container_name == "awg-three"
    assert settings.servers[1].awg_server_name == "Branch office"
    assert settings.servers[1].display_name == "Branch office"
    assert settings.servers[1].client_default_dns == "9.9.9.9"


def test_incomplete_suffixed_server_is_a_configuration_error(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "192.0.2.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "primary")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "primary-secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "one.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("MIKROTIK_HOST_2", "192.0.2.2")

    with pytest.raises(SettingsError, match="MIKROTIK_USERNAME_2"):
        Settings.from_env()


def test_registry_keeps_healthy_server_available_when_another_fails(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "192.0.2.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "primary")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "primary-secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "one.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("MIKROTIK_HOST_2", "192.0.2.2")
    monkeypatch.setenv("MIKROTIK_USERNAME_2", "secondary")
    monkeypatch.setenv("MIKROTIK_PASSWORD_2", "secondary-secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT_2", "two.example.org:51820")
    registry = ServerRegistry(Settings.from_env())
    failure = AppError("AWG_CONTAINER_NOT_FOUND", "error.containerNotFound", status_code=503)

    registry.runtimes["1"].service.startup_validate = lambda: None
    registry.runtimes["2"].service.startup_validate = lambda: (_ for _ in ()).throw(failure)
    registry.validate_startup()

    summaries = {item["id"]: item for item in registry.list()["servers"]}
    assert summaries["1"]["available"] is True
    assert summaries["2"]["available"] is False
    assert summaries["2"]["error"]["code"] == "AWG_CONTAINER_NOT_FOUND"


def test_session_secret_is_required_only_when_auth_is_enabled(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "192.0.2.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("AUTH_DISABLE", raising=False)
    with pytest.raises(SettingsError, match="SESSION_SECRET"):
        Settings.from_env()
    monkeypatch.setenv("AUTH_DISABLE", "yes")
    assert Settings.from_env().auth_disable is True


def test_service_discovers_proxy_interface_and_address_pool(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    service = AwgAdminService(Settings.from_env().primary_server, FakeRouterOS())
    service.startup_validate()
    dashboard = service.dashboard()
    assert service.container_running is True
    assert dashboard["server"]["wireguard_interface"] == "wg-awg"
    assert dashboard["pools"] == [{"cidr": "192.168.177.0/24", "server_address": "192.168.177.1", "free_address": "192.168.177.3", "available": True}]
    assert dashboard["clients"][0]["name"] == "Alice"
    assert dashboard["clients"][0]["rx_human"] == "1.0 KB"
    assert dashboard["sync"]["ok"] is True


def test_service_combines_multiple_container_envlists(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    service = AwgAdminService(Settings.from_env().primary_server, FakeRouterOS())
    context = service.load_context()
    assert context.envlists == ["awg-base", "awg-clients"]
    assert context.env["AWG_MODE"] == "server"
    assert context.env["AWG_CLIENT_PUBS"] == "client-public"


def test_server_settings_derive_status_and_show_addresses_and_keys(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    service = AwgAdminService(Settings.from_env().primary_server, FakeRouterOS())
    result = service.server_settings()
    by_key = {item["key"]: item for item in result["settings"]}
    by_env_key = {item["key"]: item for item in result["environment"]}

    assert by_key["Container status"]["value_key"] == "server.running"
    assert by_key["Occupied addresses"]["value"] == "192.168.177.2/32"
    assert by_env_key["AWG_HEADER_PROTECTION_KEY"]["value"] == "visible-header-protection-key"
    assert "sensitive" not in by_env_key["AWG_HEADER_PROTECTION_KEY"]
    assert by_env_key["AWG_HEADER_PROTECTION_KEY"]["read_only"] is True
    assert by_env_key["AWG_HEADER_PROTECTION_KEY"]["special_action"] == "regenerate-header-key"
    assert by_env_key["AWG_SERVER_PUB"]["description_key"] == "serverSettings.env.serverPublicKey"
    assert by_env_key["AWG_CLIENT_PUB"]["managed"] is True
    assert by_env_key["AWG_CLIENT_PUBS"]["managed"] is True
    assert len(result["revision"]) == 64


def test_server_settings_reject_existing_unassigned_envlist(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    router = MutableRouterOS()
    router.env_records.append({".id": "*foreign", "list": "other-container", "key": "FOREIGN", "value": "1", "comment": "", "disabled": "no"})
    service = AwgAdminService(Settings.from_env().primary_server, router)

    with pytest.raises(AppError) as raised:
        service.validate_env_variable("other-container", "NEW_KEY")

    assert raised.value.code == "ENVLIST_NOT_ASSIGNED"
    assert raised.value.message_key == "error.envlistNotAssignedVariable"
    assert raised.value.payload()["error"]["message_args"] == {"key": "NEW_KEY", "envlist": "other-container"}
    assert service.validate_env_variable("new-unique-list", "NEW_KEY")["new_envlist"] is True


def test_server_settings_reject_duplicate_variable_in_same_envlist(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    service = AwgAdminService(Settings.from_env().primary_server, MutableRouterOS())

    with pytest.raises(AppError) as raised:
        service.validate_env_variable("awg-base", "AWG_JC")

    assert raised.value.code == "ENV_VARIABLE_DUPLICATE"
    assert raised.value.payload()["error"]["message_args"] == {"key": "AWG_JC", "envlist": "awg-base"}


def test_server_settings_reject_duplicate_staged_additions(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    router = MutableRouterOS()
    service = AwgAdminService(Settings.from_env().primary_server, router)
    current = service.server_settings()
    payload = ServerSettingsUpdate.parse_obj({
        "revision": current["revision"],
        "env_changes": [
            {"action": "add", "envlist": "new-list", "key": "NEW_KEY", "value": "one"},
            {"action": "add", "envlist": "new-list", "key": "NEW_KEY", "value": "two"},
        ],
    })

    with pytest.raises(AppError) as raised:
        service.update_server_settings(payload)

    assert raised.value.code == "ENV_VARIABLE_DUPLICATE"
    assert not any(item["key"] == "NEW_KEY" for item in router.env_records)
    assert router.container_status == "running"


def test_server_settings_apply_staged_changes_and_restart(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    router = MutableRouterOS()
    service = AwgAdminService(Settings.from_env().primary_server, router)
    current = service.server_settings()
    jc = next(item for item in current["environment"] if item["key"] == "AWG_JC")
    payload = ServerSettingsUpdate.parse_obj({
        "revision": current["revision"],
        "listen_port": 51821,
        "env_changes": [
            {"action": "update", "id": jc["id"], "envlist": jc["envlist"], "key": jc["key"], "value": "9", "comment": "custom junk count", "disabled": False},
            {"action": "add", "envlist": "awg-extra", "key": "AWG_EXTRA", "value": "enabled", "comment": "custom option", "disabled": False},
        ],
        "address_changes": [{"action": "update", "id": "*3", "address": "192.168.178.1/24"}],
    })

    result = service.update_server_settings(payload)

    assert router.container_status == "running"
    assert "awg-extra" in router.envlists
    assert router.wireguard["listen-port"] == "51821"
    assert next(item for item in router.env_records if item["key"] == "AWG_REMOTE")["value"].endswith(":51821")
    assert next(item for item in router.env_records if item["key"] == "AWG_JC")["comment"] == "custom junk count"
    assert next(item for item in router.env_records if item["key"] == "AWG_EXTRA")["list"] == "awg-extra"
    assert router.addresses[0]["address"] == "192.168.178.1/24"
    assert result["revision"] != current["revision"]


def test_adding_environment_variable_requires_value():
    with pytest.raises(ValueError):
        ServerSettingsUpdate.parse_obj({
            "revision": "0" * 64,
            "env_changes": [{"action": "add", "envlist": "awg-extra", "key": "AWG_EXTRA", "value": ""}],
        })


def test_server_settings_remove_empty_envlist_from_container(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    router = MutableRouterOS()
    router.envlists.append("awg-extra")
    router.env_records.append({".id": "*extra", "list": "awg-extra", "key": "AWG_EXTRA", "value": "1", "comment": "", "disabled": "no"})
    service = AwgAdminService(Settings.from_env().primary_server, router)
    current = service.server_settings()
    extra = next(item for item in current["environment"] if item["id"] == "*extra")

    service.update_server_settings(ServerSettingsUpdate.parse_obj({
        "revision": current["revision"],
        "env_changes": [{"action": "delete", "id": extra["id"], "envlist": extra["envlist"], "key": extra["key"]}],
    }))

    assert "awg-extra" not in router.envlists
    assert not any(item.get("list") == "awg-extra" for item in router.env_records)


def test_server_settings_do_not_start_stopped_container(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    router = MutableRouterOS()
    router.container_status = "stopped"
    service = AwgAdminService(Settings.from_env().primary_server, router)
    current = service.server_settings()
    row = next(item for item in current["environment"] if item["key"] == "AWG_JC")

    service.update_server_settings(ServerSettingsUpdate.parse_obj({
        "revision": current["revision"],
        "env_changes": [{"action": "update", "id": row["id"], "envlist": row["envlist"], "key": row["key"], "value": "8", "comment": "", "disabled": False}],
    }))

    assert router.container_status == "stopped"
    assert next(item for item in router.env_records if item["key"] == "AWG_JC")["value"] == "8"


def test_server_settings_conflict_and_rollback(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    router = FailingSettingsRouterOS()
    service = AwgAdminService(Settings.from_env().primary_server, router)
    current = service.server_settings()
    row = next(item for item in current["environment"] if item["key"] == "AWG_JC")
    router.env_records[0]["comment"] = "external change"
    with pytest.raises(AppError) as conflict:
        service.update_server_settings(ServerSettingsUpdate.parse_obj({"revision": current["revision"], "listen_port": 51822}))
    assert conflict.value.code == "SERVER_SETTINGS_CONFLICT"

    current = service.server_settings()
    row = next(item for item in current["environment"] if item["key"] == "AWG_JC")
    with pytest.raises(AppError) as failure:
        service.update_server_settings(ServerSettingsUpdate.parse_obj({
            "revision": current["revision"],
            "env_changes": [{"action": "update", "id": row["id"], "envlist": row["envlist"], "key": row["key"], "value": "99", "comment": "", "disabled": False}],
            "address_changes": [{"action": "add", "address": "10.10.10.1/24"}],
        }))
    assert failure.value.code == "MIKROTIK_API_ERROR"
    assert next(item for item in router.env_records if item["key"] == "AWG_JC")["value"] == "7"
    assert router.container_status == "running"


def test_failed_new_settings_report_successful_rollback(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    router = MutableRouterOS()
    service = AwgAdminService(Settings.from_env().primary_server, router)
    current = service.server_settings()
    row = next(item for item in current["environment"] if item["key"] == "AWG_JC")
    start_attempts = 0

    def wait_container(_container_id, running, _timeout):
        nonlocal start_attempts
        if not running:
            return
        start_attempts += 1
        if start_attempts == 1:
            raise AppError(
                "PROXY_RESTART_FAILED",
                "error.proxyRestartFailed",
                technical="container status=error",
                status_code=502,
            )

    monkeypatch.setattr(service, "_wait_container", wait_container)
    monkeypatch.setattr(service, "_container_start_failure_log", lambda: "07:56:22 FATAL: invalid AWG setting")

    with pytest.raises(AppError) as raised:
        service.update_server_settings(ServerSettingsUpdate.parse_obj({
            "revision": current["revision"],
            "env_changes": [{"action": "update", "id": row["id"], "envlist": row["envlist"], "key": row["key"], "value": "999", "comment": "", "disabled": False}],
        }))

    assert raised.value.code == "SERVER_SETTINGS_ROLLED_BACK"
    assert "invalid AWG setting" in raised.value.technical
    assert next(item for item in router.env_records if item["key"] == "AWG_JC")["value"] == "7"
    assert router.container_status == "running"


def test_server_key_regeneration_updates_interface_and_environment(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    router = MutableRouterOS()
    service = AwgAdminService(Settings.from_env().primary_server, router)
    current = service.server_settings()

    service.update_server_settings(ServerSettingsUpdate.parse_obj({
        "revision": current["revision"],
        "regenerate_server_key": True,
    }))

    env_public = next(item for item in router.env_records if item["key"] == "AWG_SERVER_PUB")["value"]
    assert env_public == router.wireguard["public-key"]
    assert env_public != "server-public"
    assert len(base64.b64decode(router.wireguard["private-key"])) == 32


def test_header_protection_key_regeneration_updates_environment(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    router = MutableRouterOS()
    service = AwgAdminService(Settings.from_env().primary_server, router)
    current = service.server_settings()

    service.update_server_settings(ServerSettingsUpdate.parse_obj({
        "revision": current["revision"],
        "regenerate_header_protection_key": True,
    }))

    value = next(item for item in router.env_records if item["key"] == "AWG_HEADER_PROTECTION_KEY")["value"]
    assert value != "visible-header-protection-key"
    assert len(base64.b64decode(value)) == 32


def test_service_uses_only_private_key_from_show_client_config(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    result = AwgAdminService(Settings.from_env().primary_server, FakeClientConfigRouterOS()).client_config("*4")
    parsed = parse_conf(result["config"])
    assert parsed["Interface"]["PrivateKey"] == "client-private"
    assert parsed["Interface"]["Address"] == "192.168.177.2/32"
    assert "ListenPort" not in parsed["Interface"]
    assert parsed["Peer"]["PublicKey"] == "server-public"


def test_missing_private_key_produces_warning_and_empty_config_field(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    service = AwgAdminService(Settings.from_env().primary_server, FakeClientConfigRouterOS("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEA="))
    result = service.client_config("*4")
    assert result["private_key_missing"] is True
    assert "PrivateKey = \n" in result["config"]


def test_custom_awg_container_name_is_used(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("AWG_CONTAINER_NAME", "custom-awg")
    service = AwgAdminService(Settings.from_env().primary_server, FakeCustomContainerNameRouterOS())
    assert service.load_context().container["name"] == "custom-awg"


def test_stopped_awg_container_does_not_fail_startup(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    service = AwgAdminService(Settings.from_env().primary_server, FakeContainerStateRouterOS("stopped"))
    service.startup_validate()


def test_restart_uses_current_container_state(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    router = FakeContainerStateRouterOS("stopped")
    service = AwgAdminService(Settings.from_env().primary_server, router)
    context = service.load_context()

    assert service._restart_proxy(context) is False
    assert router.actions == []

    router.status = "running"
    assert service._restart_proxy(context) is True
    assert router.actions == [("container", "stop", "*1"), ("container", "start", "*1")]


def test_runner_reports_configuration_error_without_traceback():
    env = os.environ.copy()
    for name in ("MIKROTIK_HOST", "MIKROTIK_USERNAME", "MIKROTIK_PASSWORD", "AWG_CLIENT_ENDPOINT", "SESSION_SECRET"):
        env.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-m", "app.runner"],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    assert result.stderr.startswith("AWG_ADMIN_STARTUP_ERROR: Missing required environment variables:")
    assert "Traceback" not in result.stderr
