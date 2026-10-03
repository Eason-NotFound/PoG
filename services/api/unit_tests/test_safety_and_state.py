from datetime import UTC, datetime
from decimal import Decimal
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from pog_api.app import _procurement_response
from pog_api.test_db_safety import assert_safe_test_target

TEST_URL = "postgresql+psycopg://pog_api@127.0.0.1:55432/pog_api_test"


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://pog_api@127.0.0.1:55432/pog_api_local",
    "postgresql+psycopg://pog_api@example.invalid:55432/pog_api_test",
    TEST_URL + "?host=example.invalid", TEST_URL + "#override",
    "postgresql+psycopg://postgres@127.0.0.1:55432/pog_api_test",
])
def test_explicit_ci_does_not_override_unsafe_database_url(url):
    with pytest.raises(RuntimeError):
        assert_safe_test_target(url, ci="true", target_confirmed="github-actions")


def test_destructive_test_requires_explicit_ownership(tmp_path):
    with pytest.raises(RuntimeError, match="managed marker"):
        assert_safe_test_target(TEST_URL)
    marker = {"managedBy": "pog-api-a1", "testDatabase": "pog_api_test", "port": 55432}
    (tmp_path / "managed.json").write_text(json.dumps(marker), encoding="utf-8")
    assert assert_safe_test_target(TEST_URL, managed_state=str(tmp_path)).port == 55432
    marker["port"] = 55433
    (tmp_path / "managed.json").write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not match"):
        assert_safe_test_target(TEST_URL, managed_state=str(tmp_path), ci="true", target_confirmed="github-actions")


@pytest.mark.parametrize("status", [
    "off_chain_draft", "create_queued", "po_queued", "ai_pre_queued", "reserve_vote_queued",
    "reserve_queued", "invoice_queued", "receipt_queued",
])
def test_pending_business_state_does_not_reuse_old_transaction_confirmation(status):
    resource = SimpleNamespace(
        id=uuid4(), project_id=uuid4(), business_id="0x" + "11" * 32, title="Synthetic purchase",
        vendor_wallet="0x" + "22" * 20, budget_cap_atomic=Decimal(100), chain_status=status,
        chain_tx_hash="0x" + "33" * 32, chain_block_number=10, created_at=datetime.now(UTC),
    )
    assert _procurement_response(resource).chain_state.verified is False
