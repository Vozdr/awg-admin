from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .settings import Settings


class Locales:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.directory = Path(settings.locales_dir)

    def codes(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted(path.stem for path in self.directory.glob("*.json") if re.fullmatch(r"[A-Za-z0-9_-]+", path.stem))

    def safe_code(self, value: str | None) -> str:
        value = re.sub(r"[^A-Za-z0-9_-]", "", value or "")
        codes = self.codes()
        if value in codes:
            return value
        if self.settings.default_lang in codes:
            return self.settings.default_lang
        if "ru" in codes:
            return "ru"
        return codes[0] if codes else "ru"

    def load(self, value: str | None) -> dict[str, Any]:
        code = self.safe_code(value)
        path = self.directory / f"{code}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict[str, str]]:
        result = []
        for code in self.codes():
            try:
                data = self.load(code)
            except Exception:
                data = {}
            result.append(
                {
                    "code": code,
                    "name": str(data.get("language.name") or code),
                    "native_name": str(data.get("language.nativeName") or data.get("language.name") or code),
                }
            )
        return result

    def validate_required(self) -> None:
        for code in ("ru", "en"):
            path = self.directory / f"{code}.json"
            if not path.exists():
                raise RuntimeError(f"required locale is missing: {path}")
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data:
                raise RuntimeError(f"required locale is empty or invalid: {path}")

