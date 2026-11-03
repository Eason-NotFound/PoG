"""A retry requires explicit operator intent before reading any database config."""
from uuid import uuid4

from pog_api.cli import retry_funding_preflight_command


def test_unconfirmed_retry_does_not_read_settings_or_connect(monkeypatch, capsys):
    def forbidden():
        raise AssertionError("No settings or database access without confirmation")
    monkeypatch.setattr("pog_api.cli.Settings.from_env", forbidden)
    assert retry_funding_preflight_command(False, uuid4()) == 2
    assert "--confirm-retry" in capsys.readouterr().err
