from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from conftest import auth
from pog_api.app import create_app
from pog_api.config import Settings
from pog_api.models import Operation, Procurement, Project


def test_foundation_creates_off_chain_draft_and_replays_same_key(
    client, actors, login, project_payload, session_factory
):
    token = login(actors["foundation"])
    headers = {**auth(token), "Idempotency-Key": "same-project"}
    first = client.post("/v2/projects", json=project_payload, headers=headers)
    second = client.post(
        "/v2/projects",
        json={k: project_payload[k] for k in reversed(project_payload)},
        headers=headers,
    )
    assert first.status_code == second.status_code == 202
    first_body, second_body = first.json(), second.json()
    assert first_body["operation"]["operationId"] == second_body["operation"]["operationId"]
    assert first_body["project"]["id"] == second_body["project"]["id"]
    assert first_body["operation"]["status"] == "awaiting_authorization"
    assert first_body["operation"]["chainVerified"] is False
    assert first_body["project"]["chainState"] == {
        "status": "off_chain_draft",
        "verified": False,
    }
    assert second_body["operation"]["replayed"] is True
    with session_factory() as session:
        assert session.scalar(select(func.count(Project.id))) == 1
        assert session.scalar(select(func.count(Operation.id))) == 1


def test_same_key_different_payload_conflicts(client, actors, login, project_payload):
    token = login(actors["foundation"])
    headers = {**auth(token), "Idempotency-Key": "conflict"}
    assert client.post("/v2/projects", json=project_payload, headers=headers).status_code == 202
    changed = {**project_payload, "title": "Different title"}
    response = client.post("/v2/projects", json=changed, headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_payload_conflict"


def test_idempotency_scope_differs_by_principal(client, actors, login, project_payload):
    first_token = login(actors["foundation"])
    second_token = login(actors["other_foundation"])
    headers_one = {**auth(first_token), "Idempotency-Key": "shared-text"}
    headers_two = {**auth(second_token), "Idempotency-Key": "shared-text"}
    assert client.post("/v2/projects", json=project_payload, headers=headers_one).status_code == 202
    assert client.post("/v2/projects", json=project_payload, headers=headers_two).status_code == 202


def test_concurrent_first_namespace_and_duplicate_request_create_one_project(
    settings, actors, login, client, project_payload, session_factory
):
    token = login(actors["foundation"])
    headers = {**auth(token), "Idempotency-Key": "concurrent-project"}

    def submit():
        with TestClient(create_app(settings)) as thread_client:
            return thread_client.post("/v2/projects", json=project_payload, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: submit(), range(2)))
    assert [response.status_code for response in responses] == [202, 202]
    ids = {response.json()["project"]["id"] for response in responses}
    assert len(ids) == 1
    with session_factory() as session:
        assert session.scalar(select(func.count(Project.id))) == 1


def test_restart_preserves_idempotency(settings, client, actors, login, project_payload):
    token = login(actors["foundation"])
    headers = {**auth(token), "Idempotency-Key": "restart-project"}
    first = client.post("/v2/projects", json=project_payload, headers=headers).json()
    with TestClient(create_app(settings)) as restarted:
        second = restarted.post("/v2/projects", json=project_payload, headers=headers)
    assert second.status_code == 202
    assert second.json()["project"]["id"] == first["project"]["id"]
    assert second.json()["operation"]["replayed"] is True


def test_namespace_change_hides_old_resources_and_operations(
    settings, client, actors, login, project_payload, session_factory
):
    token = login(actors["foundation"])
    created = client.post(
        "/v2/projects",
        json=project_payload,
        headers={**auth(token), "Idempotency-Key": "old-namespace"},
    ).json()
    new_settings = Settings(
        database_url=settings.database_url,
        storage_root=settings.storage_root,
        run_id="new-run",
        instance_id="new-instance",
    )
    with TestClient(create_app(new_settings)) as new_client:
        assert (
            new_client.get(f"/v2/projects/{created['project']['id']}", headers=auth(token)).status_code
            == 404
        )
        assert (
            new_client.get(
                f"/v2/operations/{created['operation']['operationId']}", headers=auth(token)
            ).status_code
            == 404
        )


def test_donor_sees_public_rule_but_not_wallets(client, actors, login, project_payload):
    foundation_token = login(actors["foundation"])
    project = client.post(
        "/v2/projects",
        json=project_payload,
        headers={**auth(foundation_token), "Idempotency-Key": "public-project"},
    ).json()["project"]
    donor_token = login(actors["donor"])
    response = client.get(f"/v2/projects/{project['id']}", headers=auth(donor_token))
    assert response.status_code == 200
    body = response.json()
    assert body["foundationWallet"] is None
    assert body["recipientWallet"] is None
    assert "proportionally" in body["fairnessRule"]
    assert "unresolved debt" in body["closingRule"]


def test_cross_project_and_role_permissions(client, actors, login, project_payload):
    donor_token = login(actors["donor"])
    denied = client.post(
        "/v2/projects",
        json=project_payload,
        headers={**auth(donor_token), "Idempotency-Key": "donor-project"},
    )
    assert denied.status_code == 403
    foundation_token = login(actors["foundation"])
    project = client.post(
        "/v2/projects",
        json=project_payload,
        headers={**auth(foundation_token), "Idempotency-Key": "owned"},
    ).json()["project"]
    other_token = login(actors["other_foundation"])
    response = client.post(
        "/v2/procurements",
        json={
            "projectId": project["id"],
            "title": "Unauthorized procurement",
            "vendorWallet": "0x00000000000000000000000000000000000000aa",
            "budgetCapAtomic": "1",
        },
        headers={**auth(other_token), "Idempotency-Key": "cross-project"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("bad_amount", [0, True, -1, "1.0", "1e2", "+1", "01"])
def test_procurement_rejects_noncanonical_amount(
    client, created_project, bad_amount
):
    token, project = created_project
    response = client.post(
        "/v2/procurements",
        json={
            "projectId": project["project"]["id"],
            "title": "Bad amount",
            "vendorWallet": "0x00000000000000000000000000000000000000aa",
            "budgetCapAtomic": bad_amount,
        },
        headers={**auth(token), "Idempotency-Key": f"bad-{bad_amount!s}"},
    )
    assert response.status_code == 422


def test_postgres_uint256_constraint_rejects_negative_and_overflow(
    created_project, session_factory
):
    _token, project = created_project
    project_id = project["project"]["id"]
    with session_factory() as session, session.begin():
        namespace_id = session.scalar(select(Project.namespace_id))
        foundation_id = session.scalar(select(Project.foundation_user_id))
    for amount in (Decimal("-1"), Decimal(2**256)):
        with session_factory() as session:
            with pytest.raises(IntegrityError):
                with session.begin():
                    session.add(
                        Procurement(
                            namespace_id=namespace_id,
                            project_id=project_id,
                            business_id="0x" + "1" * 64,
                            title="constraint test",
                            foundation_user_id=foundation_id,
                            vendor_wallet="0x" + "a" * 40,
                            budget_cap_atomic=amount,
                            chain_status="off_chain_draft",
                        )
                    )
                    session.flush()
            session.rollback()


def test_client_cannot_inject_confirmed_fields(client, actors, login, project_payload):
    token = login(actors["foundation"])
    response = client.post(
        "/v2/projects",
        json={**project_payload, "chainStatus": "confirmed", "txHash": "0x" + "1" * 64},
        headers={**auth(token), "Idempotency-Key": "inject-confirmed"},
    )
    assert response.status_code == 422


def test_business_failure_operation_and_original_status_survive_retry_and_query(
    client, actors, login
):
    token = login(actors["foundation"])
    payload = {
        "projectId": "00000000-0000-0000-0000-000000000001",
        "title": "Missing parent",
        "vendorWallet": "0x00000000000000000000000000000000000000aa",
        "budgetCapAtomic": "1",
    }
    headers = {**auth(token), "Idempotency-Key": "failed-procurement"}
    first = client.post("/v2/procurements", json=payload, headers=headers)
    second = client.post("/v2/procurements", json=payload, headers=headers)
    assert first.status_code == second.status_code == 404
    first_error = first.json()["error"]
    second_error = second.json()["error"]
    assert first_error["code"] == second_error["code"] == "project_not_found"
    assert first_error["operationId"] == second_error["operationId"]
    operation = client.get(
        f"/v2/operations/{first_error['operationId']}", headers=auth(token)
    )
    assert operation.status_code == 200
    body = operation.json()
    assert body["status"] == "failed"
    assert body["errorStatus"] == 404
    assert body["errorCode"] == "project_not_found"
    assert body["errorMessage"] == "Project not found"
