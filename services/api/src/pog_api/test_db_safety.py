from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class SafeTestDatabaseTarget:
    database: str
    host: str
    port: int


def validate_test_database_url(value: str) -> SafeTestDatabaseTarget:
    """Validate the destructive-test target before any driver sees the URL."""

    parsed = urlsplit(value)
    if parsed.scheme != "postgresql+psycopg":
        raise RuntimeError("Tests require the postgresql+psycopg URL scheme")
    if parsed.query or parsed.fragment:
        raise RuntimeError("Test database URLs must not contain query parameters or fragments")
    if parsed.username != "pog_api":
        raise RuntimeError("Tests require the dedicated pog_api database role")
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Tests only permit a loopback PostgreSQL host")
    if parsed.path != "/pog_api_test":
        raise RuntimeError("Tests only permit the dedicated pog_api_test database")
    try:
        port = parsed.port or 5432
    except ValueError as exc:
        raise RuntimeError("Test database URL has an invalid port") from exc
    return SafeTestDatabaseTarget(database="pog_api_test", host=parsed.hostname, port=port)


def assert_safe_test_target(
    value: str, *, managed_state: str | None = None,
    ci: str | None = None, target_confirmed: str | None = None,
) -> SafeTestDatabaseTarget:
    """Require an owned local test target or an explicit isolated CI target before reset."""
    target = validate_test_database_url(value)
    if managed_state:
        marker = json.loads((Path(managed_state).resolve() / "managed.json").read_text(encoding="utf-8"))
        if (
            marker.get("managedBy") != "pog-api-a1"
            or marker.get("testDatabase") != target.database
            or marker.get("port") != target.port
        ):
            raise RuntimeError("Managed PostgreSQL marker does not match the test URL")
        return target
    if ci != "true" or target_confirmed != "github-actions":
        raise RuntimeError("Direct pytest requires a managed marker or explicit CI target")
    return target
