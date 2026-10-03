from __future__ import annotations

from dataclasses import dataclass
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
