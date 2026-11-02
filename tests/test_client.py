from __future__ import annotations

from pathlib import Path

import httpx

from async_multi_api_fetcher.cache import JsonCache
from async_multi_api_fetcher.client import fetch_all
from async_multi_api_fetcher.sources import Source, open_meteo, openfda_reactions, rest_countries
from tests import samples
from tests.conftest import Routes

FAST = {"retries": 0, "backoff": 0.0}


async def test_all_three_succeed_in_order(sources: list[Source], routes: Routes) -> None:
    results = await fetch_all(sources, **FAST)
    assert [r.source for r in results] == ["weather", "country", "fda"]
    assert [r.status for r in results] == ["ok", "ok", "ok"]
    assert results[1].summary is not None and "Ottawa" in results[1].summary
    assert results[2].summary is not None and "Fatigue" in results[2].summary


async def test_one_failure_does_not_break_the_others(sources: list[Source], routes: Routes) -> None:
    routes.weather.mock(return_value=httpx.Response(400, json=samples.WEATHER_BAD_LAT))
    results = await fetch_all(sources, **FAST)
    assert results[0].status == "error"
    assert results[0].error is not None and "Latitude" in results[0].error
    assert results[1].status == "ok"
    assert results[2].status == "ok"


async def test_openfda_404_is_no_results_not_error(sources: list[Source], routes: Routes) -> None:
    routes.fda.mock(return_value=httpx.Response(404, json=samples.FDA_NOT_FOUND))
    results = await fetch_all(sources, **FAST)
    assert results[2].status == "no_results"
    assert results[2].error is None


async def test_http_200_with_success_false_is_an_error(
    sources: list[Source], routes: Routes
) -> None:
    routes.country.mock(return_value=httpx.Response(200, json=samples.COUNTRY_LEGACY_DEPRECATED))
    results = await fetch_all(sources, **FAST)
    assert results[1].status == "error"
    assert results[1].error is not None and "deprecated" in results[1].error


async def test_missing_api_key_fails_without_any_request(routes: Routes) -> None:
    results = await fetch_all([rest_countries("canada", None, 3)], **FAST)
    assert results[0].status == "error"
    assert results[0].error is not None and "REST_COUNTRIES_API_KEY" in results[0].error
    assert routes.country.call_count == 0


async def test_api_key_is_sent_as_bearer_header(routes: Routes) -> None:
    await fetch_all([rest_countries("canada", "secret-123", 3)], **FAST)
    request = routes.country.calls.last.request
    assert request.headers["Authorization"] == "Bearer secret-123"
    assert "secret-123" not in str(request.url)


async def test_unexpected_shape_is_a_validation_error_result(routes: Routes) -> None:
    routes.weather.mock(return_value=httpx.Response(200, json={"hello": "world"}))
    results = await fetch_all([open_meteo(1, 1)], **FAST)
    assert results[0].status == "error"
    assert results[0].error is not None and "unexpected response shape" in results[0].error


async def test_non_json_body_is_an_error(routes: Routes) -> None:
    routes.fda.mock(return_value=httpx.Response(200, text="<html>maintenance</html>"))
    results = await fetch_all([openfda_reactions("aspirin", 5)], **FAST)
    assert results[0].status == "error"
    assert results[0].error is not None and "not valid JSON" in results[0].error


async def test_missing_capital_is_handled(routes: Routes) -> None:
    routes.country.mock(return_value=httpx.Response(200, json=samples.COUNTRY_NO_CAPITAL))
    results = await fetch_all([rest_countries("antarctica", "k", 1)], **FAST)
    assert results[0].status == "ok"
    assert results[0].summary is not None and "capital none" in results[0].summary


async def test_retry_then_success(routes: Routes) -> None:
    routes.fda.mock(side_effect=[httpx.Response(503), httpx.Response(200, json=samples.FDA_OK)])
    results = await fetch_all([openfda_reactions("aspirin", 5)], retries=2, backoff=0.0)
    assert results[0].status == "ok"
    assert routes.fda.call_count == 2


async def test_retries_exhausted(routes: Routes) -> None:
    routes.fda.mock(side_effect=lambda request: httpx.Response(503))
    results = await fetch_all([openfda_reactions("aspirin", 5)], retries=2, backoff=0.0)
    assert results[0].status == "error"
    assert results[0].error == "HTTP 503"
    assert routes.fda.call_count == 3  # first try + 2 retries


async def test_client_errors_are_not_retried(routes: Routes) -> None:
    routes.weather.mock(return_value=httpx.Response(400, json=samples.WEATHER_BAD_LAT))
    await fetch_all([open_meteo(999, 1)], retries=3, backoff=0.0)
    assert routes.weather.call_count == 1


async def test_timeout_becomes_an_error_result(routes: Routes) -> None:
    routes.weather.mock(side_effect=httpx.ConnectTimeout("too slow"))
    results = await fetch_all([open_meteo(1, 1)], **FAST)
    assert results[0].status == "error"
    assert results[0].error is not None and "timed out" in results[0].error


# ---- caching -------------------------------------------------------------------------


async def test_second_run_is_served_from_cache(
    tmp_path: Path, sources: list[Source], routes: Routes
) -> None:
    path = tmp_path / "cache.json"
    first = await fetch_all(sources, cache=JsonCache(path, ttl_seconds=60), **FAST)
    assert not any(r.from_cache for r in first)

    # A brand-new JsonCache instance proves the data really went to disk.
    second = await fetch_all(sources, cache=JsonCache(path, ttl_seconds=60), **FAST)
    assert all(r.from_cache for r in second)
    assert [r.summary for r in second] == [r.summary for r in first]
    assert routes.weather.call_count == 1
    assert routes.country.call_count == 1
    assert routes.fda.call_count == 1


async def test_refresh_ignores_cache_but_updates_it(
    tmp_path: Path, sources: list[Source], routes: Routes
) -> None:
    path = tmp_path / "cache.json"
    await fetch_all(sources, cache=JsonCache(path, ttl_seconds=60), **FAST)
    again = await fetch_all(sources, cache=JsonCache(path, ttl_seconds=60), refresh=True, **FAST)
    assert not any(r.from_cache for r in again)
    assert routes.weather.call_count == 2


async def test_errors_and_empty_results_are_not_cached(
    tmp_path: Path, sources: list[Source], routes: Routes
) -> None:
    path = tmp_path / "cache.json"
    routes.weather.mock(return_value=httpx.Response(400, json=samples.WEATHER_BAD_LAT))
    routes.fda.mock(return_value=httpx.Response(404, json=samples.FDA_NOT_FOUND))
    await fetch_all(sources, cache=JsonCache(path, ttl_seconds=60), **FAST)

    again = await fetch_all(sources, cache=JsonCache(path, ttl_seconds=60), **FAST)
    assert [r.from_cache for r in again] == [False, True, False]  # only the country succeeded
    assert routes.weather.call_count == 2
    assert routes.fda.call_count == 2


async def test_cache_file_never_contains_the_api_key(tmp_path: Path, routes: Routes) -> None:
    path = tmp_path / "cache.json"
    await fetch_all(
        [rest_countries("canada", "super-secret-key", 3)],
        cache=JsonCache(path, ttl_seconds=60),
        **FAST,
    )
    assert "super-secret-key" not in path.read_text(encoding="utf-8")
