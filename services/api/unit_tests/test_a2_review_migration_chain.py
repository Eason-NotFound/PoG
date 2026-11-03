"""No-engine compatibility checks for immutable published 003 + additive 004."""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import Mock

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest


API_ROOT = Path(__file__).resolve().parents[1]
PUBLISHED_003_SHA256 = "fabff2145aab986001b635cb84bed96641cef996ace15379f909979c0fe32665"


def _scripts():
    return ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))


def test_published_003_migration_bytes_are_preserved():
    path = API_ROOT / "migrations/versions/c31003a20003_signing_expiry.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == PUBLISHED_003_SHA256
    assert not (path.parent / "c31003a20003_a2_review_guards.py").exists()


def test_review_head_is_single_linear_addition_after_published_003():
    scripts = _scripts()
    assert scripts.get_heads() == ["c31004a30006"]
    assert scripts.get_revision("c31004a30006").down_revision == "c31003a30005"
    assert scripts.get_revision("c31003a30005").down_revision == "c31003a20004"
    assert scripts.get_revision("c31003a20004").down_revision == "c31003a20003"
    assert scripts.get_revision("c31003a20003").down_revision == "c31003a20002"
    assert [item.revision for item in scripts.iterate_revisions("c31003a20004", "c31003a20002")] == [
        "c31003a20004", "c31003a20003",
    ]


def test_review_upgrade_replaces_existing_partial_index_not_unique_constraint(monkeypatch):
    migration = _scripts().get_revision("c31003a20004").module
    operations = Mock()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()
    assert operations.mock_calls[0].args == ("uq_signing_request_nonce_family",)
    operations.drop_index.assert_called_once_with("uq_signing_request_nonce_family", table_name="signing_requests")
    assert not any(call.args and call.args[0] == "uq_signing_request_nonce_family"
                   for call in operations.drop_constraint.call_args_list)
    signing_index = next(call for call in operations.create_index.call_args_list
                         if call.args[0] == "uq_signing_request_nonce_family")
    assert signing_index.kwargs["unique"] is True
    assert str(signing_index.kwargs["postgresql_where"]) == (
        "status NOT IN ('expired','invalidated_stale','invalidated_instance','invalidated_not_broadcast')"
    )


def test_review_downgrade_refuses_without_queries_or_history_mutation(monkeypatch):
    migration = _scripts().get_revision("c31003a20004").module
    operations = Mock(side_effect=AssertionError("004 rollback must not touch history"))
    monkeypatch.setattr(migration, "op", operations)
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()
    assert operations.mock_calls == []


def test_published_003_downgrade_keeps_expired_history_and_refuses(monkeypatch):
    migration = _scripts().get_revision("c31003a20003").module
    operations = Mock()
    operations.get_bind.return_value.scalar.return_value = True
    monkeypatch.setattr(migration, "op", operations)
    with pytest.raises(RuntimeError, match="retained expired"):
        migration.downgrade()
    operations.drop_index.assert_not_called()
    operations.drop_constraint.assert_not_called()
    operations.create_unique_constraint.assert_not_called()


def test_empty_published_003_downgrade_restores_original_unique_constraint(monkeypatch):
    migration = _scripts().get_revision("c31003a20003").module
    operations = Mock()
    operations.get_bind.return_value.scalar.return_value = False
    monkeypatch.setattr(migration, "op", operations)
    migration.downgrade()
    operations.drop_index.assert_called_once_with("uq_signing_request_nonce_family", table_name="signing_requests")
    operations.create_unique_constraint.assert_called_once_with(
        "uq_signing_request_nonce_family", "signing_requests",
        ["namespace_id", "contract_address", "signer_wallet", "nonce_text", "kind"],
    )


def test_diagnostic_upgrade_adds_only_private_table_and_immutable_history(monkeypatch):
    migration = _scripts().get_revision("c31004a30006").module
    operations = Mock()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()
    operations.create_table.assert_called_once()
    assert operations.create_table.call_args.args[0] == "ai_diagnostics"
    sql = operations.execute.call_args.args[0]
    assert "terminal diagnostic report is immutable" in sql
    assert "diagnostic input scope is immutable" in sql
    operations.add_column.assert_not_called()
    operations.drop_table.assert_not_called()
    with pytest.raises(RuntimeError, match="forward-only"):
        migration.downgrade()
    operations.drop_table.assert_not_called()
