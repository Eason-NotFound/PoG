from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from pog_api.test_database import assert_safe_test_target, build_safe_test_engine


SAFE_URL = "postgresql+psycopg://pog_api:test-secret@127.0.0.1:55433/pog_api_test"
CI_ENVIRONMENT = {"CI": "true", "POG_TEST_TARGET_CONFIRMED": "github-actions"}


@pytest.fixture(autouse=True)
def clean_database():
    """No database fixtures or connections are used by this safety suite."""
    yield


@pytest.fixture
def engine_builder_calls(monkeypatch):
    calls = []
    sentinel = object()

    def builder(database_url):
        calls.append(database_url)
        return sentinel

    monkeypatch.setattr("pog_api.db.build_engine", builder)
    return calls, sentinel


def _marker(directory: Path, **updates):
    marker = {"managedBy": "pog-api-a1", "host": "127.0.0.1", "port": 55433,
              "testDatabase": "pog_api_test"}
    marker.update(updates)
    directory.mkdir()
    (directory / "managed.json").write_text(json.dumps(marker), encoding="utf-8")
    return {"POG_MANAGED_POSTGRES_STATE": str(directory)}


def test_managed_marker_and_explicit_ci_allow_only_the_test_builder(tmp_path, engine_builder_calls):
    calls, sentinel = engine_builder_calls
    environment = _marker(tmp_path / "owned")
    assert build_safe_test_engine(SAFE_URL, environ=environment) is sentinel
    assert build_safe_test_engine(SAFE_URL, environ=CI_ENVIRONMENT) is sentinel
    assert calls == [SAFE_URL, SAFE_URL]


@pytest.mark.parametrize("database_url", [
    None, "", "postgresql://pog_api@127.0.0.1:55433/pog_api_test",
    "sqlite:///pog_api_test", "postgresql+psycopg://postgres@127.0.0.1:55433/pog_api_test",
    "postgresql+psycopg://pog_admin@127.0.0.1:55433/pog_api_test",
    "postgresql+psycopg://pog_api@127.0.0.1:55433/pog_api_local",
    "postgresql+psycopg://pog_api@127.0.0.1:55433/postgres",
    "postgresql+psycopg://pog_api@example.invalid:55433/pog_api_test",
    "postgresql+psycopg://pog_api@0.0.0.0:55433/pog_api_test",
    "postgresql+psycopg://pog_api@localhost:55433/pog_api_test",
    "postgresql+psycopg://pog_api@127.0.0.1/pog_api_test",
    "postgresql+psycopg://pog_api@127.0.0.1:0/pog_api_test",
    "postgresql+psycopg://pog_api@127.0.0.1:invalid/pog_api_test",
    "postgresql+psycopg://pog_api@127.0.0.1:55433/pog_api_test?host=example.invalid",
    "postgresql+psycopg://pog_api@127.0.0.1:55433/pog_api_test?dbname=pog_api_local",
    "postgresql+psycopg://pog_api@127.0.0.1:55433/pog_api_test?port=65432",
    "postgresql+psycopg://pog_api@127.0.0.1:55433/pog_api_test?options=-csearch_path=other",
    "postgresql+psycopg://pog_api@127.0.0.1:55433/pog_api_test?",
    "postgresql+psycopg://pog_api@127.0.0.1:55433/pog_api_test#",
    "postgresql+psycopg://pog_api@127.0.0.1:55433/pog_api_test#override",
    "postgresql+psycopg://pog_api@127.0.0.1:55433/pog_api_test\n",
])
def test_unsafe_urls_fail_before_build_engine(database_url, engine_builder_calls):
    calls, _sentinel = engine_builder_calls
    with pytest.raises(RuntimeError):
        build_safe_test_engine(database_url, environ=CI_ENVIRONMENT)
    assert calls == []


@pytest.mark.parametrize("field,value", [
    ("managedBy", "other-project"), ("host", "example.invalid"),
    ("host", "localhost"), ("port", 55432), ("port", "55433"),
    ("port", True), ("testDatabase", "pog_api_local"),
])
def test_wrong_marker_fails_before_builder_without_ci_fallback(
    tmp_path, engine_builder_calls, field, value,
):
    calls, _sentinel = engine_builder_calls
    environment = {**CI_ENVIRONMENT, **_marker(tmp_path / "wrong", **{field: value})}
    with pytest.raises(RuntimeError, match="marker does not match"):
        build_safe_test_engine(SAFE_URL, environ=environment)
    assert calls == []


@pytest.mark.parametrize("contents", ["invalid json", "[]", "null"])
def test_invalid_marker_fails_before_builder(tmp_path, engine_builder_calls, contents):
    calls, _sentinel = engine_builder_calls
    state = tmp_path / "bad-marker"
    state.mkdir()
    (state / "managed.json").write_text(contents, encoding="utf-8")
    with pytest.raises(RuntimeError):
        build_safe_test_engine(SAFE_URL, environ={"POG_MANAGED_POSTGRES_STATE": str(state)})
    assert calls == []


def test_missing_or_symlink_marker_fails_before_builder(tmp_path, engine_builder_calls):
    calls, _sentinel = engine_builder_calls
    state = tmp_path / "state"
    state.mkdir()
    environment = {"POG_MANAGED_POSTGRES_STATE": str(state)}
    with pytest.raises(RuntimeError):
        build_safe_test_engine(SAFE_URL, environ=environment)
    target = tmp_path / "another-marker.json"
    target.write_text("{}", encoding="utf-8")
    (state / "managed.json").symlink_to(target)
    with pytest.raises(RuntimeError):
        build_safe_test_engine(SAFE_URL, environ=environment)
    assert calls == []


@pytest.mark.parametrize("environment", [
    {}, {"CI": "true"}, {"POG_TEST_TARGET_CONFIRMED": "github-actions"},
    {"CI": "false", "POG_TEST_TARGET_CONFIRMED": "github-actions"},
    {"CI": "true", "POG_TEST_TARGET_CONFIRMED": "somewhere-else"},
])
def test_absent_test_environment_proof_fails_before_builder(environment, engine_builder_calls):
    calls, _sentinel = engine_builder_calls
    with pytest.raises(RuntimeError, match="managed marker or explicit CI"):
        build_safe_test_engine(SAFE_URL, environ=environment)
    assert calls == []


@pytest.mark.parametrize("variable", [
    "PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER",
    "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS",
])
def test_libpq_environment_cannot_override_the_guarded_target(variable, engine_builder_calls):
    calls, _sentinel = engine_builder_calls
    with pytest.raises(RuntimeError, match="environment overrides"):
        build_safe_test_engine(SAFE_URL, environ={**CI_ENVIRONMENT, variable: "override"})
    assert calls == []


def test_live_module_rejects_business_database_before_manifest_or_engine(
    monkeypatch, engine_builder_calls,
):
    calls, _sentinel = engine_builder_calls
    monkeypatch.setenv("POG_TEST_DATABASE_URL", "postgresql+psycopg://pog_api@127.0.0.1:55433/pog_api_local")
    monkeypatch.delenv("POG_A2_LIVE_MANIFEST", raising=False)
    path = Path(__file__).resolve().parents[1] / "live_tests/test_a2_receipt_confirmed.py"
    spec = importlib.util.spec_from_file_location("unsafe_live_module_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(RuntimeError, match="dedicated pog_api_test"):
        spec.loader.exec_module(module)
    assert calls == []


def test_guard_runs_again_before_engine_even_after_collection(tmp_path, engine_builder_calls):
    calls, _sentinel = engine_builder_calls
    environment = _marker(tmp_path / "mutable-owned")
    assert_safe_test_target(SAFE_URL, environ=environment)
    marker_path = Path(environment["POG_MANAGED_POSTGRES_STATE"]) / "managed.json"
    marker_path.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="marker does not match"):
        build_safe_test_engine(SAFE_URL, environ=environment)
    assert calls == []
