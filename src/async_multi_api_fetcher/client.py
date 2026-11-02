"""The async engine: concurrency, retries, failure isolation, cache integration.

This module is API-agnostic. It receives Source objects and asks each one how to interpret
the response, so there is no API-specific code here.

Guarantees:
- All sources run concurrently (bounded by a semaphore), over ONE shared AsyncClient.
- fetch_one() never raises: every problem becomes a FetchResult with status="error",
  so one broken API can never cost you the results of the others.
- Only successful results are cached; "no results" and errors are always re-checked.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import ValidationError

from async_multi_api_fetcher.cache import JsonCache
from async_multi_api_fetcher.models import FetchResult
from async_multi_api_fetcher.sources import Source

log = logging.getLogger(__name__)

# Worth retrying: the server (or the network) may recover. 4xx errors other than 429 won't.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _make_result(source: Source, started: float, **fields: Any) -> FetchResult:
    return FetchResult(
        source=source.name,
        fetched_at=datetime.now(UTC),
        elapsed_ms=_elapsed_ms(started),
        **fields,
    )


def _error(source: Source, started: float, message: str) -> FetchResult:
    return _make_result(source, started, status="error", error=message)


async def _get_with_retries(
    client: httpx.AsyncClient, source: Source, retries: int, backoff: float
) -> httpx.Response:
    """GET with exponential backoff + jitter on transport errors and retryable statuses."""
    attempt = 0
    while True:
        try:
            response = await client.get(source.url, params=source.params, headers=source.headers)
        except httpx.TransportError:  # timeouts, connection errors, ...
            if attempt >= retries:
                raise
            reason = "transport error"
        else:
            if response.status_code not in RETRY_STATUSES or attempt >= retries:
                return response
            reason = f"HTTP {response.status_code}"
        delay = backoff * (2**attempt) + random.uniform(0, backoff / 2)
        log.debug("%s: %s, retry %d/%d in %.2fs", source.name, reason, attempt + 1, retries, delay)
        await asyncio.sleep(delay)
        attempt += 1


def _first_validation_issue(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "<root>"
    return f"{location}: {first['msg']} ({exc.error_count()} issue(s) in total)"


def _interpret(source: Source, response: httpx.Response, started: float) -> FetchResult:
    """Turn one HTTP response into a FetchResult (status check -> JSON -> validation)."""
    try:
        body: Any = response.json()
        parsed = True
    except ValueError:
        body, parsed = None, False

    # 1. The source decides what the status code + body mean (this is where HTTP 200 with
    #    success=false, or HTTP 404 meaning "no matches", is understood).
    status, message = source.classify(response.status_code, body)
    if status == "error":
        return _error(source, started, message or f"HTTP {response.status_code}")
    if status == "no_results":
        return _make_result(source, started, status="no_results")

    # 2. Validate the payload against the typed model.
    if not parsed:
        return _error(source, started, f"HTTP {response.status_code}: response was not valid JSON")
    try:
        model = source.model.model_validate(body)
    except ValidationError as exc:
        return _error(source, started, f"unexpected response shape: {_first_validation_issue(exc)}")

    # 3. A valid but empty payload is "no results", not success.
    if source.is_empty(model):
        return _make_result(source, started, status="no_results")

    return _make_result(
        source,
        started,
        status="ok",
        data=model.model_dump(mode="json"),
        summary=source.summarize(model),
    )


def _from_cache(source: Source, cache: JsonCache, started: float) -> FetchResult | None:
    entry = cache.get(source.cache_key)
    if entry is None:
        return None
    try:
        # Re-validate: if the model changed since this was cached, treat it as a miss.
        model = source.model.model_validate(entry.payload)
    except ValidationError:
        log.debug("%s: cached payload no longer matches the model, refetching", source.name)
        return None
    return FetchResult(
        source=source.name,
        status="ok",
        data=entry.payload,
        summary=source.summarize(model),
        from_cache=True,
        fetched_at=entry.fetched_at,  # original fetch time, so the age can be shown
        elapsed_ms=_elapsed_ms(started),
    )


async def fetch_one(
    client: httpx.AsyncClient,
    source: Source,
    sem: asyncio.Semaphore,
    *,
    cache: JsonCache | None,
    refresh: bool,
    retries: int,
    backoff: float,
) -> FetchResult:
    started = time.perf_counter()

    if source.missing_secret:
        return _error(
            source,
            started,
            f"missing credential: set the {source.missing_secret} environment variable",
        )

    # Cache hits skip the semaphore: they cost nothing and should never wait in line.
    if cache is not None and not refresh:
        cached = _from_cache(source, cache, started)
        if cached is not None:
            return cached

    try:
        async with sem:
            response = await _get_with_retries(client, source, retries, backoff)
        result = _interpret(source, response, started)
    except httpx.TimeoutException:
        result = _error(source, started, "request timed out")
    except httpx.HTTPError as exc:
        result = _error(source, started, f"network error: {type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001 - last line of defence: one source must not kill the run
        log.debug("unexpected failure in %s", source.name, exc_info=True)
        result = _error(source, started, f"unexpected error: {type(exc).__name__}: {exc}")

    if cache is not None and result.status == "ok" and result.data is not None:
        cache.set(source.cache_key, result.data)
    return result


async def fetch_all(
    sources: Sequence[Source],
    *,
    cache: JsonCache | None = None,
    refresh: bool = False,
    concurrency: int = 5,
    timeout: float = 10.0,
    retries: int = 2,
    backoff: float = 0.5,
) -> list[FetchResult]:
    """Fetch every source concurrently. Results come back in the same order as `sources`."""
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "fetcher/0.1"}) as client:
        results = await asyncio.gather(
            *(
                fetch_one(
                    client,
                    source,
                    sem,
                    cache=cache,
                    refresh=refresh,
                    retries=retries,
                    backoff=backoff,
                )
                for source in sources
            )
        )

    if cache is not None:
        try:
            cache.save()
        except OSError as exc:
            log.warning("Could not write cache: %s", exc)
    return list(results)
