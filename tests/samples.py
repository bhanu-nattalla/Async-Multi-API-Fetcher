"""Small hand-written response bodies. Replace or complement them with the real captures in
tests/fixtures/*.json (created by explore.py): each fixture's "body" key is a real response."""

WEATHER_OK = {
    "latitude": 51.51,
    "longitude": -0.13,
    "current_units": {
        "time": "iso8601",
        "interval": "seconds",
        "temperature_2m": "°C",
        "wind_speed_10m": "km/h",
    },
    "current": {
        "time": "2026-11-02T10:45",
        "interval": 900,
        "temperature_2m": 9.6,
        "wind_speed_10m": 15.5,
    },
}

WEATHER_BAD_LAT = {
    "error": True,
    "reason": "Latitude must be in range of -90 to 90°. Given: 999.0.",
}

COUNTRY_OK = {
    "success": True,
    "data": {
        "objects": [
            {
                "names": {"common": "Canada", "official": "Canada"},
                "codes": {"alpha_2": "CA", "alpha_3": "CAN"},
                "capitals": [{"name": "Ottawa", "primary": True}],
                "region": "Americas",
                "subregion": "North America",
                "flag": {"emoji": "🇨🇦"},
            }
        ]
    },
    "errors": [],
}

COUNTRY_NO_CAPITAL = {
    "success": True,
    "data": {"objects": [{"names": {"common": "Antarctica"}, "capitals": None}]},
    "errors": [],
}

# What the deprecated v1-v4 endpoints return: HTTP 200, but the envelope says it failed.
COUNTRY_LEGACY_DEPRECATED = {
    "success": False,
    "data": None,
    "errors": [{"message": "This API version has been deprecated."}],
}

FDA_OK = {
    "meta": {"last_updated": "2026-07-30"},
    "results": [{"term": "FATIGUE", "count": 38671}, {"term": "NAUSEA", "count": 30000}],
}

FDA_NOT_FOUND = {"error": {"code": "NOT_FOUND", "message": "No matches found!"}}
