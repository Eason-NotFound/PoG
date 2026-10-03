"""R11 cache parser tests: no engine, connection, SQL, or chain is used."""
from __future__ import annotations

import json

import pytest

from pog_api.idempotency import persisted_verified_namespace


@pytest.fixture(autouse=True)
def clean_database():
    # Override the parent PG fixture: this module must not construct an engine.
    yield


class NoQuerySession:
    def scalar(self, *_args, **_kwargs):
        raise AssertionError("Invalid manifest identity reached a SQL query")


INVALID_SHAPES = [
    [], None, "manifest", 1,
    {"runId": "run", "chain": []}, {"runId": "run", "chain": None},
    {"runId": "run", "chain": "chain"}, {"runId": "run", "chain": 1},
    {"runId": {}, "chain": {"instanceId": "instance"}},
    {"runId": [], "chain": {"instanceId": "instance"}},
    {"runId": 1, "chain": {"instanceId": "instance"}},
    {"runId": None, "chain": {"instanceId": "instance"}},
    {"runId": "", "chain": {"instanceId": "instance"}},
    {"runId": "run", "chain": {"instanceId": {}}},
    {"runId": "run", "chain": {"instanceId": []}},
    {"runId": "run", "chain": {"instanceId": 1}},
    {"runId": "run", "chain": {"instanceId": None}},
    {"runId": "run", "chain": {"instanceId": ""}},
    {"chain": {"instanceId": "instance"}}, {"runId": "run", "chain": {}},
]


@pytest.mark.parametrize("manifest", INVALID_SHAPES)
def test_malformed_manifest_identity_is_rejected_before_sql(tmp_path, manifest):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert persisted_verified_namespace(NoQuerySession(), path) is None


@pytest.mark.parametrize("kind", ["none", "missing", "invalid_json", "symlink"])
def test_absent_or_untrusted_manifest_is_rejected_before_sql(tmp_path, kind):
    path = tmp_path / "manifest.json"
    if kind == "none":
        path = None
    elif kind == "invalid_json":
        path.write_text("{", encoding="utf-8")
    elif kind == "symlink":
        target = tmp_path / "real.json"
        target.write_text('{"runId":"run","chain":{"instanceId":"instance"}}')
        path.symlink_to(target)
    assert persisted_verified_namespace(NoQuerySession(), path) is None


def test_valid_identity_with_no_verified_cache_returns_none_without_engine(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('{"runId":"run","chain":{"instanceId":"instance"}}')

    class EmptyCacheSession:
        calls = 0

        def scalar(self, statement):
            self.calls += 1
            params = statement.compile().params
            assert "run" in params.values() and "instance" in params.values()
            assert all(not isinstance(value, (dict, list)) for value in params.values())
            return None

    session = EmptyCacheSession()
    assert persisted_verified_namespace(session, path) is None
    assert session.calls == 1
