from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .errors import AppError, unknown_error
from .routeros import RouterOSClient
from .services import AwgAdminService
from .settings import ServerSettings, Settings


LOGGER = logging.getLogger("awg-admin")


@dataclass
class ServerRuntime:
    settings: ServerSettings
    routeros: RouterOSClient
    service: AwgAdminService
    startup_error: AppError | None = None

    def summary(self) -> dict[str, Any]:
        error = self.service.runtime_error or (None if self.service.last_success else self.startup_error)
        return {
            "id": self.settings.id,
            "name": self.settings.display_name,
            "container_name": self.settings.awg_container_name,
            "mikrotik_host": self.settings.mikrotik_host,
            "available": error is None,
            "error": error.payload()["error"] if error else None,
            "last_success": self.service.last_success,
        }


class ServerRegistry:
    def __init__(self, settings: Settings):
        self.runtimes: dict[str, ServerRuntime] = {}
        for server in settings.servers:
            routeros = RouterOSClient(server)
            self.runtimes[server.id] = ServerRuntime(
                settings=server,
                routeros=routeros,
                service=AwgAdminService(server, routeros),
            )
        self.default_id = settings.primary_server.id

    def get(self, server_id: str | None) -> ServerRuntime:
        requested = server_id or self.default_id
        runtime = self.runtimes.get(requested)
        if runtime is None:
            raise AppError(
                "SERVER_NOT_FOUND",
                "error.serverNotFound",
                technical=f"unknown server id: {requested}",
                status_code=404,
            )
        return runtime

    def list(self) -> dict[str, Any]:
        return {"default": self.default_id, "servers": [runtime.summary() for runtime in self.runtimes.values()]}

    def validate_startup(self) -> None:
        usable = 0
        first_fatal: AppError | None = None
        for runtime in self.runtimes.values():
            try:
                runtime.service.startup_validate()
                runtime.startup_error = None
                usable += 1
                LOGGER.info("Startup validation completed server=%s", runtime.settings.id)
            except AppError as exc:
                runtime.startup_error = exc
                if first_fatal is None:
                    first_fatal = exc
                if exc.code == "MIKROTIK_PERMISSION_DENIED":
                    usable += 1
                    LOGGER.error(
                        "Startup permission check failed server=%s; diagnostic UI remains available: %s",
                        runtime.settings.id,
                        exc.technical,
                    )
                else:
                    LOGGER.error(
                        "Startup check failed server=%s code=%s detail=%s; other servers remain available",
                        runtime.settings.id,
                        exc.code,
                        exc.technical,
                    )
            except Exception as exc:
                error = unknown_error(exc)
                runtime.startup_error = error
                if first_fatal is None:
                    first_fatal = error
                LOGGER.exception(
                    "Unexpected startup check failure server=%s request_id=%s; other servers remain available",
                    runtime.settings.id,
                    error.request_id,
                )
        if usable == 0 and first_fatal is not None:
            raise first_fatal

    def close(self) -> None:
        for runtime in self.runtimes.values():
            runtime.routeros.close()
