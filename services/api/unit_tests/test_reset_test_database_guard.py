from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest


SAFE_URL = "postgresql+psycopg://pog_api:synthetic@127.0.0.1:55433/pog_api_test"
LIBPQ_OVERRIDES = (
    "PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER",
    "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS",
)


@pytest.fixture
def reset_script():
    path = Path(__file__).resolve().parents[1] / "scripts/reset-test-db.py"
    spec = importlib.util.spec_from_file_location("reset_test_database_guard_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def owned_target(tmp_path, monkeypatch):
    for name in (*LIBPQ_OVERRIDES, "CI", "POG_TEST_TARGET_CONFIRMED"):
        monkeypatch.delenv(name, raising=False)
    state = tmp_path / "owned-postgres"
    state.mkdir()
    marker = {
        "managedBy": "pog-api-a1", "host": "127.0.0.1",
        "port": 55433, "testDatabase": "pog_api_test",
    }
    (state / "managed.json").write_text(json.dumps(marker), encoding="utf-8")
    monkeypatch.setenv("POG_ALLOW_TEST_DB_RESET", "1")
    monkeypatch.setenv("POG_TEST_DATABASE_URL", SAFE_URL)
    monkeypatch.setenv("POG_MANAGED_POSTGRES_STATE", str(state))
    return state, marker


@pytest.fixture
def blocked_entry(reset_script, owned_target, monkeypatch):
    # The shared guard has its own pathlib reference. Any construction here
    # means reset's marker resolution was attempted before target rejection.
    marker_path = Mock(side_effect=AssertionError("reset marker resolution was reached"))
    connect = Mock(side_effect=AssertionError("database driver was reached"))
    monkeypatch.setattr(reset_script, "Path", marker_path)
    monkeypatch.setattr(reset_script.psycopg, "connect", connect)
    return marker_path, connect


def _reject_before_reset(reset_script, blocked_entry, *, match=None):
    with pytest.raises(RuntimeError, match=match):
        reset_script.main()
    marker_path, connect = blocked_entry
    marker_path.assert_not_called()
    connect.assert_not_called()


@pytest.mark.parametrize("url", [
    None, "", "sqlite:///pog_api_test",
    "postgresql://pog_api@127.0.0.1:55433/pog_api_test",
    "postgresql+psycopg://postgres@127.0.0.1:55433/pog_api_test",
    "postgresql+psycopg://pog_api@127.0.0.1:55433/pog_api_local",
    "postgresql+psycopg://pog_api@example.invalid:55433/pog_api_test",
    "postgresql+psycopg://pog_api@localhost:55433/pog_api_test",
    "postgresql+psycopg://pog_api@127.0.0.1/pog_api_test",
    "postgresql+psycopg://pog_api@127.0.0.1:0/pog_api_test",
    "postgresql+psycopg://pog_api@127.0.0.1:invalid/pog_api_test",
    SAFE_URL + "?host=example.invalid", SAFE_URL + "?dbname=pog_api_local",
    SAFE_URL + "?", SAFE_URL + "#", SAFE_URL + "\n",
])
def test_reset_rejects_unsafe_url_before_marker_resolution_or_driver(
    reset_script, blocked_entry, monkeypatch, url,
):
    if url is None:
        monkeypatch.delenv("POG_TEST_DATABASE_URL")
    else:
        monkeypatch.setenv("POG_TEST_DATABASE_URL", url)
    _reject_before_reset(reset_script, blocked_entry)


@pytest.mark.parametrize("name", LIBPQ_OVERRIDES)
def test_reset_rejects_libpq_overrides_before_marker_resolution_or_driver(
    reset_script, blocked_entry, monkeypatch, name,
):
    monkeypatch.setenv(name, "unsafe-override")
    _reject_before_reset(reset_script, blocked_entry, match="environment overrides")


@pytest.mark.parametrize("field,value", [
    ("managedBy", "other-project"), ("host", "example.invalid"),
    ("testDatabase", "pog_api_local"), ("port", 55432),
    ("port", "55433"), ("port", True),
])
def test_reset_rejects_wrong_marker_without_ci_fallback_before_driver(
    reset_script, blocked_entry, owned_target, monkeypatch, field, value,
):
    state, marker = owned_target
    marker[field] = value
    (state / "managed.json").write_text(json.dumps(marker), encoding="utf-8")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("POG_TEST_TARGET_CONFIRMED", "github-actions")
    _reject_before_reset(reset_script, blocked_entry, match="marker does not match")


@pytest.mark.parametrize("contents", ["not-json", "[]", "null"])
def test_reset_rejects_invalid_marker_before_marker_resolution_or_driver(
    reset_script, blocked_entry, owned_target, contents,
):
    state, _marker = owned_target
    (state / "managed.json").write_text(contents, encoding="utf-8")
    _reject_before_reset(reset_script, blocked_entry)


def test_reset_rejects_missing_marker_before_marker_resolution_or_driver(
    reset_script, blocked_entry, owned_target,
):
    state, _marker = owned_target
    (state / "managed.json").unlink()
    _reject_before_reset(reset_script, blocked_entry)


@pytest.mark.parametrize("symlink_target", ["state", "marker"])
def test_reset_rejects_symlink_before_resolution_can_hide_it(
    reset_script, blocked_entry, owned_target, tmp_path, monkeypatch, symlink_target,
):
    state, _marker = owned_target
    if symlink_target == "state":
        alias = tmp_path / "state-alias"
        alias.symlink_to(state, target_is_directory=True)
        monkeypatch.setenv("POG_MANAGED_POSTGRES_STATE", str(alias))
    else:
        marker_path = state / "managed.json"
        original = state / "original-managed.json"
        marker_path.rename(original)
        marker_path.symlink_to(original)
    _reject_before_reset(reset_script, blocked_entry, match="regular owned-harness record")


@pytest.mark.parametrize("permission", [None, "0", "true"])
def test_reset_still_requires_explicit_reset_permission_before_target_checks(
    reset_script, blocked_entry, monkeypatch, permission,
):
    if permission is None:
        monkeypatch.delenv("POG_ALLOW_TEST_DB_RESET")
    else:
        monkeypatch.setenv("POG_ALLOW_TEST_DB_RESET", permission)
    guard = Mock(side_effect=AssertionError("target guard reached without explicit reset permission"))
    monkeypatch.setattr(reset_script, "assert_safe_test_target", guard)
    _reject_before_reset(reset_script, blocked_entry, match="POG_ALLOW_TEST_DB_RESET=1")
    guard.assert_not_called()


def test_reset_still_requires_managed_state_even_with_valid_ci_declaration(
    reset_script, blocked_entry, monkeypatch,
):
    monkeypatch.delenv("POG_MANAGED_POSTGRES_STATE")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("POG_TEST_TARGET_CONFIRMED", "github-actions")
    _reject_before_reset(reset_script, blocked_entry, match="POG_MANAGED_POSTGRES_STATE is required")


def test_owned_reset_keeps_dedicated_database_admin_commands_without_real_connection(
    reset_script, owned_target, monkeypatch,
):
    connection = Mock()
    connect = MagicMock()
    connect.return_value.__enter__.return_value = connection
    monkeypatch.setattr(reset_script.psycopg, "connect", connect)
    reset_script.main()
    connect.assert_called_once_with(
        "postgresql://pog_api:synthetic@127.0.0.1:55433/postgres", autocommit=True,
    )
    assert connection.execute.call_count == 3
    terminate, drop, create = connection.execute.call_args_list
    assert terminate.args == (
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        ("pog_api_test",),
    )
    assert drop.args[0].as_string() == 'DROP DATABASE IF EXISTS "pog_api_test"'
    assert create.args[0].as_string() == 'CREATE DATABASE "pog_api_test" OWNER "pog_api"'
