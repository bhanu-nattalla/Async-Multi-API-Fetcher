from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from async_multi_api_fetcher.cache import JsonCache


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def test_miss_then_hit(tmp_path: Path) -> None:
    cache = JsonCache(tmp_path / "c.json", ttl_seconds=60, now=Clock())
    assert cache.get("k") is None
    cache.set("k", {"a": 1})
    entry = cache.get("k")
    assert entry is not None and entry.payload == {"a": 1}


def test_entry_expires_after_ttl(tmp_path: Path) -> None:
    clock = Clock()
    cache = JsonCache(tmp_path / "c.json", ttl_seconds=60, now=clock)
    cache.set("k", {"a": 1})
    clock.advance(59)
    assert cache.get("k") is not None
    clock.advance(1)
    assert cache.get("k") is None


def test_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    clock = Clock()
    first = JsonCache(path, ttl_seconds=60, now=clock)
    first.set("k", {"a": 1})
    first.save()
    second = JsonCache(path, ttl_seconds=60, now=clock)
    entry = second.get("k")
    assert entry is not None and entry.payload == {"a": 1}


def test_corrupt_file_is_treated_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text("{ this is not json", encoding="utf-8")
    cache = JsonCache(path, ttl_seconds=60)
    assert cache.get("k") is None
    cache.set("k", {"a": 1})
    cache.save()  # must overwrite the corrupt file cleanly
    assert JsonCache(path, ttl_seconds=60).get("k") is not None


def test_wrong_structure_is_treated_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert JsonCache(path, ttl_seconds=60).get("k") is None


def test_save_leaves_no_temp_files(tmp_path: Path) -> None:
    cache = JsonCache(tmp_path / "c.json", ttl_seconds=60)
    cache.set("k", {"a": 1})
    cache.save()
    assert [p.name for p in tmp_path.iterdir()] == ["c.json"]


def test_save_creates_missing_folders(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "c.json"
    cache = JsonCache(path, ttl_seconds=60)
    cache.set("k", {"a": 1})
    cache.save()
    assert path.exists()


def test_clear_removes_file(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    cache = JsonCache(path, ttl_seconds=60)
    cache.set("k", {"a": 1})
    cache.save()
    cache.clear()
    assert not path.exists()
    assert cache.get("k") is None
