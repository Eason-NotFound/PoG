from __future__ import annotations

import os
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from pog_api.test_db_safety import validate_test_database_url


def main() -> None:
    if os.getenv("POG_ALLOW_TEST_DB_RESET") != "1":
        raise RuntimeError("POG_ALLOW_TEST_DB_RESET=1 is required")
    state = Path(os.environ["POG_MANAGED_POSTGRES_STATE"]).resolve()
    marker_path = state / "managed.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if (
        marker.get("managedBy") != "pog-api-a1"
        or marker.get("testDatabase") != "pog_api_test"
    ):
        raise RuntimeError("Managed PostgreSQL marker is invalid")
    url = os.environ["POG_TEST_DATABASE_URL"]
    target = validate_test_database_url(url)
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
