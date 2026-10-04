from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pog_api.amounts import UINT256_MAX
from pog_api.db import build_engine
from pog_api.models import (
    DeploymentInstance, DonorCreditProjection, LedgerProjection, Operation,
    Procurement, Project, SigningRequest, User,
)
from pog_api.test_database import assert_safe_test_target


UINT64_MAX = 2**64 - 1
AMOUNT_COLUMNS = (
    ("procurements", "reserved_amount_atomic", "procurement"),
    ("procurements", "invoice_amount_atomic", "procurement"),
    ("ledger_projections", "deposits_atomic", "ledger"),
    ("ledger_projections", "reserved_atomic", "ledger"),
    ("ledger_projections", "released_atomic", "ledger"),
    ("ledger_projections", "returned_atomic", "ledger"),
    ("donor_credit_projections", "credit_atomic", "credit"),
)


@pytest.fixture
def persisted_a2(session_factory):
    with session_factory() as session, session.begin():
        users = [User(username=f"bounds-{name}", password_hash="synthetic-nonlogin-hash",
                      display_name=f"Synthetic bounds {name}", active=True)
                 for name in ("foundation", "recipient", "human")]
        session.add_all(users)
        namespace = DeploymentInstance(
            schema_version="a2-chain-1", run_id=str(uuid4()), instance_id=str(uuid4()),
            mode="mock", verified=False, active=True,
        )
        session.add(namespace)
        session.flush()
        project = Project(
            namespace_id=namespace.id, business_id="0x" + uuid4().hex * 2,
            title="Synthetic bounds project", public_summary="Persistence test only",
            foundation_user_id=users[0].id, recipient_user_id=users[1].id,
            human_approver_user_id=users[2].id, foundation_wallet="0x" + "11" * 20,
            recipient_wallet="0x" + "22" * 20, asset_symbol="mHKD", fairness_rule="Synthetic",
            chain_status="off_chain_draft",
        )
        session.add(project)
        session.flush()
        procurement = Procurement(
            namespace_id=namespace.id, project_id=project.id, business_id="0x" + uuid4().hex * 2,
            title="Synthetic bounds procurement", foundation_user_id=users[0].id,
            vendor_wallet="0x" + "33" * 20, budget_cap_atomic=Decimal(100),
            reserved_amount_atomic=Decimal(0), invoice_amount_atomic=Decimal(0),
            chain_status="off_chain_draft",
        )
        ledger = LedgerProjection(
            namespace_id=namespace.id, project_id=project.id, asset_address="0x" + "44" * 20,
        )
        credit = DonorCreditProjection(
            namespace_id=namespace.id, project_id=project.id, donor_wallet="0x" + "55" * 20,
        )
        session.add_all([procurement, ledger, credit])
        session.flush()
        return {"namespace": namespace.id, "project": project.id, "procurement": procurement.id,
                "ledger": ledger.id, "credit": credit.id, "principal": users[0].id}


@pytest.mark.parametrize("table,column,resource", AMOUNT_COLUMNS)
@pytest.mark.parametrize("value", [str(UINT256_MAX + 1), "NaN", "-1"])
def test_all_a2_amount_columns_reject_invalid_uint256_directly_in_postgresql(
    engine, persisted_a2, table, column, resource, value,
):
    with pytest.raises(IntegrityError) as error:
        with engine.begin() as connection:
            connection.execute(text(
                f"UPDATE {table} SET {column}=CAST(:value AS numeric) WHERE id=:id"
            ), {"value": value, "id": persisted_a2[resource]})
    assert error.value.orig.sqlstate == "23514"
    with engine.connect() as connection:
        assert connection.scalar(text(f"SELECT {column} FROM {table} WHERE id=:id"),
                                 {"id": persisted_a2[resource]}) == Decimal(0)


@pytest.mark.parametrize("table,column,resource", AMOUNT_COLUMNS)
def test_a2_amount_columns_preserve_zero_and_maximum_without_float_round_trip(
    engine, persisted_a2, table, column, resource,
):
    for value in ("0", str(UINT256_MAX)):
        with engine.begin() as connection:
            connection.execute(text(
                f"UPDATE {table} SET {column}=CAST(:value AS numeric) WHERE id=:id"
            ), {"value": value, "id": persisted_a2[resource]})
        with engine.connect() as connection:
            assert connection.scalar(text(f"SELECT {column} FROM {table} WHERE id=:id"),
                                     {"id": persisted_a2[resource]}) == Decimal(value)


def _insert_request(
    factory, persisted, *, nonce="0", deadline="1", status="prepared", submitted=False,
    policy_epoch=0, barrier=None,
):
    with factory() as session, session.begin():
        operation = Operation(
            namespace_id=persisted["namespace"], principal_id=persisted["principal"],
            operation_kind="signing_request.ai_pre", idempotency_key=str(uuid4()),
            payload_hash="11" * 32, status="awaiting_authorization",
        )
        session.add(operation)
        session.flush()
        if barrier is not None:
            barrier.wait(timeout=5)
        request = SigningRequest(
            namespace_id=persisted["namespace"], operation_id=operation.id,
            procurement_id=persisted["procurement"], signer_user_id=persisted["principal"],
            kind="ai_pre", status=status, contract_address="0x" + "66" * 20,
            signer_wallet="0x" + "77" * 20, nonce_text=nonce, deadline_text=deadline,
            policy_epoch=policy_epoch, typed_data={}, context_json={}, digest="0x" + "88" * 32,
            submitted_operation_id=operation.id if submitted else None,
        )
        session.add(request)
        session.flush()
        return request.id


@pytest.mark.parametrize("field,value", [
    ("nonce", str(UINT256_MAX + 1)), ("nonce", "00"), ("nonce", "-1"), ("nonce", "1.0"),
    ("deadline", str(UINT64_MAX + 1)), ("deadline", "00"), ("deadline", "-1"), ("deadline", "1.0"),
    ("policy_epoch", 2**32), ("policy_epoch", -1),
])
def test_signing_integer_constraints_reject_overflow_and_noncanonical_text(
    session_factory, persisted_a2, field, value,
):
    with pytest.raises(IntegrityError) as error:
        _insert_request(session_factory, persisted_a2, **{field: value})
    assert error.value.orig.sqlstate == "23514"


def test_signing_full_unsigned_bounds_are_preserved(session_factory, persisted_a2):
    first = _insert_request(session_factory, persisted_a2, nonce="0", deadline="0", policy_epoch=0)
    second = _insert_request(session_factory, persisted_a2, nonce=str(UINT256_MAX),
                             deadline=str(UINT64_MAX), policy_epoch=2**32 - 1)
    with session_factory() as session:
        assert session.get(SigningRequest, first).nonce_text == "0"
        maximum = session.get(SigningRequest, second)
        assert maximum.nonce_text == str(UINT256_MAX)
        assert maximum.deadline_text == str(UINT64_MAX)
        assert maximum.policy_epoch == 2**32 - 1


@pytest.mark.parametrize("terminal", ["expired", "invalidated_stale"])
def test_nonce_family_can_renew_unsubmitted_terminal_request_without_losing_history(
    session_factory, persisted_a2, terminal,
):
    old = _insert_request(session_factory, persisted_a2, nonce="7", deadline="100")
    with pytest.raises(IntegrityError) as error:
        _insert_request(session_factory, persisted_a2, nonce="7", deadline="200")
    assert error.value.orig.sqlstate == "23505"
    with session_factory() as session, session.begin():
        session.get(SigningRequest, old).status = terminal
    new = _insert_request(session_factory, persisted_a2, nonce="7", deadline="200")
    with session_factory() as session:
        assert session.get(SigningRequest, old).status == terminal
        assert session.get(SigningRequest, old).deadline_text == "100"
        assert session.get(SigningRequest, new).status == "prepared"
        assert session.query(SigningRequest).count() == 2


@pytest.mark.parametrize("status", ["queued", "requires_attention", "confirmed", "failed"])
@pytest.mark.parametrize("terminal", ["expired", "invalidated_stale"])
def test_submitted_or_unknown_signing_request_cannot_release_nonce_family(
    session_factory, persisted_a2, status, terminal,
):
    request_id = _insert_request(session_factory, persisted_a2, nonce="7", status=status, submitted=True)
    with pytest.raises(IntegrityError) as error:
        with session_factory() as session, session.begin():
            session.get(SigningRequest, request_id).status = terminal
    assert error.value.orig.sqlstate == "23514"
    with pytest.raises(IntegrityError) as duplicate:
        _insert_request(session_factory, persisted_a2, nonce="7", deadline="200")
    assert duplicate.value.orig.sqlstate == "23505"
    with session_factory() as session:
        assert session.get(SigningRequest, request_id).status == status


def test_two_concurrent_live_nonce_family_reservations_have_one_winner(
    session_factory, persisted_a2,
):
    barrier = threading.Barrier(2)

    def compete(deadline):
        try:
            return ("created", _insert_request(session_factory, persisted_a2, nonce="9",
                                                 deadline=deadline, barrier=barrier))
        except IntegrityError as error:
            return ("rejected", error.orig.sqlstate)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(compete, ("100", "200")))
    assert sorted(outcome[0] for outcome in outcomes) == ["created", "rejected"]
    assert next(value for outcome, value in outcomes if outcome == "rejected") == "23505"
    with session_factory() as session:
        assert session.query(SigningRequest).count() == 1


def test_populated_a1_upgrades_to_review_head_without_queueing_or_rewriting_data(
    tmp_path, monkeypatch,
):
    base_url = os.environ["POG_TEST_DATABASE_URL"]
    assert_safe_test_target(base_url)
    parsed = urlsplit(base_url.replace("postgresql+psycopg://", "postgresql://", 1))
    database = "pog_api_a2_bounds_" + uuid4().hex[:12]
    admin_url = urlunsplit(parsed._replace(path="/postgres"))
    target_plain = urlunsplit(parsed._replace(path="/" + database))
    target = target_plain.replace("postgresql://", "postgresql+psycopg://", 1)
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(database), sql.Identifier("pog_api"),
        ))
    target_engine = None
    try:
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        monkeypatch.setenv("POG_DATABASE_URL", target)
        command.upgrade(config, "84fcc48891be")
        target_engine = build_engine(target)
        ids = {name: uuid4() for name in ("namespace", "foundation", "recipient", "human", "project",
                                         "procurement", "operation", "audit", "wallet", "session")}
        with target_engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO deployment_instances (id,schema_version,run_id,instance_id,mode,verified,active) "
                "VALUES (:namespace,'a1','legacy-run','legacy-instance','mock',false,true)"
            ), ids)
            for name in ("foundation", "recipient", "human"):
                connection.execute(text(
                    "INSERT INTO users (id,username,password_hash,display_name,active) "
                    "VALUES (:id,:username,'preserved-a1-password-hash','Legacy synthetic user',true)"
                ), {"id": ids[name], "username": "legacy-" + name})
            connection.execute(text(
                "INSERT INTO wallet_authorizations (id,user_id,role_name,wallet_address,active) "
                "VALUES (:wallet,:foundation,'foundation',:address,true)"
            ), {**ids, "address": "0x" + "11" * 20})
            connection.execute(text(
                "INSERT INTO sessions (id,user_id,token_hash,expires_at) "
                "VALUES (:session,:foundation,:token,now()+interval '1 day')"
            ), {**ids, "token": "22" * 32})
            connection.execute(text(
                "INSERT INTO projects (id,namespace_id,business_id,title,public_summary,foundation_user_id,"
                "recipient_user_id,human_approver_user_id,foundation_wallet,recipient_wallet,asset_symbol,"
                "fairness_rule,chain_status) VALUES (:project,:namespace,:business,'Preserved A1 project',"
                "'Synthetic legacy draft',:foundation,:recipient,:human,:fw,:rw,'mHKD','Legacy rule','off_chain_draft')"
            ), {**ids, "business": "0x" + "33" * 32, "fw": "0x" + "11" * 20, "rw": "0x" + "44" * 20})
            connection.execute(text(
                "INSERT INTO procurements (id,namespace_id,project_id,business_id,title,foundation_user_id,"
                "vendor_wallet,budget_cap_atomic,chain_status) VALUES (:procurement,:namespace,:project,"
                ":business,'Preserved A1 procurement',:foundation,:vendor,100,'off_chain_draft')"
            ), {**ids, "business": "0x" + "55" * 32, "vendor": "0x" + "66" * 20})
            connection.execute(text(
                "INSERT INTO operations (id,namespace_id,principal_id,operation_kind,idempotency_key,payload_hash,"
                "status,result_resource_type,result_resource_id) VALUES (:operation,:namespace,:foundation,"
                "'project.create_draft','legacy-key',:payload,'awaiting_authorization','project',:project)"
            ), {**ids, "payload": "77" * 32})
            connection.execute(text(
                "INSERT INTO audit_logs (id,principal_id,operation_id,action,resource_type,resource_id,outcome,metadata_json) "
                "VALUES (:audit,:foundation,:operation,'project.create_draft','project',:project,'accepted','{}'::jsonb)"
            ), ids)
        columns = {
            "users": "id,username,password_hash", "wallet_authorizations": "id,user_id,role_name,wallet_address",
            "sessions": "id,user_id,token_hash,expires_at", "projects": "id,business_id,chain_status",
            "procurements": "id,business_id,budget_cap_atomic,chain_status",
            "operations": "id,idempotency_key,payload_hash,status,result_resource_id",
            "audit_logs": "id,principal_id,operation_id,action,metadata_json",
        }
        with target_engine.connect() as connection:
            before = {table: connection.execute(text(f"SELECT {fields} FROM {table} ORDER BY id")).all()
                      for table, fields in columns.items()}
        command.upgrade(config, "head")
        with target_engine.connect() as connection:
            after = {table: connection.execute(text(f"SELECT {fields} FROM {table} ORDER BY id")).all()
                     for table, fields in columns.items()}
            assert after == before
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "c31004a30006"
            assert connection.scalar(text("SELECT count(*) FROM operations WHERE status='queued'")) == 0
            assert connection.scalar(text("SELECT count(*) FROM chain_transactions")) == 0
            assert connection.scalar(text("SELECT count(*) FROM signing_requests")) == 0
    finally:
        if target_engine is not None:
            target_engine.dispose()
        assert database.startswith("pog_api_a2_bounds_") and database != "pog_api_test"
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                               "WHERE datname=%s AND pid<>pg_backend_pid()", (database,))
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
