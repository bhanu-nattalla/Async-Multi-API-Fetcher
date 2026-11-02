# argparse + entry point
"""Command-line interface: parse arguments, wire everything together, print, set exit code.

Exit codes:  0 = every source answered (a valid "no results" counts as an answer)
             1 = at least one source failed
             2 = bad command-line usage (raised by argparse itself)
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from async_multi_api_fetcher.cache import JsonCache
from async_multi_api_fetcher.client import fetch_all
from async_multi_api_fetcher.models import FetchResult
from async_multi_api_fetcher.render import render_json, render_text
from async_multi_api_fetcher.sources import REST_COUNTRIES_KEY_ENV, SOURCE_NAMES, build_sources

DEFAULT_CACHE_PATH = Path.home() / ".cache" / "fetcher" / "cache.json"
DEFAULT_LAT, DEFAULT_LON = 51.5072, -0.1276  # change to a place you care about


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return number


def _positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fetcher",
        description="Concurrently query Open-Meteo, REST Countries and openFDA.",
        epilog=(
            f"REST Countries needs a free API key in the {REST_COUNTRIES_KEY_ENV} "
            "environment variable (it is deliberately not a command-line option, so it "
            "never ends up in your shell history)."
        ),
    )

    what = parser.add_argument_group("what to fetch")
    what.add_argument(
        "--sources",
        nargs="+",
        choices=SOURCE_NAMES,
        default=list(SOURCE_NAMES),
        metavar="SOURCE",
        help=f"which sources to query: {', '.join(SOURCE_NAMES)} (default: all)",
    )
    # Latitude/longitude are intentionally not range-checked here, so you can pass 999 and
    # watch the API's 400 become one isolated failure while the other sources still succeed.
    what.add_argument("--lat", type=float, default=DEFAULT_LAT, help="latitude for weather")
    what.add_argument("--lon", type=float, default=DEFAULT_LON, help="longitude for weather")
    what.add_argument("--country", default="india", help="country search text (default: india)")
    what.add_argument("--drug", default="aspirin", help="drug name for openFDA (default: aspirin)")
    what.add_argument(
        "--limit",
        type=_positive_int,
        default=5,
        help="max countries / max reaction terms to return (default: 5)",
    )

    cache = parser.add_argument_group("caching")
    cache.add_argument(
        "--ttl", type=_non_negative_int, default=3600, help="cache lifetime in seconds"
    )
    cache.add_argument("--cache-path", type=Path, default=DEFAULT_CACHE_PATH, help="cache file")
    mode = cache.add_mutually_exclusive_group()
    mode.add_argument("--no-cache", action="store_true", help="neither read nor write the cache")
    mode.add_argument(
        "--refresh", action="store_true", help="ignore cached data, fetch fresh, update the cache"
    )
    cache.add_argument("--clear-cache", action="store_true", help="delete the cache file first")

    net = parser.add_argument_group("network")
    net.add_argument("--timeout", type=_positive_float, default=10.0, help="seconds per request")
    net.add_argument(
        "--concurrency", type=_positive_int, default=5, help="max simultaneous requests"
    )
    net.add_argument(
        "--retries", type=_non_negative_int, default=2, help="retries on transient errors"
    )

    out = parser.add_argument_group("output")
    out.add_argument("--output", choices=("text", "json"), default="text", help="output format")
    out.add_argument("-v", "--verbose", action="store_true", help="debug logging on stderr")
    return parser


def _configure_output(verbose: bool) -> None:
    # Windows consoles often default to a legacy code page that cannot print flag emoji or "°C".
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            try:
                stream.reconfigure(encoding="utf-8")
            except ValueError:  # already used / not reconfigurable (e.g. under a test harness)
                pass
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.INFO if verbose else logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def exit_code(results: Sequence[FetchResult]) -> int:
    """Policy decision: "no results" is a valid answer, so only real errors make this 1.
    If you would rather treat empty results as a failure, change it here (one place)."""
    return 1 if any(r.status == "error" for r in results) else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_output(args.verbose)

    if args.clear_cache:
        JsonCache(args.cache_path, args.ttl).clear()

    cache = None if args.no_cache else JsonCache(args.cache_path, args.ttl)
    sources = build_sources(
        args.sources,
        lat=args.lat,
        lon=args.lon,
        country=args.country,
        drug=args.drug,
        limit=args.limit,
        rest_countries_key=os.environ.get(REST_COUNTRIES_KEY_ENV),
    )

    try:
        results = asyncio.run(
            fetch_all(
                sources,
                cache=cache,
                refresh=args.refresh,
                concurrency=args.concurrency,
                timeout=args.timeout,
                retries=args.retries,
            )
        )
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130

    print(render_json(results) if args.output == "json" else render_text(results))
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
