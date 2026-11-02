from __future__ import annotations

from typing import Any

import pytest

from async_multi_api_fetcher.sources import (
    classify_open_meteo,
    classify_openfda,
    classify_rest_countries,
    rest_countries,
)
from tests import samples


def test_cache_key_ignores_credentials() -> None:
    a = rest_countries("canada", "key-a", 3)
    b = rest_countries("canada", "key-b", 3)
    assert a.cache_key == b.cache_key
    assert "key-a" not in a.cache_key


def test_cache_key_changes_with_query() -> None:
    assert rest_countries("canada", "k", 3).cache_key != rest_countries("india", "k", 3).cache_key


@pytest.mark.parametrize(
    ("status", "body", "expected_status"),
    [
        (200, samples.WEATHER_OK, "ok"),
        (400, samples.WEATHER_BAD_LAT, "error"),
        (503, None, "error"),
    ],
)
def test_classify_open_meteo(status: int, body: Any, expected_status: str) -> None:
    assert classify_open_meteo(status, body)[0] == expected_status


@pytest.mark.parametrize(
    ("status", "body", "expected_status"),
    [
        (200, samples.COUNTRY_OK, "ok"),
        (200, samples.COUNTRY_LEGACY_DEPRECATED, "error"),  # HTTP 200 but success=false
        (401, None, "error"),
        (403, {"errors": [{"message": "bad key"}]}, "error"),
        (404, None, "no_results"),
        (500, None, "error"),
    ],
)
def test_classify_rest_countries(status: int, body: Any, expected_status: str) -> None:
    assert classify_rest_countries(status, body)[0] == expected_status


def test_rest_countries_error_message_is_extracted() -> None:
    _, message = classify_rest_countries(200, samples.COUNTRY_LEGACY_DEPRECATED)
    assert message is not None and "deprecated" in message


@pytest.mark.parametrize(
    ("status", "body", "expected_status"),
    [
        (200, samples.FDA_OK, "ok"),
        (404, samples.FDA_NOT_FOUND, "no_results"),
        (404, None, "error"),  # a 404 without the NOT_FOUND body is NOT "no results"
        (500, None, "error"),
    ],
)
def test_classify_openfda(status: int, body: Any, expected_status: str) -> None:
    assert classify_openfda(status, body)[0] == expected_status
