"""Typed models.

Two kinds of models live here:

1. Payload models: what we *expect* each API to send back. Pydantic validates the raw JSON
   against them, so a changed or broken response fails at the boundary with a clear message
   instead of a KeyError ten functions later. Unknown extra fields are ignored on purpose:
   we only model what we use.
2. FetchResult: the one uniform shape the rest of the program deals with, no matter which
   API produced it or whether it worked.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------------------
# Open-Meteo
# --------------------------------------------------------------------------------------
class WeatherCurrent(BaseModel):
    time: str
    temperature_2m: float
    wind_speed_10m: float


class WeatherUnits(BaseModel):
    temperature_2m: str = "°C"
    wind_speed_10m: str = "km/h"


class WeatherResponse(BaseModel):
    latitude: float
    longitude: float
    current: WeatherCurrent
    current_units: WeatherUnits = Field(default_factory=WeatherUnits)


# --------------------------------------------------------------------------------------
# REST Countries v5 (JSON:API-style envelope; records live under data.objects)
#
# Only fields we are confident about are modelled. After reading tests/fixtures/
# restcountries_ok.json, add more here (and to COUNTRY_FIELDS in sources.py).
# --------------------------------------------------------------------------------------


class CountryNames(BaseModel):
    common: str
    official: str | None = None


class CountryCodes(BaseModel):
    alpha_2: str | None = None
    alpha_3: str | None = None


class Capital(BaseModel):
    name: str
    primary: bool | None = None


class Flag(BaseModel):
    emoji: str | None = None


class Country(BaseModel):
    names: CountryNames
    codes: CountryCodes = Field(default_factory=CountryCodes)
    capitals: list[Capital] = Field(default_factory=list)
    region: str | None = None
    subregion: str | None = None
    flag: Flag | None = None

    @field_validator("capitals", mode="before")
    @classmethod
    def _none_means_empty(cls, value: Any) -> Any:
        # Some territories (e.g. Antarctica) may report no capital as null instead of [].
        return [] if value is None else value


class CountryPage(BaseModel):
    objects: list[Country] = Field(default_factory=list)


class ApiMessage(BaseModel):
    message: str


class CountriesResponse(BaseModel):
    success: bool | None = None
    data: CountryPage | None = None
    errors: list[ApiMessage] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# openFDA drug adverse events (count query)
# --------------------------------------------------------------------------------------


class FdaMeta(BaseModel):
    last_updated: str | None = None


class FdaTermCount(BaseModel):
    term: str
    count: int


class FdaResponse(BaseModel):
    meta: FdaMeta | None = None
    results: list[FdaTermCount] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# Uniform result
# --------------------------------------------------------------------------------------

Status = Literal["ok", "no_results", "error"]


class FetchResult(BaseModel):
    """What one source produced in one run. Exactly one of data / error is meaningful."""

    source: str
    status: Status
    data: dict[str, Any] | None = None  # validated payload (JSON-safe), only when status == "ok"
    summary: str | None = None  # human-readable one-liner(s), only when status == "ok"
    error: str | None = None  # only when status == "error"
    from_cache: bool = False
    fetched_at: datetime  # when the data was fetched (original time for cache hits)
    elapsed_ms: int | None = None
