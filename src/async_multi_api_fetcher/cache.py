# JSON file cache with TTL
"""A small JSON-file cache with a time-to-live (TTL).

File format:  { "<cache key>": { "fetched_at": "<ISO UTC time>", "payload": { ... } } }

Design notes:
- The file is read once (lazily) and written once (save()), not once per source.
- Writes are atomic: write a temp file in the same folder, then os.replace() it over the
  real file, so a crash mid-write can never leave a half-written (corrupt) cache.
- A missing, unreadable or corrupt file is treated as an empty cache, never as a crash.
- The clock is injectable so TTL behaviour can be tested without sleeping.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CacheEntry:
    fetched_at: datetime
    payload: dict[str, Any]


class JsonCache:
    def __init__(
        self,
        path: Path,
        ttl_seconds: int,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._path = path
        self._ttl = timedelta(seconds=ttl_seconds)
        self._now = now
        self._entries: dict[str, dict[str, Any]] | None = None  # loaded lazily
        self._dirty = False

    # -- reading ---------------------------------------------------------------------

    def _read_file(self) -> dict[str, dict[str, Any]]:
        try:
            raw: Any = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:  # ValueError covers JSONDecodeError
            log.warning("Ignoring unreadable cache file %s: %s", self._path, exc)
            return {}
        if not isinstance(raw, dict):
            log.warning("Ignoring cache file %s: unexpected structure", self._path)
            return {}
        return {key: value for key, value in raw.items() if isinstance(value, dict)}

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._entries is None:
            self._entries = self._read_file()
        return self._entries

    def get(self, key: str) -> CacheEntry | None:
        """Return a fresh entry, or None if missing, malformed or older than the TTL."""
        entry = self._load().get(key)
        if entry is None:
            return None
        try:
            fetched_at = datetime.fromisoformat(entry["fetched_at"])
            payload = entry["payload"]
        except (KeyError, TypeError, ValueError):
            return None
        if fetched_at.tzinfo is None or not isinstance(payload, dict):
            return None
        if self._now() - fetched_at >= self._ttl:
            return None
        return CacheEntry(fetched_at=fetched_at, payload=payload)

    # -- writing ---------------------------------------------------------------------

    def set(self, key: str, payload: dict[str, Any]) -> None:
        self._load()[key] = {"fetched_at": self._now().isoformat(), "payload": payload}
        self._dirty = True

    def save(self) -> None:
        """Atomically write the cache to disk (no-op if nothing changed)."""
        if not self._dirty or self._entries is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self._path.parent, prefix=".cache-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._entries, handle, indent=2, ensure_ascii=False)
            os.replace(tmp_name, self._path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        self._dirty = False

    def clear(self) -> None:
        """Delete the cache file and forget everything in memory."""
        self._path.unlink(missing_ok=True)
        self._entries = {}
        self._dirty = False
