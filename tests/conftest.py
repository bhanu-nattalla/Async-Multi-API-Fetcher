from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
import respx

from async_multi_api_fetcher.sources import Source, open_meteo, openfda_reactions, rest_countries
from tests import samples


@pytest.fixture
def mock_api() -> Iterator[respx.MockRouter]:
    """Intercepts every httpx request. Unmocked requests raise, so no test can hit the internet."""
    with respx.mock(assert_all_called=False) as router:
        yield router


@dataclass
class Routes:
    weather: respx.Route
    country: respx.Route
    fda: respx.Route


@pytest.fixture
def routes(mock_api: respx.MockRouter) -> Routes:
    """All three APIs answering happily. Tests override one route to simulate a failure."""
    return Routes(
        weather=mock_api.get(host="api.open-meteo.com", path="/v1/forecast").mock(
            return_value=httpx.Response(200, json=samples.WEATHER_OK)
        ),
        country=mock_api.get(host="api.restcountries.com", path="/countries/v5").mock(
            return_value=httpx.Response(200, json=samples.COUNTRY_OK)
        ),
        fda=mock_api.get(host="api.fda.gov", path="/drug/event.json").mock(
            return_value=httpx.Response(200, json=samples.FDA_OK)
        ),
    )


@pytest.fixture
def sources() -> list[Source]:
    return [
        open_meteo(51.5, -0.12),
        rest_countries("canada", "test-key", 3),
        openfda_reactions("aspirin", 5),
    ]
