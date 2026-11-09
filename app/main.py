from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError

from .auth import SESSION_COOKIE, SESSION_MAX_AGE, AuthManager, client_address
from .errors import AppError, unknown_error
from .i18n import Locales
from .models import ClientCreate, ClientToggle, ClientUpdate, SyncRequest
from .routeros import RouterOSClient
from .services import AwgAdminService
from .settings import Settings, SettingsError


APP_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
STARTUP_CHECKS_COMPLETED = False

try:
    settings = Settings.from_env()
except SettingsError as exc:
    print(f"AWG_ADMIN_STARTUP_ERROR: {exc}", file=sys.stderr, flush=True)
    raise

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger("awg-admin")

routeros = RouterOSClient(settings)
service = AwgAdminService(settings, routeros)
auth = AuthManager(settings)
locales = Locales(settings)


def render_template(name: str, replacements: dict[str, str] | None = None) -> str:
    text = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
    for key, value in (replacements or {}).items():
        text = text.replace(f"__{key}__", value)
    return text


@asynccontextmanager
async def lifespan(_: FastAPI):
    print(f"AWG Admin {settings.app_version} starting", flush=True)
    print("https://github.com/Vozdr/awg-admin", flush=True)
    for key, value in settings.safe_startup_values():
        print(f"AWG_ADMIN: {key}={value}", flush=True)
    if settings.auth_disable:
        LOGGER.error(
            "AUTH_DISABLED: authentication to the admin panel is disabled. Remove AUTH_DISABLE from env to secure the panel."
        )
    elif settings.admin_username == "admin" and settings.admin_password == "admin":
        LOGGER.error("DEFAULT_CREDENTIALS: admin panel uses default credentials admin/admin")
    if not STARTUP_CHECKS_COMPLETED:
        locales.validate_required()
        try:
            await run_in_threadpool(service.startup_validate)
            LOGGER.info("Startup validation completed successfully")
        except AppError as exc:
            if exc.code == "MIKROTIK_PERMISSION_DENIED":
                service.runtime_error = exc
                LOGGER.error("Startup permission check failed; diagnostic UI remains available: %s", exc.technical)
            else:
                LOGGER.error("Fatal startup check failed code=%s detail=%s", exc.code, exc.technical)
                raise RuntimeError(f"{exc.code}: {exc.technical}") from exc
    yield
    routeros.close()


app = FastAPI(title="AWG Admin", version=settings.app_version, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    LOGGER.warning("request_error code=%s request_id=%s detail=%s", exc.code, exc.request_id, exc.technical)
    return JSONResponse(exc.payload(), status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    error = AppError(
        "VALIDATION_ERROR",
        "error.validation",
        technical=json.dumps(exc.errors(), ensure_ascii=False),
        status_code=422,
    )
    return JSONResponse(error.payload(), status_code=422)


@app.exception_handler(Exception)
async def unknown_error_handler(_: Request, exc: Exception) -> JSONResponse:
    error = unknown_error(exc)
    LOGGER.exception("unknown_error request_id=%s", error.request_id)
    return JSONResponse(error.payload(), status_code=500)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": settings.app_version}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    await run_in_threadpool(service.load_context, False)
    return {"status": "ready", "last_success": service.last_success}


@app.get("/login", response_class=HTMLResponse)
async def login_page(awg_admin_lang: str | None = Cookie(default=None)):
    if settings.auth_disable:
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(
        render_template(
            "login.html",
            {
                "DEFAULT_LANG": locales.safe_code(awg_admin_lang),
                "DEFAULT_THEME": settings.default_theme,
                "LOGIN_ERROR": "",
            },
        )
    )


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    awg_admin_lang: str | None = Cookie(default=None),
):
    if settings.auth_disable:
        return RedirectResponse("/", status_code=303)
    address = client_address(request)
    if not auth.login_allowed(address):
        return HTMLResponse(
            render_template(
                "login.html",
                {
                    "DEFAULT_LANG": locales.safe_code(awg_admin_lang),
                    "DEFAULT_THEME": settings.default_theme,
                    "LOGIN_ERROR": '<div class="login-error" data-i18n="login.rateLimited">Too many attempts</div>',
                },
            ),
            status_code=429,
        )
    if not auth.valid_credentials(username, password):
        auth.login_failed(address)
        return HTMLResponse(
            render_template(
                "login.html",
                {
                    "DEFAULT_LANG": locales.safe_code(awg_admin_lang),
                    "DEFAULT_THEME": settings.default_theme,
                    "LOGIN_ERROR": '<div class="login-error" data-i18n="login.invalid">Invalid credentials</div>',
                },
            ),
            status_code=401,
        )
    auth.login_succeeded(address)
    token = auth.make_session(username)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=SESSION_MAX_AGE,
    )
    LOGGER.info("panel_login_success client=%s", address)
    return response


@app.get("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/", response_class=HTMLResponse)
async def index(
    awg_admin_session: str | None = Cookie(default=None),
    awg_admin_lang: str | None = Cookie(default=None),
):
    if not settings.auth_disable and not auth.valid_session(awg_admin_session):
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(
        render_template(
            "index.html",
            {
                "APP_VERSION": settings.app_version,
                "DEFAULT_LANG": locales.safe_code(awg_admin_lang),
                "DEFAULT_THEME": settings.default_theme,
                "AUTH_DISABLED": "true" if settings.auth_disable else "false",
            },
        )
    )


@app.get("/api/i18n")
async def i18n_list() -> dict[str, Any]:
    return {"locales": locales.list(), "default": settings.default_lang}


@app.get("/api/i18n/{lang}")
async def i18n_get(lang: str) -> dict[str, Any]:
    return locales.load(lang)


@app.get("/api/session")
async def session_info(session: str = Depends(auth.require_auth)) -> dict[str, Any]:
    return {
        "csrf": "" if settings.auth_disable else auth.csrf_token(session),
        "auth_disabled": settings.auth_disable,
        "default_lang": settings.default_lang,
        "default_theme": settings.default_theme,
        "version": settings.app_version,
        "client_defaults": {
            "keepalive": settings.client_default_keepalive,
            "dns": settings.client_default_dns,
            "allowed_ips": settings.client_default_allowed_ips,
        },
    }


@app.get("/api/dashboard")
async def dashboard(_: str = Depends(auth.require_auth)) -> dict[str, Any]:
    return await run_in_threadpool(service.dashboard)


@app.get("/api/server/settings")
async def server_settings(_: str = Depends(auth.require_auth)) -> dict[str, Any]:
    return await run_in_threadpool(service.server_settings)


@app.get("/api/clients/{peer_id}/config")
async def client_config(peer_id: str, _: str = Depends(auth.require_auth)) -> dict[str, Any]:
    return await run_in_threadpool(service.client_config, peer_id)


@app.post("/api/clients")
async def create_client(data: ClientCreate, _: str = Depends(auth.require_write_auth)) -> dict[str, Any]:
    return await run_in_threadpool(service.create_client, data)


@app.put("/api/clients/{peer_id}")
async def update_client(peer_id: str, data: ClientUpdate, _: str = Depends(auth.require_write_auth)) -> dict[str, Any]:
    return await run_in_threadpool(service.update_client, peer_id, data)


@app.post("/api/clients/{peer_id}/disabled")
async def toggle_client(peer_id: str, data: ClientToggle, _: str = Depends(auth.require_write_auth)) -> dict[str, Any]:
    return await run_in_threadpool(service.toggle_client, peer_id, data.disabled)


@app.delete("/api/clients/{peer_id}")
async def delete_client(peer_id: str, _: str = Depends(auth.require_write_auth)) -> dict[str, Any]:
    return await run_in_threadpool(service.delete_client, peer_id)


@app.get("/api/sync")
async def sync_preview(_: str = Depends(auth.require_auth)) -> dict[str, Any]:
    return await run_in_threadpool(service.sync_preview)


@app.post("/api/sync")
async def sync_apply(data: SyncRequest, _: str = Depends(auth.require_write_auth)) -> dict[str, Any]:
    if not data.confirm:
        raise HTTPException(status_code=400, detail="Confirmation is required")
    return await run_in_threadpool(service.sync_apply)

