from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import psycopg
import pytest
from psycopg import sql
from sqlalchemy import inspect, text

from pog_api.adapters import AdapterRegistry, DependencyUnavailable
from pog_api.app import create_app
from pog_api.cli import seed_demo
from pog_api.config import Settings
from pog_api.db import build_engine, build_session_factory
from pog_api.models import User


def test_unavailable_adapters_never_claim_success():
    adapters = AdapterRegistry.a1_default()
    assert adapters.chain.status().available is False
    assert adapters.chain.status().verified is False
    assert adapters.ai.status().mode == "unavailable"
    assert adapters.payment.status().mode == "unavailable"
    try:
        adapters.chain.submit({"action": "createProject"})
    except DependencyUnavailable:
        pass
    else:
        raise AssertionError("Unavailable chain adapter returned success")
    wallet = adapters.wallet.request_authorization("project.create_draft")
    assert wallet == {
        "status": "awaiting_authorization",
        "action": "project.create_draft",
        "simulated": "true",
    }


def test_openapi_matches_implemented_paths_and_typed_envelopes(client):
    document = client.get("/openapi.json").json()
    expected = {
        "/health",
        "/ready",
        "/v2/deployment-config",
        "/v2/sessions",
        "/v2/me",
        "/v2/sessions/current",
        "/v2/projects",
        "/v2/projects/{project_id}",
        "/v2/procurements",
        "/v2/procurements/{procurement_id}",
        "/v2/documents",
        "/v2/documents/{document_id}",
        "/v2/documents/{document_id}/content",
        "/v2/operations/{operation_id}",
    }
    assert expected <= set(document["paths"])
    project_schema = document["paths"]["/v2/projects"]["post"]["responses"]["202"]["content"][
        "application/json"
    ]["schema"]
    assert project_schema["$ref"].endswith("ProjectMutationResponse")
    document_schema = document["paths"]["/v2/documents"]["post"]["responses"]["202"]["content"][
        "application/json"
    ]["schema"]
    assert document_schema["$ref"].endswith("DocumentMutationResponse")
    assert "No chain transactions" in document["info"]["description"]


def test_empty_migration_downgrade_reupgrade_and_ready_gate(tmp_path, monkeypatch):
    base = os.environ["POG_TEST_DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    parsed = urlsplit(base)
    database = "pog_api_migration_" + uuid4().hex[:12]
    admin_url = urlunsplit(parsed._replace(path="/postgres"))
    target_plain = urlunsplit(parsed._replace(path="/" + database))
    target = target_plain.replace("postgresql://", "postgresql+psycopg://", 1)
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(database), sql.Identifier("pog_api")
            )
        )
    try:
        settings = Settings(
            database_url=target,
            storage_root=tmp_path / "migration-storage",
            run_id="migration-run",
            instance_id="migration-instance",
        )
        with TestClient(create_app(settings)) as empty_client:
            response = empty_client.get("/ready")
            assert response.status_code == 503
            assert response.json()["ready"] is False
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        monkeypatch.setenv("POG_DATABASE_URL", target)
        command.upgrade(config, "c31003a20003")
        target_engine = build_engine(target)
        try:
            names = set(inspect(target_engine).get_table_names())
            assert {"users", "operations", "documents", "document_versions"} <= names
            with TestClient(create_app(settings)) as migrated_client:
                # Published migrations remain reversible, while this app now
                # requires the additive review head for database readiness.
                assert migrated_client.get("/ready").status_code == 503
            command.downgrade(config, "base")
            assert "users" not in set(inspect(target_engine).get_table_names())
            with TestClient(create_app(settings)) as downgraded_client:
                assert downgraded_client.get("/ready").status_code == 503
            command.upgrade(config, "84fcc48891be")
            namespace_id = uuid4()
            user_id = uuid4()
            with target_engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO deployment_instances "
                    "(id,schema_version,run_id,instance_id,mode,verified,active) "
                    "VALUES (:id,'a1','populated-run','populated-instance','mock',false,true)"
                ), {"id": namespace_id})
                connection.execute(text(
                    "INSERT INTO users (id,username,password_hash,display_name,active) "
                    "VALUES (:id,'preserved-a1-user','hash','Preserved A1 User',true)"
                ), {"id": user_id})
            command.upgrade(config, "head")
            with target_engine.connect() as connection:
                assert connection.scalar(text(
                    "SELECT username FROM users WHERE id=:id"
                ), {"id": user_id}) == "preserved-a1-user"
                assert connection.scalar(text(
                    "SELECT run_id FROM deployment_instances WHERE id=:id"
                ), {"id": namespace_id}) == "populated-run"
            assert {"signing_requests", "ledger_projections", "donor_credit_projections"} <= set(
                inspect(target_engine).get_table_names()
            )
            with TestClient(create_app(settings)) as reviewed_client:
                assert reviewed_client.get("/ready").status_code == 200
            with pytest.raises(RuntimeError, match="forward-only"):
                command.downgrade(config, "c31003a20003")
            with target_engine.connect() as connection:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "c31003a20004"
        finally:
            target_engine.dispose()
    finally:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database,),
            )
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))


def test_seed_is_opt_in_idempotent_and_never_rotates_passwords(
    monkeypatch, session_factory, engine
):
    monkeypatch.setenv("POG_DATABASE_URL", os.environ["POG_TEST_DATABASE_URL"])
    monkeypatch.setenv("POG_STORAGE_ROOT", "/tmp/pog-a1-seed-test-storage")
    values = {
        "FOUNDATION": ("foundation password 12345", "0x" + "1" * 40),
        "RECIPIENT": ("recipient password 12345", "0x" + "2" * 40),
        "DONOR": ("donor password value 123", "0x" + "3" * 40),
        "ADMIN": ("human password value 123", "0x" + "4" * 40),
    }
    for key, (password, wallet) in values.items():
        monkeypatch.setenv(f"POG_SEED_{key}_PASSWORD", password)
        monkeypatch.setenv(f"POG_SEED_{key}_WALLET", wallet)
    assert seed_demo(False) == 2
    assert seed_demo(True) == 0
    with session_factory() as session:
        before = {user.username: user.password_hash for user in session.query(User).all()}
    for key in values:
        monkeypatch.setenv(f"POG_SEED_{key}_PASSWORD", "new password should not replace")
    assert seed_demo(True) == 0
    with session_factory() as session:
        after = {user.username: user.password_hash for user in session.query(User).all()}
    assert after == before


def test_seed_rejects_zero_duplicate_and_placeholder(monkeypatch):
    monkeypatch.setenv("POG_DATABASE_URL", os.environ["POG_TEST_DATABASE_URL"])
    monkeypatch.setenv("POG_STORAGE_ROOT", "/tmp/pog-a1-seed-test-storage")
    for key in ("FOUNDATION", "RECIPIENT", "DONOR", "ADMIN"):
        monkeypatch.setenv(f"POG_SEED_{key}_PASSWORD", "valid long password")
        monkeypatch.setenv(f"POG_SEED_{key}_WALLET", "0x" + "1" * 40)
    assert seed_demo(True) == 2
    monkeypatch.setenv("POG_SEED_FOUNDATION_WALLET", "0x" + "0" * 40)
    monkeypatch.setenv("POG_SEED_RECIPIENT_WALLET", "0x" + "2" * 40)
    monkeypatch.setenv("POG_SEED_DONOR_WALLET", "0x" + "3" * 40)
    monkeypatch.setenv("POG_SEED_ADMIN_WALLET", "0x" + "4" * 40)
    assert seed_demo(True) == 2
    monkeypatch.setenv("POG_SEED_FOUNDATION_WALLET", "0x" + "1" * 40)
    monkeypatch.setenv("POG_SEED_FOUNDATION_PASSWORD", "CHANGE_ME_please")
    assert seed_demo(True) == 2
