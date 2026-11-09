from __future__ import annotations

import os
import sys

import uvicorn

from .errors import AppError, redact
from .settings import Settings, SettingsError


def startup_error(message: str) -> int:
    print(f"AWG_ADMIN_STARTUP_ERROR: {redact(message)}", file=sys.stderr, flush=True)
    return 1


def main() -> int:
    # Validate the environment before importing FastAPI/Uvicorn application
    # modules. Expected configuration errors then produce one concise line
    # instead of an import traceback from the ASGI server.
    try:
        Settings.from_env()
    except SettingsError as exc:
        return startup_error(str(exc))

    try:
        from . import main as app_module
    except Exception as exc:
        return startup_error(f"UNKNOWN: {exc}")

    try:
        app_module.locales.validate_required()
        app_module.service.startup_validate()
    except AppError as exc:
        if exc.code == "MIKROTIK_PERMISSION_DENIED":
            app_module.service.runtime_error = exc
        else:
            return startup_error(f"{exc.code}: {exc.technical or exc.message_key}")
    except Exception as exc:
        return startup_error(f"UNKNOWN: {exc}")

    app_module.STARTUP_CHECKS_COMPLETED = True
    log_level = os.getenv("LOG_LEVEL", "ERROR").strip().lower() or "error"
    uvicorn.run(
        app_module.app,
        host="0.0.0.0",
        port=8080,
        log_level=log_level,
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
