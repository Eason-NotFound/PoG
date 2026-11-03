"""No-engine readiness compatibility for the additive diagnostic migration."""
import pytest

from pog_api.app import _database_schema_ready


@pytest.mark.parametrize("enabled,revision,diagnostic_tables,expected", [
    (True, "c31004a30006", 1, True),
    (True, "c31003a30005", 0, False),
    (True, "c31003a30005", 1, False),
    (True, "c31004a30006", 0, False),
    (False, "c31003a30005", 0, True),
    (False, "c31004a30006", 0, True),
    (False, "unknown_future_revision", 1, False),
])
def test_readiness_requires_diagnostic_schema_only_when_enabled(enabled, revision, diagnostic_tables, expected):
    assert _database_schema_ready(revision, 12, 6, diagnostic_tables, ai_diagnostic_enabled=enabled) is expected


@pytest.mark.parametrize("required,payment", [(11, 6), (12, 5)])
def test_additive_revision_never_bypasses_existing_table_gates(required, payment):
    assert not _database_schema_ready("c31004a30006", required, payment, 1, ai_diagnostic_enabled=True)
