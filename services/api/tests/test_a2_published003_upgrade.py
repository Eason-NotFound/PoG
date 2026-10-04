"""Real PG: published 003 authorization history survives additive 004 intact."""
from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from alembic import command
from alembic.config import Config
from eth_account import Account
from eth_account.messages import encode_typed_data
import psycopg
from psycopg import sql
import pytest
from sqlalchemy import inspect, text

from pog_api.db import build_engine
from pog_api.hashing import payload_sha256
from pog_api.test_database import assert_safe_test_target
from pog_api.typed_data import digest, human_intent_typed


@pytest.fixture(autouse=True)
def clean_database():
    # This real-PG migration test owns a separate random database. Never truncate
    # or downgrade the shared application/test database through the parent fixture.
    yield


def test_populated_published003_authorizations_upgrade_to004_without_rewriting_history(monkeypatch):
    base_url = os.environ["POG_TEST_DATABASE_URL"]
    assert_safe_test_target(base_url)
    api_root = Path(__file__).resolve().parents[1]
    published = api_root / "migrations/versions/c31003a20003_signing_expiry.py"
    expected_sha256 = "fabff2145aab986001b635cb84bed96641cef996ace15379f909979c0fe32665"
    assert hashlib.sha256(published.read_bytes()).hexdigest() == expected_sha256
    parsed = urlsplit(base_url.replace("postgresql+psycopg://", "postgresql://", 1))
    database = "pog_api_003_history_" + uuid4().hex[:12]
    admin_url = urlunsplit(parsed._replace(path="/postgres"))
    target_plain = urlunsplit(parsed._replace(path="/" + database))
    target = target_plain.replace("postgresql://", "postgresql+psycopg://", 1)
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(database), sql.Identifier("pog_api"),
        ))
    engine = None
    try:
        config = Config(str(api_root / "alembic.ini"))
        monkeypatch.setenv("POG_DATABASE_URL", target)
        command.upgrade(config, "c31003a20003")
        engine = build_engine(target)
        ids = {name: uuid4() for name in (
            "namespace", "foundation", "recipient", "human", "wallet", "project", "procurement",
            "document", "version", "expired_operation", "live_operation", "expired_request", "live_request",
            "expired_audit", "live_audit",
        )}
        signer = Account.create()
        business = "0x" + "33" * 32
        contract = "0x" + "88" * 20
        authorized_at = datetime(2026, 10, 3, 1, 2, 3, tzinfo=UTC)
        requests = {}
        for state, deadline, ttl in (("expired", 1_000, 60), ("live", 2_000, 300)):
            typed = human_intent_typed({
                "targetId": business, "action": 0, "termsHash": "0x" + "44" * 32,
                "assessmentId": "0x" + "55" * 32, "signer": signer.address,
                "nonce": 7, "deadline": deadline, "policyEpoch": 1,
            }, 31337, contract)
            signed = Account.sign_message(encode_typed_data(full_message=typed), signer.key)
            requests[state] = {
                "typed": typed, "digest": digest(typed), "signature": "0x" + signed.signature.hex().removeprefix("0x"),
                "deadline": str(deadline), "body": {"kind": "reserve", "reserveAmountAtomic": "80000000",
                                                       "deadlineTtlSeconds": ttl},
            }
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO deployment_instances (id,schema_version,run_id,instance_id,mode,verified,active) "
                "VALUES (:namespace,'a2-chain-1','published003-history','immutable-source-instance','mock',false,true)"
            ), ids)
            connection.execute(text("INSERT INTO roles(name) VALUES ('human_approver') ON CONFLICT DO NOTHING"))
            for role in ("foundation", "recipient", "human"):
                connection.execute(text(
                    "INSERT INTO users (id,username,password_hash,display_name,active) "
                    "VALUES (:id,:name,'synthetic-preserved-password-hash','Synthetic migration fixture',true)"
                ), {"id": ids[role], "name": "published003-" + role})
            connection.execute(text(
                "INSERT INTO wallet_authorizations (id,user_id,role_name,wallet_address,active) "
                "VALUES (:wallet,:human,'human_approver',:signer,true)"
            ), {**ids, "signer": signer.address})
            connection.execute(text(
                "INSERT INTO projects (id,namespace_id,business_id,title,public_summary,foundation_user_id,"
                "recipient_user_id,human_approver_user_id,foundation_wallet,recipient_wallet,asset_symbol,"
                "fairness_rule,chain_status) VALUES (:project,:namespace,:business,'Preserved003 project',"
                "'Synthetic preserved source',:foundation,:recipient,:human,:fw,:rw,'mHKD','Original fairness','active')"
            ), {**ids, "business": "0x" + "22" * 32, "fw": "0x" + "11" * 20, "rw": "0x" + "66" * 20})
            connection.execute(text(
                "INSERT INTO procurements (id,namespace_id,project_id,business_id,title,foundation_user_id,"
                "vendor_wallet,budget_cap_atomic,chain_status,pre_evidence_hash,pre_assessment_id,"
                "reserved_amount_atomic,invoice_amount_atomic) VALUES (:procurement,:namespace,:project,"
                ":business,'Preserved003 procurement',:foundation,:vendor,80000000,'pre_assessed',:evidence,:assessment,0,0)"
            ), {**ids, "business": business, "vendor": "0x" + "77" * 20,
                "evidence": "0x" + "99" * 32, "assessment": "0x" + "55" * 32})
            connection.execute(text(
                "INSERT INTO documents (id,namespace_id,procurement_id,category,owner_user_id) "
                "VALUES (:document,:namespace,:procurement,'purchase_order',:foundation)"
            ), ids)
            connection.execute(text(
                "INSERT INTO document_versions (id,document_id,version,original_filename,content_type,size_bytes,"
                "sha256_hex,keccak256_hex,storage_key,uploaded_by_user_id,referenced) "
                "VALUES (:version,:document,1,'synthetic-preserved.pdf','application/pdf',1,:sha,:keccak,"
                "'synthetic-migration-only/no-private-file.pdf',:foundation,true)"
            ), {**ids, "sha": "aa" * 32, "keccak": "bb" * 32})
            for state in ("expired", "live"):
                request = requests[state]
                # Hank's published target-scoped signing hash; preserve it, do
                # not rewrite to the later resourceId/businessId hash format.
                historical_hash = payload_sha256({"procurementId": str(ids["procurement"]), "body": request["body"]})
                requests[state]["payload_hash"] = historical_hash
                values = {**ids, "operation": ids[state + "_operation"], "request": ids[state + "_request"],
                          "audit": ids[state + "_audit"], "key": "published003-" + state,
                          "payload": historical_hash, "signer": signer.address, "contract": contract,
                          "status": "expired" if state == "expired" else "signed", "deadline": request["deadline"],
                          "typed": json.dumps(request["typed"]), "digest": request["digest"], "signature": request["signature"],
                          "authorized": authorized_at, "context": json.dumps({
                              "projectUuid": str(ids["project"]), "procurementUuid": str(ids["procurement"]),
                              "reserveAmountAtomic": "80000000", "sourceDocumentVersionId": str(ids["version"]),
                          }), "metadata": json.dumps({
                              "explicitConfirm": True, "sourceDocumentVersionId": str(ids["version"]),
                              "originalPayloadHash": historical_hash, "preservedState": state,
                          })}
                connection.execute(text(
                    "INSERT INTO operations (id,namespace_id,principal_id,operation_kind,idempotency_key,payload_hash,"
                    "status,result_resource_type,result_resource_id) VALUES (:operation,:namespace,:human,"
                    "'signing_request.reserve',:key,:payload,'confirmed','signing_request',:request)"
                ), values)
                connection.execute(text(
                    "INSERT INTO signing_requests (id,namespace_id,operation_id,procurement_id,signer_user_id,kind,"
                    "status,contract_address,signer_wallet,nonce_text,deadline_text,policy_epoch,typed_data,context_json,"
                    "digest,signature,submitted_operation_id,authorized_at) VALUES (:request,:namespace,:operation,"
                    ":procurement,:human,'reserve',:status,:contract,:signer,'7',:deadline,1,CAST(:typed AS jsonb),"
                    "CAST(:context AS jsonb),:digest,:signature,NULL,:authorized)"
                ), values)
                connection.execute(text(
                    "INSERT INTO audit_logs (id,principal_id,operation_id,action,resource_type,resource_id,outcome,metadata_json) "
                    "VALUES (:audit,:human,:operation,'signing_request.published003_fixture','signing_request',"
                    ":request,'authorized',CAST(:metadata AS jsonb))"
                ), values)
        tables = ("deployment_instances", "users", "wallet_authorizations", "projects", "procurements",
                  "documents", "document_versions", "operations", "signing_requests", "audit_logs")
        columns = {table: [column["name"] for column in inspect(engine).get_columns(table)] for table in tables}

        def snapshot(connection):
            return {table: connection.execute(text(
                "SELECT " + ",".join(columns[table]) + " FROM " + table + " ORDER BY id"
            )).mappings().all() for table in tables}

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "c31003a20003"
            same_family = connection.execute(text(
                "SELECT status,nonce_text,contract_address,signer_wallet FROM signing_requests ORDER BY status"
            )).all()
            assert [row.status for row in same_family] == ["expired", "signed"]
            assert len({tuple(row[1:]) for row in same_family}) == 1
            before = snapshot(connection)
        command.upgrade(config, "c31003a20004")
        with engine.connect() as connection:
            assert snapshot(connection) == before
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "c31003a20004"
            assert connection.scalar(text("SELECT count(*) FROM signing_requests")) == 2
            assert connection.scalar(text("SELECT count(*) FROM operations")) == 2
            assert connection.scalar(text("SELECT count(*) FROM operations WHERE status IN ('queued','submitted')")) == 0
            for table in ("operation_steps", "chain_transactions", "policy_projections", "chain_events", "indexer_cursors"):
                assert connection.scalar(text("SELECT count(*) FROM " + table)) == 0
            for state in ("expired", "live"):
                historical = connection.execute(text(
                    "SELECT sr.typed_data,sr.digest,sr.deadline_text,sr.signature,sr.authorized_at,sr.context_json,"
                    "sr.operation_id,sr.procurement_id,sr.signer_user_id,o.payload_hash,o.result_resource_id "
                    "FROM signing_requests sr JOIN operations o ON o.id=sr.operation_id WHERE sr.id=:id"
                ), {"id": ids[state + "_request"]}).one()
                assert historical.typed_data == requests[state]["typed"]
                assert historical.digest == requests[state]["digest"]
                assert historical.deadline_text == requests[state]["deadline"]
                assert historical.signature == requests[state]["signature"]
                assert historical.authorized_at == authorized_at
                assert historical.payload_hash == requests[state]["payload_hash"]
                assert historical.operation_id == ids[state + "_operation"]
                assert historical.result_resource_id == ids[state + "_request"]
                assert historical.procurement_id == ids["procurement"] and historical.signer_user_id == ids["human"]
                assert historical.context_json["sourceDocumentVersionId"] == str(ids["version"])
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "c31003a30005"
        assert hashlib.sha256(published.read_bytes()).hexdigest() == expected_sha256
    finally:
        if engine is not None:
            engine.dispose()
        assert database.startswith("pog_api_003_history_") and database != "pog_api_test"
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                               "WHERE datname=%s AND pid<>pg_backend_pid()", (database,))
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
