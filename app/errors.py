from __future__ import annotations

import re
import uuid
from dataclasses import dataclass


SECRET_PATTERNS = (
    re.compile(r"(?i)(password|private[-_ ]?key|preshared[-_ ]?key|token|authorization|cookie)\s*[=:]\s*([^\s,;]+)"),
    re.compile(r"dckr_pat_[A-Za-z0-9_-]+"),
)


def redact(value: object) -> str:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: f"{m.group(1)}=<hidden>", text)
        else:
            text = pattern.sub("<hidden>", text)
    return text[:4000]


@dataclass
class AppError(Exception):
    code: str
    message_key: str
    technical: str = ""
    status_code: int = 500
    hint_key: str = ""
    request_id: str = ""

    def __post_init__(self) -> None:
        if not self.request_id:
            self.request_id = uuid.uuid4().hex[:12]
        super().__init__(self.code)

    def payload(self) -> dict[str, object]:
        return {
            "error": {
                "code": self.code,
                "message_key": self.message_key,
                "hint_key": self.hint_key,
                "technical": redact(self.technical),
                "request_id": self.request_id,
            }
        }


def unknown_error(exc: Exception) -> AppError:
    return AppError(
        code="UNKNOWN_ERROR",
        message_key="error.unknown",
        hint_key="error.unknownHint",
        technical=redact(repr(exc)),
        status_code=500,
    )

