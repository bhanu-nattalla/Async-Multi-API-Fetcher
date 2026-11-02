"""Step 1: explore the three APIs and save raw responses as test fixtures.

Run from the project root:

    python explore.py                  # run every case
    python explore.py restcountries    # run only cases whose name starts with this

REST Countries v5 needs a free API key (https://restcountries.com, free tier = 500 requests/month).
Set it first (PowerShell):

    $env:REST_COUNTRIES_API_KEY = "your-key"

Cases that need the key are skipped if it isn't set.
Each response is saved to tests/fixtures/<case>.json. API keys are never saved.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

FIXTURES_DIR = Path("tests/fixtures")
TIMEOUT = httpx.Timeout(10.0)
RC_KEY = os.environ.get("REST_COUNTRIES_API_KEY")

# Change these to a location you care about.
LAT, LON = 51.5072, -0.1276

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
RC_V5 = "https://api.restcountries.com/countries/v5"
RC_LEGACY = "https://restcountries.com/v3.1/name/india"  # deprecated: kept as a fixture on purpose
OPENFDA_EVENTS = "https://api.fda.gov/drug/event.json"

Auth = Literal["none", "env_key", "bad_key"]


@dataclass(frozen=True)
class Case:
    name: str
    url: str
    params: dict[str, Any]
    note: str = ""
    auth: Auth = "none"
    secret_params: frozenset[str] = field(default_factory=lambda: frozenset({"api_key", "api-key"}))


def fda_params(drug: str) -> dict[str, Any]:
    return {
        "search": f'patient.drug.medicinalproduct:"{drug}"',
        "count": "patient.reaction.reactionmeddrapt.exact",
        "limit": 5,
    }


CASES: list[Case] = [
    # --- Open-Meteo ---
    Case(
        "open_meteo_ok",
        OPEN_METEO,
        {"latitude": LAT, "longitude": LON, "current": "temperature_2m,wind_speed_10m"},
        "Happy path.",
    ),
    Case(
        "open_meteo_bad_coords",
        OPEN_METEO,
        {"latitude": 999, "longitude": LON, "current": "temperature_2m,wind_speed_10m"},
        "Invalid latitude: HTTP 400 with {'error': true, 'reason': ...}.",
    ),
    # --- REST Countries v5 (Bearer auth, records under data.objects) ---
    Case(
        "restcountries_ok",
        RC_V5,
        {"q": "canada", "limit": 1, "response_fields_omit": "names.translations"},
        "Happy path, ONE full record. Read it to decide which fields your model needs.",
        auth="env_key",
    ),
    Case(
        "restcountries_search_many",
        RC_V5,
        {"q": "india", "limit": 5, "response_fields": "names.common,codes.alpha_2"},
        "Free-text search: how many matches come back, and what does the envelope look like?",
        auth="env_key",
    ),
    Case(
        "restcountries_no_capital",
        RC_V5,
        {"q": "antarctica", "response_fields": "names.common,codes.alpha_2,capitals"},
        "Antarctica: what does 'capitals' look like when there is none?",
        auth="env_key",
    ),
    Case(
        "restcountries_not_found",
        RC_V5,
        {"q": "zzzznotacountry", "response_fields": "names.common,codes.alpha_2"},
        "Nonsense query: is 'no match' a 404, or a 200 with an empty list?",
        auth="env_key",
    ),
    Case(
        "restcountries_no_key",
        RC_V5,
        {"q": "canada", "limit": 1, "response_fields": "names.common"},
        "No Authorization header at all.",
        auth="none",
    ),
    Case(
        "restcountries_bad_key",
        RC_V5,
        {"q": "canada", "limit": 1, "response_fields": "names.common"},
        "Invalid key.",
        auth="bad_key",
    ),
    Case(
        "restcountries_legacy_v3",
        RC_LEGACY,
        {},
        "Deprecated v3.1: returns HTTP 200 with success=false. "
        "Great fixture for 'status code lied'.",
    ),
    # --- openFDA ---
    Case(
        "openfda_ok",
        OPENFDA_EVENTS,
        fda_params("aspirin"),
        "Happy path: {'meta': ..., 'results': [{'term': ..., 'count': ...}]}.",
    ),
    Case(
        "openfda_no_results",
        OPENFDA_EVENTS,
        fda_params("zzzznotarealdrug123"),
        "Nonsense drug: HTTP 404 NOT_FOUND. A valid query with zero results, "
        "not a network failure.",
    ),
]


def build_headers(case: Case) -> dict[str, str] | None:
    """Return request headers, or None if this case must be skipped."""
    if case.auth == "env_key":
        return {"Authorization": f"Bearer {RC_KEY}"} if RC_KEY else None
    if case.auth == "bad_key":
        return {"Authorization": "Bearer THIS_IS_NOT_A_REAL_KEY"}
    return {}


def redact(params: dict[str, Any], secret_names: frozenset[str]) -> dict[str, Any]:
    return {k: ("REDACTED" if k in secret_names else v) for k, v in params.items()}


def pick_headers(response: httpx.Response) -> dict[str, str]:
    """Keep only the response headers worth knowing about (rate limits, content type)."""
    wanted_prefixes = ("x-ratelimit", "ratelimit", "retry-after", "content-type")
    return {k: v for k, v in response.headers.items() if k.lower().startswith(wanted_prefixes)}


def parse_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        # Not JSON (e.g. an HTML error page). Keep a bounded chunk of the text.
        return {"_non_json_body": response.text[:2000]}


def run_case(client: httpx.Client, case: Case, headers: dict[str, str]) -> dict[str, Any]:
    record: dict[str, Any] = {
        "case": case.name,
        "note": case.note,
        "request": {
            "url": case.url,
            "params": redact(case.params, case.secret_params),
            "auth": case.auth,  # we record the kind of auth, never the key itself
        },
    }
    try:
        response = client.get(case.url, params=case.params, headers=headers)
    except httpx.HTTPError as exc:
        # Network-level failure: DNS, timeout, connection refused, ...
        record["network_error"] = f"{type(exc).__name__}: {exc}"
        return record

    record["status_code"] = response.status_code
    record["headers"] = pick_headers(response)
    record["body"] = parse_body(response)
    return record


def save_fixture(record: dict[str, Any]) -> Path:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_DIR / f"{record['case']}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def preview(record: dict[str, Any], max_chars: int = 600) -> str:
    if "network_error" in record:
        return f"  NETWORK ERROR: {record['network_error']}"
    body = json.dumps(record["body"], indent=2, ensure_ascii=False)
    if len(body) > max_chars:
        body = body[:max_chars] + "\n  ... (truncated)"
    return body


def main() -> None:
    prefix = sys.argv[1] if len(sys.argv) > 1 else ""
    selected = [c for c in CASES if c.name.startswith(prefix)]
    if not selected:
        print(f"No cases start with {prefix!r}.")
        return

    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        for case in selected:
            print("=" * 72)
            print(case.name)
            if case.note:
                print(f"  note: {case.note}")
            headers = build_headers(case)
            if headers is None:
                print("  SKIPPED: set REST_COUNTRIES_API_KEY to run this case.\n")
                continue
            record = run_case(client, case, headers)
            path = save_fixture(record)
            print(f"  status: {record.get('status_code', 'n/a')}   saved: {path}")
            for key, value in record.get("headers", {}).items():
                print(f"  header {key}: {value}")
            print(preview(record))
            print()


if __name__ == "__main__":
    main()
