from __future__ import annotations

import socket
import ssl
import struct
import threading
from dataclasses import dataclass
from typing import Iterable

from .errors import AppError
from .settings import ServerSettings


class RouterOSProtocolError(RuntimeError):
    pass


@dataclass
class RouterOSTrap(RuntimeError):
    message: str
    category: str = ""

    def __str__(self) -> str:
        return self.message


def encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    if length < 0x4000:
        return struct.pack(">H", length | 0x8000)
    if length < 0x200000:
        value = length | 0xC00000
        return bytes([(value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF])
    if length < 0x10000000:
        return struct.pack(">I", length | 0xE0000000)
    return b"\xF0" + struct.pack(">I", length)


def decode_length(read_exact) -> int:
    first = read_exact(1)[0]
    if first < 0x80:
        return first
    if first < 0xC0:
        return ((first & 0x3F) << 8) | read_exact(1)[0]
    if first < 0xE0:
        tail = read_exact(2)
        return ((first & 0x1F) << 16) | (tail[0] << 8) | tail[1]
    if first < 0xF0:
        tail = read_exact(3)
        return ((first & 0x0F) << 24) | (tail[0] << 16) | (tail[1] << 8) | tail[2]
    if first == 0xF0:
        return struct.unpack(">I", read_exact(4))[0]
    raise RouterOSProtocolError(f"unsupported API length prefix: {first:#x}")


def parse_sentence(words: list[str]) -> tuple[str, dict[str, str]]:
    if not words:
        raise RouterOSProtocolError("empty API sentence")
    kind = words[0]
    values: dict[str, str] = {}
    for word in words[1:]:
        if word.startswith("="):
            key, _, value = word[1:].partition("=")
            values[key] = value
        elif word.startswith(".tag="):
            values[".tag"] = word[5:]
        else:
            values.setdefault("message", word)
    return kind, values


def trap_to_app_error(exc: RouterOSTrap) -> AppError:
    lowered = exc.message.lower()
    if "not enough permissions" in lowered or "not allowed" in lowered:
        return AppError(
            "MIKROTIK_PERMISSION_DENIED",
            "error.mikrotikPermissionDenied",
            technical=exc.message,
            status_code=503,
            hint_key="error.mikrotikPermissionDeniedHint",
        )
    if "no such" in lowered:
        return AppError(
            "MIKROTIK_RESOURCE_NOT_FOUND",
            "error.mikrotikResourceNotFound",
            technical=exc.message,
            status_code=404,
        )
    return AppError(
        "MIKROTIK_API_ERROR",
        "error.mikrotikApiError",
        technical=exc.message,
        status_code=502,
    )


class RouterOSClient:
    def __init__(self, settings: ServerSettings):
        self.settings = settings
        self._sock: socket.socket | ssl.SSLSocket | None = None
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                finally:
                    self._sock = None

    def _connect(self) -> None:
        if self._sock is not None:
            return
        try:
            raw = socket.create_connection(
                (self.settings.mikrotik_host, self.settings.mikrotik_api_port),
                timeout=self.settings.mikrotik_connect_timeout,
            )
            raw.settimeout(self.settings.mikrotik_command_timeout)
            if self.settings.mikrotik_api_ssl:
                context = ssl.create_default_context()
                if not self.settings.mikrotik_ssl_verify:
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                self._sock = context.wrap_socket(
                    raw,
                    server_hostname=self.settings.mikrotik_host
                    if self.settings.mikrotik_ssl_verify
                    else None,
                )
            else:
                self._sock = raw
            self._talk(
                [
                    "/login",
                    f"=name={self.settings.mikrotik_username}",
                    f"=password={self.settings.mikrotik_password}",
                ]
            )
        except RouterOSTrap as exc:
            self.close()
            raise AppError(
                "MIKROTIK_AUTH_FAILED",
                "error.mikrotikAuthFailed",
                technical=exc.message,
                status_code=503,
                hint_key="error.mikrotikAuthFailedHint",
            ) from exc
        except (OSError, ssl.SSLError) as exc:
            self.close()
            raise AppError(
                "MIKROTIK_UNREACHABLE",
                "error.mikrotikUnavailable",
                technical=str(exc),
                status_code=503,
                hint_key="error.mikrotikUnavailableHint",
            ) from exc

    def _read_exact(self, size: int) -> bytes:
        if self._sock is None:
            raise RouterOSProtocolError("API socket is not connected")
        data = bytearray()
        while len(data) < size:
            chunk = self._sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("RouterOS API connection closed")
            data.extend(chunk)
        return bytes(data)

    def _write_sentence(self, words: Iterable[str]) -> None:
        if self._sock is None:
            raise RouterOSProtocolError("API socket is not connected")
        payload = bytearray()
        for word in words:
            encoded = word.encode("utf-8")
            payload.extend(encode_length(len(encoded)))
            payload.extend(encoded)
        payload.append(0)
        self._sock.sendall(payload)

    def _read_sentence(self) -> list[str]:
        words: list[str] = []
        while True:
            length = decode_length(self._read_exact)
            if length == 0:
                return words
            words.append(self._read_exact(length).decode("utf-8", errors="replace"))

    def _talk(self, words: list[str]) -> list[dict[str, str]]:
        self._write_sentence(words)
        records: list[dict[str, str]] = []
        while True:
            kind, values = parse_sentence(self._read_sentence())
            if kind == "!re":
                records.append(values)
            elif kind == "!trap":
                raise RouterOSTrap(values.get("message", "RouterOS API trap"), values.get("category", ""))
            elif kind == "!fatal":
                raise RouterOSProtocolError(values.get("message", "RouterOS API fatal error"))
            elif kind == "!done":
                if values:
                    records.append(values)
                return records

    def command(
        self,
        path: str,
        attributes: dict[str, object] | None = None,
        queries: Iterable[str] | None = None,
        proplist: Iterable[str] | None = None,
    ) -> list[dict[str, str]]:
        with self._lock:
            try:
                self._connect()
                words = [path]
                if proplist:
                    words.append("=.proplist=" + ",".join(proplist))
                for key, value in (attributes or {}).items():
                    words.append(f"={key}={value}")
                words.extend(queries or [])
                return self._talk(words)
            except AppError:
                raise
            except RouterOSTrap as exc:
                raise trap_to_app_error(exc) from exc
            except (OSError, ssl.SSLError, ConnectionError, RouterOSProtocolError) as exc:
                self.close()
                raise AppError(
                    "MIKROTIK_CONNECTION_LOST",
                    "error.mikrotikConnectionLost",
                    technical=str(exc),
                    status_code=503,
                    hint_key="error.mikrotikConnectionLostHint",
                ) from exc
            finally:
                # Re-authenticate every command. Besides avoiding stale sockets,
                # this makes a disabled or changed RouterOS account visible to
                # the panel immediately after the current operation.
                self.close()

    def print(
        self,
        menu: str,
        proplist: Iterable[str] | None = None,
        queries: Iterable[str] | None = None,
    ) -> list[dict[str, str]]:
        return self.command(f"/{menu.strip('/')}/print", proplist=proplist, queries=queries)

    def add(self, menu: str, values: dict[str, object]) -> str:
        result = self.command(f"/{menu.strip('/')}/add", attributes=values)
        for record in reversed(result):
            if record.get("ret"):
                return record["ret"]
        return ""

    def set(self, menu: str, item_id: str, values: dict[str, object]) -> None:
        self.command(f"/{menu.strip('/')}/set", attributes={".id": item_id, **values})

    def remove(self, menu: str, item_id: str) -> None:
        self.command(f"/{menu.strip('/')}/remove", attributes={".id": item_id})

    def action(self, menu: str, action: str, item_id: str, **values: object) -> list[dict[str, str]]:
        return self.command(
            f"/{menu.strip('/')}/{action}", attributes={".id": item_id, **values}
        )

