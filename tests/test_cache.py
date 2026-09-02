import json
import os
import time

import pytest

import schwarzplan_engine as engine

TIMED_OUT = {"elements": [], "remark": "runtime error: Query timed out in 'query' at line 3"}
EMPTY = {"elements": []}
GOOD = {"elements": [{"type": "node", "id": 1, "lat": 53.5, "lon": 9.9}]}


def test_timeout_response_is_reported_not_swallowed():
    problem = engine._response_problem(TIMED_OUT)
    assert problem is not None
    assert "timed out" in problem.lower()


def test_good_response_has_no_problem():
    assert engine._response_problem(GOOD) is None


def test_non_dict_response_is_rejected():
    assert engine._response_problem(["not", "a", "dict"]) is not None


def test_timeout_is_never_cached(tmp_path):
    """A cached timeout used to make the failure permanent for that location."""
    path = tmp_path / "c.json"
    engine._write_cache(str(path), TIMED_OUT)
    assert not path.exists()


def test_empty_result_is_never_cached(tmp_path):
    path = tmp_path / "c.json"
    engine._write_cache(str(path), EMPTY)
    assert not path.exists()


def test_good_response_round_trips(tmp_path):
    path = tmp_path / "c.json"
    engine._write_cache(str(path), GOOD)
    assert engine._read_cache(str(path)) == GOOD


def test_stale_cache_is_ignored(tmp_path):
    path = tmp_path / "c.json"
    engine._write_cache(str(path), GOOD)
    old = time.time() - engine.CACHE_TTL_SECONDS - 60
    os.utime(path, (old, old))
    assert engine._read_cache(str(path)) is None


def test_poisoned_cache_file_on_disk_is_ignored(tmp_path):
    """Existing empty cache files from earlier versions must not be trusted."""
    path = tmp_path / "c.json"
    path.write_text(json.dumps(EMPTY), encoding="utf-8")
    assert engine._read_cache(str(path)) is None


def test_missing_cache_file(tmp_path):
    assert engine._read_cache(str(tmp_path / "nope.json")) is None


def test_cache_dir_is_outside_the_application_bundle():
    """Writing next to __file__ fails inside a signed .app and breaks signing."""
    cache_dir = engine._get_cache_dir()
    source_dir = os.path.dirname(os.path.abspath(engine.__file__))
    assert not cache_dir.startswith(source_dir)
    assert os.path.isdir(cache_dir)


def test_all_mirrors_failing_raises_with_the_real_reason(monkeypatch, tmp_path):
    """The user should see why it failed, not a generic message."""
    monkeypatch.setattr(engine, "_get_cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr(engine, "OVERPASS_ENDPOINTS", ["https://example.invalid/api"])

    class FakeRequests:
        @staticmethod
        def post(*a, **k):
            raise OSError("Network is unreachable")

    monkeypatch.setitem(__import__("sys").modules, "requests", FakeRequests)

    with pytest.raises(RuntimeError, match="unreachable"):
        engine.fetch_osm_layers(53.5, 9.9, 100.0, include_buildings=True)


def test_mirrors_are_not_retried_twice(monkeypatch, tmp_path):
    """requests failing used to fall through and retry every mirror over urllib."""
    monkeypatch.setattr(engine, "_get_cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr(engine, "OVERPASS_ENDPOINTS", ["https://a.invalid", "https://b.invalid"])
    attempts = []

    class FakeRequests:
        @staticmethod
        def post(endpoint, **k):
            attempts.append(endpoint)
            raise OSError("boom")

    monkeypatch.setitem(__import__("sys").modules, "requests", FakeRequests)
    monkeypatch.setattr(
        engine.urllib.request, "urlopen",
        lambda *a, **k: pytest.fail("urllib retried mirrors that requests already tried"),
    )

    with pytest.raises(RuntimeError):
        engine.fetch_osm_layers(53.5, 9.9, 100.0, include_buildings=True)
    assert attempts == ["https://a.invalid", "https://b.invalid"]
