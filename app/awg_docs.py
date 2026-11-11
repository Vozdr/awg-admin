from __future__ import annotations

import fnmatch
import logging
import re
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("awgm-admin")

DOC_URLS = {
    "ru": "https://raw.githubusercontent.com/timbrs/amneziawg-mikrotik-c/main/docs/env.md",
    "en": "https://raw.githubusercontent.com/timbrs/amneziawg-mikrotik-c/main/docs/en/env.md",
}
DOC_PAGE_URLS = {
    "ru": "https://github.com/timbrs/amneziawg-mikrotik-c/blob/main/docs/env.md",
    "en": "https://github.com/timbrs/amneziawg-mikrotik-c/blob/main/docs/en/env.md",
}
BUNDLED_DIR = Path(__file__).resolve().parent / "upstream_docs"
CACHE_DIR = Path(tempfile.gettempdir()) / "awgm-admin-docs"
FETCH_TIMEOUT = 6
MAX_DOC_BYTES = 1024 * 1024
REQUIRED_SENTINELS = {"AWG_LISTEN", "AWG_REMOTE", "AWG_MODE", "AWG_SERVER_PUB"}


def clean_markdown(value: str) -> str:
    value = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    return " ".join(value.replace("&nbsp;", " ").split()).strip()


def _expand_keys(raw: str) -> list[str]:
    keys = re.findall(r"`(AWG_[A-Z0-9_*]+)`", raw.upper())
    if len(keys) == 2:
        first = re.fullmatch(r"(.+?)(\d+)", keys[0])
        last = re.fullmatch(r"(.+?)(\d+)", keys[1])
        if first and last and first.group(1) == last.group(1):
            start, end = int(first.group(2)), int(last.group(2))
            if start <= end and end - start <= 20:
                return [f"{first.group(1)}{index}" for index in range(start, end + 1)]
    return list(dict.fromkeys(keys))


def parse_environment_document(text: str) -> dict[str, dict[str, Any]]:
    footnotes = {
        marker: clean_markdown(note)
        for marker, note in re.findall(r"^`(\*+)`\s*(.+?)\s*$", text, flags=re.M)
    }
    result: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        if not line.lstrip().startswith("|") or "---" in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        keys = _expand_keys(cells[0])
        if not keys:
            continue
        required_raw = clean_markdown(cells[1])
        marker_match = re.search(r"(\*+)$", required_raw)
        marker = marker_match.group(1) if marker_match else ""
        required_word = required_raw.removesuffix(marker).strip().lower()
        required = required_word in {"да", "yes"}
        default_raw = clean_markdown(cells[2])
        default_value = None if default_raw in {"", "-", "--", "—"} else default_raw
        description = clean_markdown(cells[3])
        for key in keys:
            result[key] = {
                "key": key,
                "default": default_value,
                "required": required,
                "required_note": footnotes.get(marker, "") if required and marker else "",
                "description": description,
            }
    return result


def validate_environment_document(data: dict[str, dict[str, Any]]) -> None:
    concrete = {key for key in data if "*" not in key}
    if len(data) < 25 or not REQUIRED_SENTINELS.issubset(concrete):
        raise ValueError("upstream environment documentation is incomplete or has an unsupported format")


class AwgEnvironmentDocs:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._docs: dict[str, dict[str, dict[str, Any]]] = {"ru": {}, "en": {}}
        self._sources: dict[str, str] = {"ru": "unavailable", "en": "unavailable"}
        self._load_local_copies()

    @staticmethod
    def _bundled_path(lang: str) -> Path:
        return BUNDLED_DIR / f"env.{lang}.md"

    @staticmethod
    def _cache_path(lang: str) -> Path:
        return CACHE_DIR / f"env.{lang}.md"

    def _accept(self, lang: str, text: str, source: str) -> None:
        parsed = parse_environment_document(text)
        validate_environment_document(parsed)
        with self._lock:
            self._docs[lang] = parsed
            self._sources[lang] = source

    def _load_local_copies(self) -> None:
        for lang in DOC_URLS:
            for path, source in ((self._bundled_path(lang), "bundled"), (self._cache_path(lang), "cache")):
                if not path.exists():
                    continue
                try:
                    self._accept(lang, path.read_text(encoding="utf-8"), source)
                except Exception as exc:
                    LOGGER.warning("AWG docs %s copy rejected path=%s detail=%s", source, path, exc)

    @staticmethod
    def _download(url: str) -> str:
        request = urllib.request.Request(url, headers={"User-Agent": "AWGM-Admin"})
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
            payload = response.read(MAX_DOC_BYTES + 1)
        if len(payload) > MAX_DOC_BYTES:
            raise ValueError("upstream document exceeds size limit")
        return payload.decode("utf-8")

    def refresh(self) -> None:
        cache_available = True
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            cache_available = False
            LOGGER.warning("AWG environment docs cache is unavailable path=%s detail=%s", CACHE_DIR, exc)
        for lang, url in DOC_URLS.items():
            try:
                text = self._download(url)
                self._accept(lang, text, "remote")
                if cache_available:
                    try:
                        path = self._cache_path(lang)
                        temporary = path.with_suffix(".tmp")
                        temporary.write_text(text, encoding="utf-8")
                        temporary.replace(path)
                    except Exception as exc:
                        LOGGER.warning("AWG environment docs cache write skipped lang=%s detail=%s", lang, exc)
                LOGGER.info("AWG environment docs updated lang=%s keys=%s", lang, len(self._docs[lang]))
                print(
                    f"AWG_ADMIN: env.md downloaded successfully lang={lang} keys={len(self._docs[lang])}",
                    flush=True,
                )
            except Exception as exc:
                LOGGER.warning("AWG environment docs update skipped lang=%s detail=%s", lang, exc)

    def catalog(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            return {
                lang: [{**item, "pattern": "*" in key} for key, item in sorted(rows.items())]
                for lang, rows in self._docs.items()
            }

    def metadata(self, lang: str, key: str) -> dict[str, Any]:
        selected = "ru" if lang == "ru" else "en"
        with self._lock:
            rows = self._docs.get(selected) or self._docs.get("en", {})
            if key in rows:
                return dict(rows[key])
            patterns = sorted((pattern for pattern in rows if "*" in pattern), key=len, reverse=True)
            for pattern in patterns:
                if fnmatch.fnmatchcase(key, pattern):
                    item = dict(rows[pattern])
                    item["key"] = key
                    return item
        return {}

    def info(self) -> dict[str, Any]:
        with self._lock:
            return {
                "links": dict(DOC_PAGE_URLS),
                "sources": dict(self._sources),
                "catalog": self.catalog(),
            }


awg_environment_docs = AwgEnvironmentDocs()
