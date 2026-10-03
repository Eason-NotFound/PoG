from __future__ import annotations

from collections.abc import Callable, Generator
from pathlib import Path
import json
import os
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from pog_api.app import create_app
from pog_api.config import Settings
from pog_api.db import build_engine, build_session_factory
from pog_api.models import ROLE_NAMES, Role, User, WalletAuthorization
from pog_api.security import hash_password
from pog_api.test_db_safety import validate_test_database_url


TEST_DATABASE_URL = os.environ.get("POG_TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    raise RuntimeError("POG_TEST_DATABASE_URL is required; tests never fall back to SQLite")


def assert_safe_test_target() -> None:
    target = validate_test_database_url(TEST_DATABASE_URL)
    state = os.environ.get("POG_MANAGED_POSTGRES_STATE")
    if state:
        marker = json.loads((Path(state).resolve() / "managed.json").read_text(encoding="utf-8"))
        if (
            marker.get("managedBy") != "pog-api-a1"
            or marker.get("testDatabase") != "pog_api_test"
            or marker.get("port") != target.port
        ):
            raise RuntimeError("Managed PostgreSQL marker does not match the test URL")
        return
    if not (
        os.getenv("CI") == "true"
        and os.getenv("POG_TEST_TARGET_CONFIRMED") == "github-actions"
    ):
        raise RuntimeError("Direct pytest requires a managed marker or explicit CI target")


assert_safe_test_target()


@pytest.fixture(scope="session")
def engine():
    value = build_engine(TEST_DATABASE_URL)
    with value.connect() as connection:
        assert connection.dialect.name == "postgresql"
        assert connection.scalar(text("SHOW server_version_num")).startswith("17")
    yield value
    value.dispose()


@pytest.fixture(autouse=True)
def clean_database(engine):
    tables = [
        "receipt_proofs",
        "document_versions",
        "risk_reports",
        "payment_operation_links",
        "documents",
        "approval_records",
        "procurements",
        "operation_steps",
        "chain_transactions",
        "audit_logs",
        "wallet_authorizations",
        "sessions",
        "projects",
        "operations",
        "indexer_cursors",
        "chain_events",
        "users",
        "deployment_instances",
    ]
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE " + ",".join(tables) + " CASCADE"))
        for role in ROLE_NAMES:
            connection.execute(
                text("INSERT INTO roles(name) VALUES (:name) ON CONFLICT DO NOTHING"),
                {"name": role},
            )
    yield


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        storage_root=tmp_path / "private-storage",
        session_ttl_seconds=3600,
        run_id="test-run",
        instance_id="test-instance",
    )


@pytest.fixture
def client(settings: Settings) -> Generator[TestClient, None, None]:
    with TestClient(create_app(settings)) as value:
        yield value


@pytest.fixture
def session_factory(engine):
    return build_session_factory(engine)


@pytest.fixture
def create_user(session_factory) -> Callable[..., dict[str, str]]:
    counter = {"value": 0}

    def _create(
        role: str,
        *,
        username: str | None = None,
        password: str = "correct horse battery staple",
        wallet: str | None = None,
    ) -> dict[str, str]:
        counter["value"] += 1
        value = counter["value"]
        username_value = username or f"{role}-{value}"
        wallet_value = wallet or ("0x" + f"{value:040x}")
        with session_factory() as session, session.begin():
            user = User(
                username=username_value,
                password_hash=hash_password(password),
                display_name=f"{role} {value}",
                active=True,
            )
            session.add(user)
            session.flush()
            session.add(
                WalletAuthorization(
                    user_id=user.id,
                    role_name=role,
                    wallet_address=wallet_value,
                    active=True,
                )
            )
        return {
            "id": str(user.id),
            "username": username_value,
            "password": password,
            "wallet": wallet_value,
            "role": role,
        }

    return _create


@pytest.fixture
def login(client: TestClient):
    def _login(user: dict[str, str]) -> str:
        response = client.post(
            "/v2/sessions",
            json={"username": user["username"], "password": user["password"]},
        )
        assert response.status_code == 200, response.text
        return response.json()["token"]

    return _login


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def actors(create_user):
    return {
        "foundation": create_user("foundation"),
        "recipient": create_user("recipient"),
        "donor": create_user("donor"),
        "human": create_user("human_approver"),
        "other_recipient": create_user("recipient"),
        "other_foundation": create_user("foundation"),
    }


@pytest.fixture
def project_payload(actors):
    return {
        "title": "Community medical supplies",
        "publicSummary": "Demo-only off-chain draft for a shared procurement project.",
        "recipientUserId": actors["recipient"]["id"],
        "humanApproverUserId": actors["human"]["id"],
    }


@pytest.fixture
def created_project(client, login, actors, project_payload):
    token = login(actors["foundation"])
    response = client.post(
        "/v2/projects",
        json=project_payload,
        headers={**auth(token), "Idempotency-Key": "project-fixture"},
    )
    assert response.status_code == 202, response.text
    return token, response.json()


@pytest.fixture
def created_procurement(client, created_project):
    token, project = created_project
    response = client.post(
        "/v2/procurements",
        json={
            "projectId": project["project"]["id"],
            "title": "Medical kit purchase",
            "vendorWallet": "0x00000000000000000000000000000000000000aa",
            "budgetCapAtomic": "80000000",
        },
        headers={**auth(token), "Idempotency-Key": "procurement-fixture"},
    )
    assert response.status_code == 202, response.text
    return token, project, response.json()
