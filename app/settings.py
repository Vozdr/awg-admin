from __future__ import annotations

import os
import secrets
from dataclasses import dataclass


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


class SettingsError(ValueError):
    pass


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise SettingsError(f"{name} must be yes/no, true/false, on/off or 1/0")


def env_int(name: str, default: int, minimum: int = 0, maximum: int = 65535) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None or not raw.strip() else int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise SettingsError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class Settings:
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
    admin_username: str
    admin_password: str
    auth_disable: bool
    cookie_secure: bool
    session_secret: str
    default_lang: str
    default_theme: str
    client_default_keepalive: int
    client_default_dns: str
    client_default_allowed_ips: str
    app_version: str
    log_level: str
    locales_dir: str

    @classmethod
    def from_env(cls) -> "Settings":
        host = os.getenv("MIKROTIK_HOST", "").strip()
        username = os.getenv("MIKROTIK_USERNAME", "").strip()
        password = os.getenv("MIKROTIK_PASSWORD", "")
        endpoint = os.getenv("AWG_CLIENT_ENDPOINT", "").strip()
        missing = [
            name
            for name, value in (
                ("MIKROTIK_HOST", host),
                ("MIKROTIK_USERNAME", username),
                ("MIKROTIK_PASSWORD", password),
                ("AWG_CLIENT_ENDPOINT", endpoint),
            )
            if not value
        ]
        if missing:
            raise SettingsError("Missing required environment variables: " + ", ".join(missing))

        ssl_enabled = env_bool("MIKROTIK_API_SSL", False)
        port = env_int("MIKROTIK_API_PORT", 8729 if ssl_enabled else 8728, 1, 65535)
        theme = os.getenv("DEFAULT_THEME", "dark").strip().lower() or "dark"
        if theme not in {"dark", "light"}:
            raise SettingsError("DEFAULT_THEME must be dark or light")
        lang = os.getenv("DEFAULT_LANG", "ru").strip() or "ru"
        if not all(ch.isalnum() or ch in {"_", "-"} for ch in lang):
            raise SettingsError("DEFAULT_LANG contains unsupported characters")
        if ":" not in endpoint:
            raise SettingsError("AWG_CLIENT_ENDPOINT must use host:port format")
        endpoint_host, endpoint_port = endpoint.rsplit(":", 1)
        if not endpoint_host.strip():
            raise SettingsError("AWG_CLIENT_ENDPOINT host is empty")
        try:
            parsed_endpoint_port = int(endpoint_port)
        except ValueError as exc:
            raise SettingsError("AWG_CLIENT_ENDPOINT port must be an integer") from exc
        if not 1 <= parsed_endpoint_port <= 65535:
            raise SettingsError("AWG_CLIENT_ENDPOINT port must be between 1 and 65535")

        admin_username = os.getenv("ADMIN_USERNAME", "admin") or "admin"
        admin_password = os.getenv("ADMIN_PASSWORD", "admin") or "admin"
        return cls(
            mikrotik_host=host,
            mikrotik_username=username,
            mikrotik_password=password,
            mikrotik_api_ssl=ssl_enabled,
            mikrotik_api_port=port,
            mikrotik_ssl_verify=env_bool("MIKROTIK_SSL_VERIFY", True),
            mikrotik_connect_timeout=env_int("MIKROTIK_CONNECT_TIMEOUT", 10, 1, 120),
            mikrotik_command_timeout=env_int("MIKROTIK_COMMAND_TIMEOUT", 20, 1, 300),
            awg_container_name=os.getenv("AWG_CONTAINER_NAME", "wg-awg-server-1").strip()
            or "wg-awg-server-1",
            awg_client_endpoint=endpoint,
            awg_server_name=os.getenv("AWG_SERVER_NAME", "AWG Server").strip() or "AWG Server",
            admin_username=admin_username,
            admin_password=admin_password,
            auth_disable=env_bool("AUTH_DISABLE", False),
            cookie_secure=env_bool("COOKIE_SECURE", False),
            session_secret=os.getenv("SESSION_SECRET", "") or secrets.token_urlsafe(48),
            default_lang=lang,
            default_theme=theme,
            client_default_keepalive=env_int("CLIENT_DEFAULT_KEEPALIVE", 30, 0, 65535),
            client_default_dns=os.getenv("CLIENT_DEFAULT_DNS", "").strip(),
            client_default_allowed_ips=os.getenv(
                "CLIENT_DEFAULT_ALLOWED_IPS", "0.0.0.0/0,::/0"
            ).strip(),
            app_version=os.getenv("AWG_ADMIN_VERSION", "dev").strip() or "dev",
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            locales_dir=os.getenv("LOCALES_DIR", "/app/locales").strip() or "/app/locales",
        )

    def safe_startup_values(self) -> list[tuple[str, object]]:
        return [
            ("AWG_ADMIN_VERSION", self.app_version),
            ("MIKROTIK_HOST", self.mikrotik_host),
            ("MIKROTIK_API_SSL", self.mikrotik_api_ssl),
            ("MIKROTIK_API_PORT", self.mikrotik_api_port),
            ("MIKROTIK_SSL_VERIFY", self.mikrotik_ssl_verify),
            ("MIKROTIK_USERNAME", self.mikrotik_username),
            ("MIKROTIK_PASSWORD", "<hidden>"),
            ("AWG_CONTAINER_NAME", self.awg_container_name),
            ("AWG_CLIENT_ENDPOINT", self.awg_client_endpoint),
            ("AWG_SERVER_NAME", self.awg_server_name),
            ("AUTH_DISABLE", self.auth_disable),
            ("COOKIE_SECURE", self.cookie_secure),
            ("ADMIN_USERNAME", self.admin_username),
            ("ADMIN_PASSWORD", "<hidden>"),
            ("DEFAULT_LANG", self.default_lang),
            ("DEFAULT_THEME", self.default_theme),
            ("CLIENT_DEFAULT_KEEPALIVE", self.client_default_keepalive),
            ("CLIENT_DEFAULT_DNS", self.client_default_dns),
            ("CLIENT_DEFAULT_ALLOWED_IPS", self.client_default_allowed_ips),
            ("LOG_LEVEL", self.log_level),
        ]

