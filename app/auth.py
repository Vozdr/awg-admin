from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict

from fastapi import Cookie, Header, HTTPException, Request

from .settings import Settings


SESSION_COOKIE = "awg_admin_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30


class AuthManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._attempts: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def make_session(self, username: str) -> str:
        issued = str(int(time.time()))
        nonce = secrets.token_urlsafe(18)
        payload = f"{username}:{issued}:{nonce}"
        signature = hmac.new(
            self.settings.session_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()

    def valid_session(self, token: str | None) -> bool:
        if self.settings.auth_disable:
            return True
        if not token:
            return False
        try:
            raw = base64.urlsafe_b64decode(token.encode()).decode()
            username, issued, nonce, signature = raw.rsplit(":", 3)
            issued_int = int(issued)
        except Exception:
            return False
        payload = f"{username}:{issued}:{nonce}"
        expected = hmac.new(
            self.settings.session_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return (
            secrets.compare_digest(signature, expected)
            and secrets.compare_digest(username, self.settings.admin_username)
            and 0 <= int(time.time()) - issued_int < SESSION_MAX_AGE
        )

    def csrf_token(self, session: str) -> str:
        return hmac.new(
            self.settings.session_secret.encode(),
            f"csrf:{session}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def valid_credentials(self, username: str, password: str) -> bool:
        return secrets.compare_digest(username, self.settings.admin_username) and secrets.compare_digest(
            password, self.settings.admin_password
        )

    def login_allowed(self, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [stamp for stamp in self._attempts[client] if now - stamp < 300]
            self._attempts[client] = recent
            return len(recent) < 5

    def login_failed(self, client: str) -> None:
        with self._lock:
            self._attempts[client].append(time.monotonic())

    def login_succeeded(self, client: str) -> None:
        with self._lock:
            self._attempts.pop(client, None)

    def require_auth(self, awg_admin_session: str | None = Cookie(default=None)) -> str:
        if self.settings.auth_disable:
            return ""
        if not self.valid_session(awg_admin_session):
            raise HTTPException(status_code=401, detail="Authentication required")
        return awg_admin_session or ""

    def require_write_auth(
        self,
        request: Request,
        awg_admin_session: str | None = Cookie(default=None),
        x_csrf_token: str | None = Header(default=None),
    ) -> str:
        if self.settings.auth_disable:
            return ""
        session = self.require_auth(awg_admin_session)
        expected = self.csrf_token(session)
        if not x_csrf_token or not secrets.compare_digest(x_csrf_token, expected):
            raise HTTPException(status_code=403, detail="Invalid CSRF token")
        return session


def client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"

