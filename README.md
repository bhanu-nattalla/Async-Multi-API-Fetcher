# async-multi-api-fetcher: async multi-API CLI

Concurrently queries **Open-Meteo** (weather), **REST Countries v5** (country facts) and
**openFDA** (drug adverse-event counts), validates every response into typed models, caches
successful results to a JSON file, and reports each source's outcome independently.

## Setup (Windows PowerShell)

```powershell
uv sync                                   # creates .venv, installs deps + dev tools
Copy-Item .env.example .env               # then edit .env and paste your key
```

Get a free key at https://restcountries.com (500 requests/month). The app reads
`REST_COUNTRIES_API_KEY` from the environment or from a `.env` file in the folder you run
it from. **A real environment variable always wins over `.env`**, so
`$env:REST_COUNTRIES_API_KEY = "..."` overrides the file for one session. Use
`--env-file PATH` to load a different file.

`.env` is gitignored: never commit it. Save it as UTF-8 (VS Code does this by default;
PowerShell 5's `echo KEY=value > .env` writes UTF-16 and will not work).

## Usage

```powershell
uv run async-multi-api-fetcher                                    # all three sources, defaults
uv run async-multi-api-fetcher --sources weather fda              # only some sources
uv run async-multi-api-fetcher --lat 17.38 --lon 78.48 --country japan --drug ibuprofen
uv run async-multi-api-fetcher --output json                      # machine-readable
uv run async-multi-api-fetcher --refresh                          # ignore cache, fetch fresh, update cache
uv run async-multi-api-fetcher --no-cache                         # don't touch the cache at all
uv run async-multi-api-fetcher --clear-cache
uv run async-multi-api-fetcher --lat 999                          # watch ONE source fail while the others succeed
uv run python -m async-multi-api-fetcher -v                       # verbose logging on stderr
```

Exit codes: `0` all sources answered (a valid "no results" counts), `1` at least one source
failed, `2` bad usage.

## Layout

| File | Responsibility |
|---|---|
| `src/async_multi_api_fetcher/models.py` | Pydantic models for each API payload + the uniform `FetchResult` |
| `src/async_multi_api_fetcher/sources.py` | Everything API-specific: URLs, params, auth, error interpretation, summaries |
| `src/async_multi_api_fetcher/client.py` | Generic async engine: concurrency, retries, failure isolation, cache use |
| `src/async_multi_api_fetcher/cache.py` | JSON file cache with TTL and atomic writes |
| `src/async_multi_api_fetcher/config.py` | Loads `.env` into the environment (never overrides real env vars) |
| `src/async_multi_api_fetcher/render.py` | Text / JSON output |
| `src/async_multi_api_fetcher/cli.py` | argparse, wiring, exit codes |
| `explore.py` | Step-1 script: calls the real APIs and saves fixtures to `tests/fixtures/` |
| `tests/` | Offline tests (`respx` mocks), no internet needed |

## Quality gates

```powershell
uv run pytest
uv run mypy
uv run ruff check .
uv run ruff format .
```

## Design decisions worth remembering

- **One shared `httpx.AsyncClient`** (connection pooling) and a `Semaphore` to cap concurrency.
- **Failure isolation:** `fetch_one` never raises; every problem becomes `FetchResult(status="error")`.
- **HTTP status is not enough.** REST Countries' deprecated versions answered `200` with
  `success: false`; openFDA answers `404` for "no matches". Each `Source.classify` interprets
  its own API's conventions.
- **Secrets stay out of the cache:** the key travels in a header and the cache key is built
  only from name + URL + params.
- **Only successes are cached.** Errors and "no results" are re-checked on the next run.
- **Atomic cache writes** (temp file + `os.replace`) and a corrupt cache is treated as empty.
- **Policy choice:** "no results" is not a failure (see `exit_code` in `cli.py`).

## Adding a fourth API

1. `models.py`: add the payload model(s).
2. `sources.py`: add `classify_x`, `summarize_x`, a factory, and register it in `build_sources`
   and `SOURCE_NAMES`.
3. `cli.py`: add any new flags and pass them into `build_sources`.
4. `tests/`: add a sample body and a success/failure test.
`client.py`, `cache.py` and `render.py` need no changes.