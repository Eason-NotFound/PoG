from __future__ import annotations

import pytest

from pog_api.test_db_safety import validate_test_database_url


def test_accepts_only_the_static_loopback_test_target():
    target = validate_test_database_url(
        "postgresql+psycopg://pog_api:secret@127.0.0.1:55432/pog_api_test"
    )
    assert target.database == "pog_api_test"
    assert target.host == "127.0.0.1"
    assert target.port == 55432


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://pog_api@127.0.0.1:55432/pog_api_test?host=example.invalid",
        "postgresql+psycopg://pog_api@127.0.0.1:55432/pog_api_test?dbname=pog_api_local",
        "postgresql+psycopg://pog_api@127.0.0.1:55432/pog_api_test?port=65432",
        "postgresql+psycopg://pog_api@127.0.0.1:55432/pog_api_test#override",
        "postgresql+psycopg://postgres@127.0.0.1:55432/pog_api_test",
        "postgresql+psycopg://pog_api@example.invalid:55432/pog_api_test",
        "postgresql+psycopg://pog_api@127.0.0.1:55432/pog_api_local",
        "postgresql://pog_api@127.0.0.1:55432/pog_api_test",
    ],
)
def test_rejects_urls_that_can_redirect_destructive_test_operations(url: str):
    with pytest.raises(RuntimeError):
        validate_test_database_url(url)
