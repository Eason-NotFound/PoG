from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pog_api.test_db_safety import SafeTestDatabaseTarget, validate_test_database_url

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


_TARGET_ENVIRONMENT_OVERRIDES = (
    "PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER",
    "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS",
)


def assert_safe_test_target(
    database_url: str | None, *, environ: Mapping[str, str] | None = None,
) -> SafeTestDatabaseTarget:
    """Fail before creating a driver/engine for any destructive test setup.

    URL validation alone does not prove that a loopback database belongs to the
    test harness. Require its managed marker or the explicit CI declaration as
    well, and prevent libpq environment defaults from overriding the target.
    """
    environment = os.environ if environ is None else environ
    if not database_url or not isinstance(database_url, str):
        raise RuntimeError("POG_TEST_DATABASE_URL is required; tests never fall back to SQLite")
    if any(ord(character) < 32 or ord(character) == 127 for character in database_url):
        raise RuntimeError("Test database URLs must not contain control characters")
    if "?" in database_url or "#" in database_url:
        raise RuntimeError("Test database URLs must not contain query parameters or fragments")
    try:
        target = validate_test_database_url(database_url)
        parsed = urlsplit(database_url)
        explicit_port = parsed.port
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Test database URL is invalid") from exc
    if target.host != "127.0.0.1" or explicit_port is None or not 1024 <= explicit_port <= 65535:
        raise RuntimeError("Tests require an explicit managed 127.0.0.1 PostgreSQL port")
    if any(environment.get(name) for name in _TARGET_ENVIRONMENT_OVERRIDES):
        raise RuntimeError("Test database targets cannot use libpq environment overrides")
    state = environment.get("POG_MANAGED_POSTGRES_STATE")
    if state:
        state_path = Path(state)
        marker_path = state_path / "managed.json"
        try:
            if state_path.is_symlink() or marker_path.is_symlink() or not marker_path.is_file():
                raise RuntimeError("Managed PostgreSQL marker is not a regular owned-harness record")
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise RuntimeError("Managed PostgreSQL marker is missing or invalid") from exc
        if (
            not isinstance(marker, dict)
            or marker.get("managedBy") != "pog-api-a1"
            or marker.get("testDatabase") != target.database
            or marker.get("host") != target.host
            or type(marker.get("port")) is not int
            or marker["port"] != target.port
        ):
            raise RuntimeError("Managed PostgreSQL marker does not match the test URL")
    elif not (
        environment.get("CI") == "true"
        and environment.get("POG_TEST_TARGET_CONFIRMED") == "github-actions"
    ):
        raise RuntimeError("Direct pytest requires a managed marker or explicit CI test target")
    return target


def build_safe_test_engine(
    database_url: str, *, environ: Mapping[str, str] | None = None,
) -> Engine:
    """Shared tests/live-tests entry point: prove the target before its builder."""
    assert_safe_test_target(database_url, environ=environ)
    from pog_api.db import build_engine

    return build_engine(database_url)
