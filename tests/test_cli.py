from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from async_multi_api_fetcher.cli import main
from tests import samples
from tests.conftest import Routes


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REST_COUNTRIES_API_KEY", "test-key")


def test_text_output_and_exit_code_zero(
    routes: Routes, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--cache-path", str(tmp_path / "c.json"), "--retries", "0"])
    out = capsys.readouterr().out
    assert code == 0
    assert "[OK] weather" in out
    assert "[OK] country" in out
    assert "[OK] fda" in out
    assert "3 ok, 0 no results, 0 failed" in out


def test_json_output_is_parseable(
    routes: Routes, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--cache-path", str(tmp_path / "c.json"), "--retries", "0", "--output", "json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [item["source"] for item in data] == ["weather", "country", "fda"]


def test_any_failure_gives_exit_code_one(
    routes: Routes, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    routes.weather.mock(return_value=httpx.Response(400, json=samples.WEATHER_BAD_LAT))
    code = main(["--cache-path", str(tmp_path / "c.json"), "--retries", "0"])
    out = capsys.readouterr().out
    assert code == 1
    assert "[ERROR] weather" in out
    assert "[OK] country" in out  # the others still reported


def test_no_results_is_not_a_failure(
    routes: Routes, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    routes.fda.mock(return_value=httpx.Response(404, json=samples.FDA_NOT_FOUND))
    code = main(["--cache-path", str(tmp_path / "c.json"), "--retries", "0", "--sources", "fda"])
    assert code == 0
    assert "[NO RESULTS] fda" in capsys.readouterr().out


def test_no_cache_flag_does_not_write_a_cache_file(routes: Routes, tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    main(["--cache-path", str(path), "--retries", "0", "--no-cache"])
    assert not path.exists()


def test_invalid_usage_exits_with_two() -> None:
    with pytest.raises(SystemExit) as info:
        main(["--sources", "nonsense"])
    assert info.value.code == 2
