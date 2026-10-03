from __future__ import annotations

import os
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from pog_api.test_database import assert_safe_test_target


def main() -> None:
    if os.getenv("POG_ALLOW_TEST_DB_RESET") != "1":
        raise RuntimeError("POG_ALLOW_TEST_DB_RESET=1 is required")
    url = os.getenv("POG_TEST_DATABASE_URL")
    # Prove the exact target before resolving an ownership marker or allowing
    # libpq to see a connection string, including the administrative connection.
    target = assert_safe_test_target(url)
    managed_state = os.getenv("POG_MANAGED_POSTGRES_STATE")
    if not managed_state:
        raise RuntimeError("POG_MANAGED_POSTGRES_STATE is required for database reset")
    state = Path(managed_state).resolve()
    marker_path = state / "managed.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if (
        marker.get("managedBy") != "pog-api-a1"
        or marker.get("testDatabase") != "pog_api_test"
    ):
        raise RuntimeError("Managed PostgreSQL marker is invalid")
    plain = url.replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlsplit(plain)
    database = target.database
    if target.port != marker.get("port"):
        raise RuntimeError("Refusing to reset a database outside the managed loopback cluster")
    admin = urlunsplit(parsed._replace(path="/postgres"))
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (database,),
        )
        connection.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))
        connection.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(database), sql.Identifier("pog_api")
            )
        )


if __name__ == "__main__":
    main()
