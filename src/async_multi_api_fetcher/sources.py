# which APIs, how to build URLs / parse
"""Everything that is specific to one API lives here and nowhere else.

A Source bundles: where to call, how to build the request, how to interpret the HTTP
response (classify), what model to validate it with, and how to describe it to a human.
client.py stays generic: it never contains an `if source == "..."` branch.

To add a fourth API: write a model in models.py, then a classify function, a summarize
function and a factory in this file, and register the factory in build_sources().
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from async_multi_api_fetcher.models import (
    CountriesResponse,
    FdaResponse,
    Status,
    WeatherResponse,
)

SOURCE_NAMES = ("weather", "country", "fda")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REST_COUNTRIES_URL = "https://api.restcountries.com/countries/v5"
OPENFDA_URL = "https://api.fda.gov/drug/event.json"

REST_COUNTRIES_KEY_ENV = "REST_COUNTRIES_API_KEY"

# Fields requested from REST Countries. Extend together with the models in models.py.
COUNTRY_FIELDS = (
    "names.common,names.official,codes.alpha_2,codes.alpha_3,capitals,region,subregion,flag.emoji"
)

# classify() answers: "did this HTTP response succeed, find nothing, or fail?" plus a message.
Verdict = tuple[Status, str | None]


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    params: dict[str, Any]
    model: type[BaseModel]
    classify: Callable[[int, Any], Verdict]
    summarize: Callable[[Any], str]
    is_empty: Callable[[Any], bool]
    headers: dict[str, str] = field(default_factory=dict)  # may contain secrets
    missing_secret: str | None = None  # name of the env var that should have supplied a key

    @property
    def cache_key(self) -> str:
        """Identity of a request: name + URL + params. Headers (secrets) are excluded on purpose,
        so credentials never reach the cache file and rotating a key does not invalidate it."""
        raw = json.dumps(
            {"name": self.name, "url": self.url, "params": self.params},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# --------------------------------------------------------------------------------------
# Open-Meteo
# --------------------------------------------------------------------------------------


def classify_open_meteo(status: int, body: Any) -> Verdict:
    # Errors arrive as HTTP 400 with {"error": true, "reason": "..."}.
    if isinstance(body, dict) and body.get("error"):
        return "error", str(body.get("reason") or f"HTTP {status}")
    if status != 200:
        return "error", f"HTTP {status}"
    return "ok", None


def summarize_weather(r: WeatherResponse) -> str:
    c, u = r.current, r.current_units
    return (
        f"{c.temperature_2m}{u.temperature_2m}, wind {c.wind_speed_10m} {u.wind_speed_10m} "
        f"(observed {c.time})"
    )


def _never_empty(_: Any) -> bool:
    return False


def open_meteo(lat: float, lon: float) -> Source:
    return Source(
        name="weather",
        url=OPEN_METEO_URL,
        params={"latitude": lat, "longitude": lon, "current": "temperature_2m,wind_speed_10m"},
        model=WeatherResponse,
        classify=classify_open_meteo,
        summarize=summarize_weather,
        is_empty=_never_empty,
    )


# --------------------------------------------------------------------------------------
# REST Countries v5
# --------------------------------------------------------------------------------------


def _rest_countries_message(body: Any) -> str | None:
    """Pull the first message out of the {"errors": [{"message": ...}]} envelope, if present."""
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            message = errors[0].get("message")
            if message:
                return str(message)
    return None


def classify_rest_countries(status: int, body: Any) -> Verdict:
    message = _rest_countries_message(body)
    if status in (401, 403):
        return "error", message or f"HTTP {status}: missing or invalid API key"
    if status == 404:
        return "no_results", None
    # The deprecated v1-v4 endpoints answered HTTP 200 with success=false. A status code
    # alone is therefore not enough: always look at the envelope too.
    if status != 200 or (isinstance(body, dict) and body.get("success") is False):
        return "error", message or f"HTTP {status}"
    return "ok", None


def summarize_countries(r: CountriesResponse) -> str:
    lines: list[str] = []
    for c in r.data.objects if r.data else []:
        emoji = c.flag.emoji if c.flag and c.flag.emoji else ""
        code = f" ({c.codes.alpha_2})" if c.codes.alpha_2 else ""
        capital = ", ".join(cap.name for cap in c.capitals) or "none"
        where = ", ".join(p for p in (c.subregion, c.region) if p) or "unknown region"
        lines.append(f"{emoji} {c.names.common}{code}: capital {capital}; {where}".strip())
    return "\n".join(lines)


def _countries_empty(r: CountriesResponse) -> bool:
    return r.data is None or not r.data.objects


def rest_countries(query: str, api_key: str | None, limit: int) -> Source:
    return Source(
        name="country",
        url=REST_COUNTRIES_URL,
        params={"q": query, "limit": limit, "response_fields": COUNTRY_FIELDS},
        model=CountriesResponse,
        classify=classify_rest_countries,
        summarize=summarize_countries,
        is_empty=_countries_empty,
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        missing_secret=None if api_key else REST_COUNTRIES_KEY_ENV,
    )


# --------------------------------------------------------------------------------------
# openFDA
# --------------------------------------------------------------------------------------


def classify_openfda(status: int, body: Any) -> Verdict:
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    # "No matches found" is HTTP 404 + code NOT_FOUND: a valid query with zero results,
    # which is not the same thing as the service being broken.
    if status == 404 and code == "NOT_FOUND":
        return "no_results", None
    if status != 200:
        return "error", str(message or f"HTTP {status}")
    return "ok", None


def _fda_summary(drug: str) -> Callable[[FdaResponse], str]:
    def summarize(r: FdaResponse) -> str:
        top = ", ".join(f"{t.term.title()} ({t.count:,})" for t in r.results)
        return (
            f"Most reported reactions for {drug}: {top}\n"
            "(raw adverse-event report counts, not medical advice)"
        )

    return summarize


def _fda_empty(r: FdaResponse) -> bool:
    return not r.results


def openfda_reactions(drug: str, limit: int) -> Source:
    drug = drug.replace('"', "").strip()
    return Source(
        name="fda",
        url=OPENFDA_URL,
        params={
            "search": f'patient.drug.medicinalproduct:"{drug}"',
            "count": "patient.reaction.reactionmeddrapt.exact",
            "limit": limit,
        },
        model=FdaResponse,
        classify=classify_openfda,
        summarize=_fda_summary(drug),
        is_empty=_fda_empty,
    )


# --------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------


def build_sources(
    selected: Sequence[str],
    *,
    lat: float,
    lon: float,
    country: str,
    drug: str,
    limit: int,
    rest_countries_key: str | None,
) -> list[Source]:
    factories: dict[str, Callable[[], Source]] = {
        "weather": lambda: open_meteo(lat, lon),
        "country": lambda: rest_countries(country, rest_countries_key, limit),
        "fda": lambda: openfda_reactions(drug, limit),
    }
    # dict.fromkeys removes duplicates while keeping the order the user gave.
    return [factories[name]() for name in dict.fromkeys(selected)]
