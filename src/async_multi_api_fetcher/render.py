"""Turning results into text for humans or JSON for other programs."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from async_multi_api_fetcher.models import FetchResult

_TAGS = {"ok": "OK", "no_results": "NO RESULTS", "error": "ERROR"}


def _age(fetched_at: datetime) -> str:
    seconds = max(0, int((datetime.now(UTC) - fetched_at).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


def render_text(results: Sequence[FetchResult]) -> str:
    lines: list[str] = []
    for r in results:
        if r.from_cache:
            origin = f"cached {_age(r.fetched_at)}"
        else:
            origin = f"network, {r.elapsed_ms} ms"
        lines.append(f"[{_TAGS[r.status]}] {r.source}  ({origin})")
        if r.status == "ok" and r.summary:
            lines.extend(f"    {line}" for line in r.summary.splitlines())
        elif r.status == "no_results":
            lines.append("    The query was valid but matched nothing.")
        elif r.status == "error":
            lines.append(f"    {r.error}")
        lines.append("")

    ok = sum(r.status == "ok" for r in results)
    empty = sum(r.status == "no_results" for r in results)
    failed = sum(r.status == "error" for r in results)
    lines.append(f"{ok} ok, {empty} no results, {failed} failed")
    return "\n".join(lines)


def render_json(results: Sequence[FetchResult]) -> str:
    return json.dumps([r.model_dump(mode="json") for r in results], indent=2, ensure_ascii=False)
