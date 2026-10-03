from datetime import timedelta

from sqlalchemy import func, select

from conftest import auth
from pog_api.models import AuditLog, SessionRecord
from pog_api.security import utcnow


def test_health_ready_and_config_are_truthful(client):
    assert client.get("/health").json()["status"] == "healthy"
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["ready"] is True
    assert ready.json()["deployment"]["verified"] is False
    config = client.get("/v2/deployment-config")
    assert config.status_code == 200
    body = config.json()
    assert body["verified"] is False
    assert body["chainId"] is None
    assert body["contracts"] == {}
    assert body["adapters"]["chain"]["mode"] == "unavailable"
    assert body["adapters"]["wallet"]["mode"] == "mock_limited"


def test_login_me_and_idempotent_logout(client, actors, login):
    token = login(actors["foundation"])
    me = client.get("/v2/me", headers=auth(token))
    assert me.status_code == 200
    assert me.json()["role"] == "foundation"
    assert me.json()["walletAddress"] == actors["foundation"]["wallet"]
    first = client.delete("/v2/sessions/current", headers=auth(token))
    second = client.delete("/v2/sessions/current", headers=auth(token))
    assert first.json() == {"loggedOut": True, "alreadyLoggedOut": False}
    assert second.json() == {"loggedOut": True, "alreadyLoggedOut": True}
    assert client.get("/v2/me", headers=auth(token)).status_code == 401


def test_failed_login_persists_redacted_audit(client, actors, session_factory):
    response = client.post(
        "/v2/sessions",
        json={"username": actors["foundation"]["username"], "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"
    with session_factory() as session:
        record = session.scalar(select(AuditLog).where(AuditLog.action == "session.login"))
        assert record is not None
        assert record.outcome == "denied"
        assert "usernameSha256" in record.metadata_json
        assert actors["foundation"]["username"] not in str(record.metadata_json)


def test_expired_session_is_rejected(client, actors, login, session_factory):
    token = login(actors["donor"])
    with session_factory() as session, session.begin():
        record = session.scalar(select(SessionRecord))
        record.created_at = utcnow() - timedelta(seconds=2)
        record.expires_at = utcnow() - timedelta(seconds=1)
    response = client.get("/v2/me", headers=auth(token))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_inactive"


def test_forged_role_and_wallet_fields_are_rejected(client, actors, login, project_payload):
    token = login(actors["foundation"])
    forged = {
        **project_payload,
        "role": "foundation",
        "caller": actors["human"]["wallet"],
        "wallet": actors["human"]["wallet"],
    }
    response = client.post(
        "/v2/projects",
        json=forged,
        headers={**auth(token), "Idempotency-Key": "forged"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_isolated_unicode_surrogate_is_stable_validation_error(client):
    response = client.post(
        "/v2/sessions",
        content=b'{"username":"\\ud800","password":"not-a-real-password"}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_framework_404_uses_stable_error(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "route_not_found"
