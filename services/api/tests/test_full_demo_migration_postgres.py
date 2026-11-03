"""Forward-only 004->005 data preservation and case-folded nonce collision gates."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from pog_api.db import build_engine
from pog_api.test_database import assert_safe_test_target
from pog_api.test_db_safety import assert_safe_test_target as assert_managed_target


@contextmanager
def isolated_migration_database(monkeypatch):
    # Validate the owned, dedicated parent before any administrative connection.
    source = os.environ["POG_TEST_DATABASE_URL"]
    assert_safe_test_target(source)
    assert_managed_target(source, managed_state=os.getenv("POG_MANAGED_POSTGRES_STATE"),
                          ci=os.getenv("CI"), target_confirmed=os.getenv("POG_TEST_TARGET_CONFIRMED"))
    parsed = urlsplit(source.replace("postgresql+psycopg://", "postgresql://", 1))
    name = "pog_api_migration_" + uuid4().hex[:12]
    admin_url = urlunsplit(parsed._replace(path="/postgres"))
    target_plain = urlunsplit(parsed._replace(path="/" + name))
    target = target_plain.replace("postgresql://", "postgresql+psycopg://", 1)
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier("pog_api")))
    engine = build_engine(target)
    try:
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        monkeypatch.setenv("POG_DATABASE_URL", target)
        command.upgrade(config, "c31003a20004")
        yield engine, config
    finally:
        engine.dispose()
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (name,))
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def populate_004(engine, *, collision=False):
    ns, user, project, procurement = (uuid4() for _ in range(4))
    ids = []
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO deployment_instances(id,schema_version,run_id,instance_id,mode,verified,active) VALUES(:id,'a2-chain-1','migration-full-run','migration-full-instance','mock',false,true)"), {"id": ns})
        conn.execute(text("INSERT INTO users(id,username,password_hash,display_name,active) VALUES(:id,'migration-private-user','fixture-password-hash','Migration Fixture',true)"), {"id": user})
        conn.execute(text("INSERT INTO projects(id,namespace_id,business_id,title,public_summary,foundation_user_id,recipient_user_id,human_approver_user_id,foundation_wallet,recipient_wallet,asset_symbol,fairness_rule,chain_status) VALUES(:id,:ns,:business,'Migration Project','Public',:user,:user,:user,:wallet,:wallet,'mHKD','Fixture','active')"),
                     {"id": project, "ns": ns, "business": "0x" + "11" * 32, "user": user, "wallet": "0x" + "ab" * 20})
        conn.execute(text("INSERT INTO procurements(id,namespace_id,project_id,business_id,title,foundation_user_id,vendor_wallet,budget_cap_atomic,chain_status) VALUES(:id,:ns,:project,:business,'Migration Procurement',:user,:wallet,100000000,'created')"),
                     {"id": procurement, "ns": ns, "project": project, "business": "0x" + "22" * 32, "user": user, "wallet": "0x" + "bc" * 20})
        kinds = ["reserve", "reserve"] if collision else ["ai_pre", "receipt", "reserve"]
        for index, kind in enumerate(kinds):
            operation, request, step = uuid4(), uuid4(), uuid4()
            contract = "0x" + ("CD" if collision and index else "cd") * 20
            signer = "0x" + ("AB" if collision and index else "ab") * 20
            conn.execute(text("INSERT INTO operations(id,namespace_id,principal_id,operation_kind,idempotency_key,payload_hash,status,result_resource_type,result_resource_id) VALUES(:id,:ns,:user,'signing_request.fixture',:key,:hash,'awaiting_authorization','signing_request',:request)"),
                         {"id": operation, "ns": ns, "user": user, "key": f"historical-{index}", "hash": f"{index+1:064x}", "request": request})
            conn.execute(text("INSERT INTO operation_steps(id,operation_id,step_index,kind,status,detail) VALUES(:id,:op,0,'fixture.history','queued',CAST(:detail AS jsonb))"),
                         {"id": step, "op": operation, "detail": json.dumps({"immutable": f"historical-{index}"})})
            conn.execute(text("INSERT INTO signing_requests(id,namespace_id,operation_id,procurement_id,signer_user_id,kind,status,contract_address,signer_wallet,nonce_text,deadline_text,policy_epoch,typed_data,context_json,digest) VALUES(:id,:ns,:op,:proc,:user,:kind,'prepared',:contract,:signer,'0','2000',1,CAST(:typed AS jsonb),CAST(:context AS jsonb),:digest)"),
                         {"id": request, "ns": ns, "op": operation, "proc": procurement, "user": user, "kind": kind,
                          "contract": contract, "signer": signer, "typed": json.dumps({"historicalKind": kind}),
                          "context": json.dumps({"retained": index}), "digest": "0x" + f"{index+1:064x}"})
            conn.execute(text("INSERT INTO audit_logs(id,principal_id,operation_id,action,resource_type,resource_id,outcome,metadata_json) VALUES(:id,:user,:op,'historical.fixture','signing_request',:request,'prepared',CAST(:data AS jsonb))"),
                         {"id": uuid4(), "user": user, "op": operation, "request": request, "data": json.dumps({"original": index})})
            ids.append(request)
    return ids


def fingerprint(connection, table, *, added_fields=()):
    remove = "".join(f" - '{name}'" for name in added_fields)
    return connection.execute(text(f"SELECT id, to_jsonb(t){remove} FROM {table} t ORDER BY id")).all()


def test_populated_004_upgrade_retains_old_rows_hashes_audits_and_backfills_families(monkeypatch):
    with isolated_migration_database(monkeypatch) as (engine, config):
        populate_004(engine)
        tables = ("signing_requests", "operations", "audit_logs", "operation_steps", "procurements")
        with engine.connect() as conn:
            before = {table: fingerprint(conn, table) for table in tables}
        command.upgrade(config, "c31003a30005")
        added = {"signing_requests": ("nonce_family",), "operation_steps": ("executor",),
                 "procurements": ("final_evidence_hash", "final_assessment_id", "conversion_evidence_hash",
                                  "payment_evidence_hash", "settlement_hash", "returned_amount_atomic", "source_versions_json")}
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "c31003a30005"
            for table in tables:
                assert fingerprint(conn, table, added_fields=added.get(table, ())) == before[table]
            families = dict(conn.execute(text("SELECT kind,nonce_family FROM signing_requests")).all())
            assert families == {"ai_pre": "registry_ai", "receipt": "registry_recipient", "reserve": "escrow_human"}
            assert conn.scalar(text("SELECT count(*) FROM operation_steps WHERE executor='chain'")) == 3
        assert {"sim_hkd_accounts", "sim_hkd_journals", "payment_resources", "funded_claims", "payment_evidence", "payment_token_outflows"} <= set(inspect(engine).get_table_names())
        with pytest.raises(RuntimeError, match="forward-only"):
            command.downgrade(config, "c31003a20004")


def test_casefolded_nonce_collision_blocks_upgrade_without_rewriting_history(monkeypatch):
    with isolated_migration_database(monkeypatch) as (engine, config):
        populate_004(engine, collision=True)
        with engine.connect() as conn:
            before = fingerprint(conn, "signing_requests")
        with pytest.raises(DBAPIError):
            command.upgrade(config, "c31003a30005")
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "c31003a20004"
            assert fingerprint(conn, "signing_requests") == before
        assert "nonce_family" not in {row["name"] for row in inspect(engine).get_columns("signing_requests")}
