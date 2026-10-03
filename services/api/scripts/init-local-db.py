from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
from urllib.parse import quote

import psycopg
from psycopg import sql


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    state = Path(args.state).resolve()
    credentials_path = state / "credentials"
    connection_path = state / "connection.env"
    if credentials_path.exists():
        password = credentials_path.read_text(encoding="utf-8").strip()
    else:
        password = secrets.token_urlsafe(36)
        credentials_path.write_text(password + "\n", encoding="utf-8")
        credentials_path.chmod(0o600)

    admin_url = (
        f"postgresql://pog_admin@/postgres?host={quote(args.socket, safe='')}"
        f"&port={args.port}"
    )
    with psycopg.connect(admin_url, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", ("pog_api",)
        ).fetchone()
        if exists is None:
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN CREATEDB PASSWORD {}").format(
                    sql.Identifier("pog_api"), sql.Literal(password)
                )
            )
        for database in ("pog_api_local", "pog_api_test"):
            present = connection.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
            ).fetchone()
            if present is None:
                connection.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(database), sql.Identifier("pog_api")
                    )
                )
    encoded = quote(password, safe="")
    connection_path.write_text(
        (
            "export POG_DATABASE_URL="
            f"'postgresql+psycopg://pog_api:{encoded}@127.0.0.1:{args.port}/pog_api_local'\n"
            "export POG_TEST_DATABASE_URL="
            f"'postgresql+psycopg://pog_api:{encoded}@127.0.0.1:{args.port}/pog_api_test'\n"
        ),
        encoding="utf-8",
    )
    connection_path.chmod(0o600)
    marker_path = state / "managed.json"
    marker_path.write_text(
        json.dumps(
            {
                "managedBy": "pog-api-a1",
                "host": "127.0.0.1",
                "port": args.port,
                "testDatabase": "pog_api_test",
                "socket": str(Path(args.socket).resolve()),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    marker_path.chmod(0o600)
    print("Dedicated local role and databases are ready; credentials remain in ignored storage.")


if __name__ == "__main__":
    main()
