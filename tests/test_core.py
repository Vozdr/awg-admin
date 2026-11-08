from __future__ import annotations

import base64
import ipaddress
import json
import struct
import zlib

import pytest

from app.errors import redact
from app.routeros import decode_length, encode_length, parse_sentence
from app.services import (
    AwgAdminService,
    build_awg_config,
    clean_comment_metadata,
    comment_with_disabled_at,
    make_vpn_uri,
    parse_conf,
    qr_data_url,
)
from app.settings import Settings, SettingsError


class FakeRouterOS:
    def __init__(self):
        self.env = {
            "AWG_MODE": "server", "AWG_LISTEN": "0.0.0.0:51820", "AWG_REMOTE": "0.0.0.0:51820",
            "AWG_JC": "7", "AWG_JMIN": "10", "AWG_JMAX": "50", "AWG_S1": "20", "AWG_S2": "30",
            "AWG_H1": "1", "AWG_H2": "2", "AWG_H3": "3", "AWG_H4": "4",
            "AWG_SERVER_PUB": "server-public", "AWG_CLIENT_PUBS": "client-public",
        }

    def print(self, menu, proplist=None):
        if menu == "system/resource":
            return [{"version": "7.22", "board-name": "CHR", "architecture-name": "x86_64"}]
        if menu == "container":
            return [{".id": "*1", "name": "wg-awg-server-1", "status": "running", "running": "true", "envlist": "awg-env"}]
        if menu == "container/envs":
            return [{".id": f"*e{index}", "list": "awg-env", "key": key, "value": value} for index, (key, value) in enumerate(self.env.items())]
        if menu == "interface/wireguard":
            return [{".id": "*2", "name": "wg-awg", "listen-port": "51820", "public-key": "server-public", "disabled": "false", "running": "true"}]
        if menu == "ip/address":
            return [{".id": "*3", "address": "192.168.177.1/24", "network": "192.168.177.0", "interface": "wg-awg", "disabled": "false"}]
        if menu == "interface/wireguard/peers":
            return [{".id": "*4", "name": "Alice", "comment": "Phone", "interface": "wg-awg", "disabled": "false", "public-key": "client-public", "allowed-address": "192.168.177.2/32", "client-address": "192.168.177.2/24", "rx": "1024", "tx": "2048"}]
        raise AssertionError(menu)


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
    config = build_awg_config(sample_plain_config(), sample_env(), "vpn.example.org:51820")
    parsed = parse_conf(config)
    assert parsed["Interface"]["Jc"] == "7"
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
    settings = Settings.from_env()
    assert settings.mikrotik_api_port == 8728
    assert settings.admin_username == "admin"
    assert settings.admin_password == "admin"
    assert settings.awg_container_name == "wg-awg-server-1"
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "invalid")
    with pytest.raises(SettingsError):
        Settings.from_env()


def test_service_discovers_proxy_interface_and_address_pool(monkeypatch):
    monkeypatch.setenv("MIKROTIK_HOST", "172.17.0.1")
    monkeypatch.setenv("MIKROTIK_USERNAME", "awg-admin")
    monkeypatch.setenv("MIKROTIK_PASSWORD", "secret")
    monkeypatch.setenv("AWG_CLIENT_ENDPOINT", "vpn.example.org:51820")
    service = AwgAdminService(Settings.from_env(), FakeRouterOS())
    service.startup_validate()
    dashboard = service.dashboard()
    assert dashboard["server"]["wireguard_interface"] == "wg-awg"
    assert dashboard["pools"] == [{"cidr": "192.168.177.0/24", "server_address": "192.168.177.1", "free_address": "192.168.177.3", "available": True}]
    assert dashboard["clients"][0]["name"] == "Alice"
    assert dashboard["clients"][0]["rx_human"] == "1.0 KB"
    assert dashboard["sync"]["ok"] is True
