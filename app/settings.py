from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
SERVER_HOST_PATTERN = re.compile(r"^MIKROTIK_HOST_(\d+)$")


class SettingsError(ValueError):
    pass


def _env_name(name: str, index: int) -> str:
    return name if index == 1 else f"{name}_{index}"


def _env_value(name: str, index: int, default: str = "") -> str:
    return os.getenv(_env_name(name, index), default)


def env_bool(name: str, default: bool, index: int = 1) -> bool:
    full_name = _env_name(name, index)
    raw = os.getenv(full_name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise SettingsError(f"{full_name} must be yes/no, true/false, on/off or 1/0")


def env_int(name: str, default: int, minimum: int = 0, maximum: int = 65535, index: int = 1) -> int:
    full_name = _env_name(name, index)
    raw = os.getenv(full_name)
    try:
        value = default if raw is None or not raw.strip() else int(raw)
    except ValueError as exc:
        raise SettingsError(f"{full_name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise SettingsError(f"{full_name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class ServerSettings:
    id: str
    index: int
    mikrotik_host: str
    mikrotik_username: str
    mikrotik_password: str
    mikrotik_api_ssl: bool
    mikrotik_api_port: int
    mikrotik_ssl_verify: bool
    mikrotik_connect_timeout: int
    mikrotik_command_timeout: int
    awg_container_name: str
    awg_client_endpoint: str
    awg_server_name: str
    client_default_keepalive: int
    client_default_dns: str
    client_default_allowed_ips: str

    @property
    def suffix(self) -> str:
        return "" if self.index == 1 else f"_{self.index}"

    @property
    def display_name(self) -> str:
        return self.awg_server_name or self.awg_container_name

    @classmethod
    def from_env(cls, index: int) -> "ServerSettings":
        host = _env_value("MIKROTIK_HOST", index).strip()
        username = _env_value("MIKROTIK_USERNAME", index).strip()
        password = _env_value("MIKROTIK_PASSWORD", index)
        endpoint = _env_value("AWG_CLIENT_ENDPOINT", index).strip()
        required = (("MIKROTIK_HOST", host), ("MIKROTIK_USERNAME", username), ("MIKROTIK_PASSWORD", password), ("AWG_CLIENT_ENDPOINT", endpoint))
        missing = [_env_name(name, index) for name, value in required if not value]
        if missing:
            raise SettingsError("Missing required environment variables: " + ", ".join(missing))

        endpoint_name = _env_name("AWG_CLIENT_ENDPOINT", index)
        if ":" not in endpoint:
            raise SettingsError(f"{endpoint_name} must use host:port format")
        endpoint_host, endpoint_port = endpoint.rsplit(":", 1)
        if not endpoint_host.strip():
            raise SettingsError(f"{endpoint_name} host is empty")
        try:
            parsed_endpoint_port = int(endpoint_port)
        except ValueError as exc:
            raise SettingsError(f"{endpoint_name} port must be an integer") from exc
        if not 1 <= parsed_endpoint_port <= 65535:
            raise SettingsError(f"{endpoint_name} port must be between 1 and 65535")

        ssl_enabled = env_bool("MIKROTIK_API_SSL", False, index)
        return cls(
            id=str(index), index=index, mikrotik_host=host, mikrotik_username=username,
            mikrotik_password=password, mikrotik_api_ssl=ssl_enabled,
            mikrotik_api_port=env_int("MIKROTIK_API_PORT", 8729 if ssl_enabled else 8728, 1, 65535, index),
            mikrotik_ssl_verify=env_bool("MIKROTIK_SSL_VERIFY", True, index),
            mikrotik_connect_timeout=env_int("MIKROTIK_CONNECT_TIMEOUT", 10, 1, 120, index),
            mikrotik_command_timeout=env_int("MIKROTIK_COMMAND_TIMEOUT", 20, 1, 300, index),
            awg_container_name=_env_value("AWG_CONTAINER_NAME", index, "wg-awg-server-1").strip() or "wg-awg-server-1",
            awg_client_endpoint=endpoint,
            awg_server_name=_env_value("AWG_SERVER_NAME", index, "AWG Server").strip() or "AWG Server",
            client_default_keepalive=env_int("CLIENT_DEFAULT_KEEPALIVE", 30, 0, 65535, index),
            client_default_dns=_env_value("CLIENT_DEFAULT_DNS", index, "1.1.1.1,8.8.8.8").strip(),
            client_default_allowed_ips=_env_value("CLIENT_DEFAULT_ALLOWED_IPS", index, "0.0.0.0/0,::/0").strip(),
        )


@dataclass(frozen=True)
class Settings:
    servers: tuple[ServerSettings, ...]
    admin_username: str
    admin_password: str
    auth_disable: bool
    cookie_secure: bool
    session_secret: str
    default_lang: str
    default_theme: str
    app_version: str
    log_level: str
    locales_dir: str

    @classmethod
    def from_env(cls) -> "Settings":
        auth_disable = env_bool("AUTH_DISABLE", False)
        session_secret = os.getenv("SESSION_SECRET", "").strip()
        theme = os.getenv("DEFAULT_THEME", "dark").strip().lower() or "dark"
        if theme not in {"dark", "light"}:
            raise SettingsError("DEFAULT_THEME must be dark or light")
        lang = os.getenv("DEFAULT_LANG", "ru").strip() or "ru"
        if not all(ch.isalnum() or ch in {"_", "-"} for ch in lang):
            raise SettingsError("DEFAULT_LANG contains unsupported characters")

        indices = {1}
        for name in os.environ:
            match = SERVER_HOST_PATTERN.match(name)
            if match and int(match.group(1)) >= 2:
                indices.add(int(match.group(1)))
        servers = tuple(ServerSettings.from_env(index) for index in sorted(indices))
        if not auth_disable and not session_secret:
            raise SettingsError("Missing required environment variables: SESSION_SECRET")
        return cls(
            servers=servers,
            admin_username=os.getenv("ADMIN_USERNAME", "admin") or "admin",
            admin_password=os.getenv("ADMIN_PASSWORD", "admin") or "admin",
            auth_disable=auth_disable, cookie_secure=env_bool("COOKIE_SECURE", False),
            session_secret=session_secret or secrets.token_urlsafe(48), default_lang=lang,
            default_theme=theme, app_version=os.getenv("AWG_ADMIN_VERSION", "dev").strip() or "dev",
            log_level=os.getenv("LOG_LEVEL", "ERROR").strip().upper() or "ERROR",
            locales_dir=os.getenv("LOCALES_DIR", "/app/locales").strip() or "/app/locales",
        )

    @property
    def primary_server(self) -> ServerSettings:
        return self.servers[0]

    def __getattr__(self, name: str):
        if name.startswith(("mikrotik_", "awg_", "client_default_")):
            return getattr(self.primary_server, name)
        raise AttributeError(name)

    def safe_startup_values(self) -> list[tuple[str, object]]:
        values: list[tuple[str, object]] = [
            ("AWG_ADMIN_VERSION", self.app_version), ("AUTH_DISABLE", self.auth_disable),
            ("COOKIE_SECURE", self.cookie_secure), ("ADMIN_USERNAME", self.admin_username),
            ("ADMIN_PASSWORD", "<hidden>"), ("DEFAULT_LANG", self.default_lang),
            ("DEFAULT_THEME", self.default_theme), ("LOG_LEVEL", self.log_level),
            ("AWG_SERVERS", len(self.servers)),
        ]
        for server in self.servers:
            suffix = server.suffix
            values.extend([
                (f"MIKROTIK_HOST{suffix}", server.mikrotik_host),
                (f"MIKROTIK_API_SSL{suffix}", server.mikrotik_api_ssl),
                (f"MIKROTIK_API_PORT{suffix}", server.mikrotik_api_port),
                (f"MIKROTIK_SSL_VERIFY{suffix}", server.mikrotik_ssl_verify),
                (f"MIKROTIK_USERNAME{suffix}", server.mikrotik_username),
                (f"MIKROTIK_PASSWORD{suffix}", "<hidden>"),
                (f"AWG_CONTAINER_NAME{suffix}", server.awg_container_name),
                (f"AWG_CLIENT_ENDPOINT{suffix}", server.awg_client_endpoint),
                (f"AWG_SERVER_NAME{suffix}", server.awg_server_name),
                (f"CLIENT_DEFAULT_KEEPALIVE{suffix}", server.client_default_keepalive),
                (f"CLIENT_DEFAULT_DNS{suffix}", server.client_default_dns),
                (f"CLIENT_DEFAULT_ALLOWED_IPS{suffix}", server.client_default_allowed_ips),
            ])
        return values
