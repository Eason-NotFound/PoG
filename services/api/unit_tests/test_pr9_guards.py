import ast
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from pog_api.app import _operation_response, _procurement_response
import pog_api.test_db_safety as safety

SAFE_URL = "postgresql+psycopg://pog_api:synthetic@127.0.0.1:55432/pog_api_test"
ID = UUID("00000000-0000-0000-0000-000000000001")
TX = "0x" + "11" * 32


@pytest.mark.parametrize("ci,confirmation", [
    (None, None), ("true", None), (None, "github-actions"),
    ("false", "github-actions"), ("true", "other"),
])
def test_missing_ownership_requires_exact_ci_confirmation(ci, confirmation):
    with pytest.raises(RuntimeError):
        safety.assert_safe_test_target(
            SAFE_URL, ci=ci, target_confirmed=confirmation,
        )


@pytest.mark.parametrize("override", [
    {"managedBy": "other"}, {"testDatabase": "pog_api_local"}, {"port": 65432},
])
def test_wrong_marker_cannot_fall_back_to_ci(monkeypatch, override):
    marker = {
        "managedBy": "pog-api-a1", "testDatabase": "pog_api_test", "port": 55432,
        **override,
    }

    class FakePath:
        def __init__(self, _value):
            pass

        def resolve(self):
            return self

        def __truediv__(self, _name):
            return self

        def read_text(self, *, encoding):
            return json.dumps(marker)

    monkeypatch.setattr(safety, "Path", FakePath)
    with pytest.raises(RuntimeError, match="marker does not match"):
        safety.assert_safe_test_target(
            SAFE_URL, managed_state="synthetic-state",
            ci="true", target_confirmed="github-actions",
        )


def test_valid_explicit_ci_target_is_accepted():
    result = safety.assert_safe_test_target(
        SAFE_URL, ci="true", target_confirmed="github-actions",
    )
    assert result.database == "pog_api_test"


def test_live_guard_rejects_before_engine_without_importing_live_env():
    # This path is correct after copying PURE to services/api/unit_tests/.
    path = Path(__file__).resolve().parents[1] / "live_tests/test_a2_receipt_confirmed.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_real_anvil_path_stops_at_receipt_confirmed"
    )
    engine = Mock(side_effect=AssertionError("database driver was reached"))
    manifest = Mock(side_effect=AssertionError("manifest read before safety gate"))
    scope = {
        "DATABASE_URL": SAFE_URL,
        "assert_safe_test_target": safety.assert_safe_test_target,
        "os": SimpleNamespace(getenv=lambda _name: None),
        "build_engine": engine,
        "MANIFEST": SimpleNamespace(read_text=manifest),
        "json": json,
    }
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    exec(compile(module, str(path), "exec"), scope)
    with pytest.raises(RuntimeError, match="managed marker or explicit CI"):
        scope[function.name](None)
    engine.assert_not_called()
    manifest.assert_not_called()


def _procurement_fact(status, tx=TX):
    return SimpleNamespace(
        id=ID, project_id=ID, business_id="0x" + "22" * 32,
        title="Synthetic", vendor_wallet="0x" + "33" * 20,
        budget_cap_atomic=Decimal("1000000"),
        chain_status=status, chain_tx_hash=tx,
        chain_block_number=7 if tx else None,
        created_at=datetime(2026, 10, 3, tzinfo=UTC),
    )


def test_confirmed_named_state_without_transaction_is_unverified():
    assert _procurement_response(_procurement_fact("created", None)).chain_state.verified is False


@pytest.mark.parametrize("op_status,step_status,tx_status,receipt,canonical,expected", [
    ("failed", "failed", "failed", 0, True, False),
    ("requires_attention", "requires_attention", "requires_attention", 1, False, False),
    ("submitted", "submitted", "submitted", 1, True, False),
    ("confirmed", "confirmed", "confirmed", 1, True, True),
    ("requires_attention", "confirmed", "confirmed", 1, True, False),
])
def test_operation_verification_requires_confirmed_canonical_receipt(
    op_status, step_status, tx_status, receipt, canonical, expected,
):
    operation = SimpleNamespace(
        id=ID, status=op_status, operation_kind="synthetic",
        result_resource_type="procurement", result_resource_id=ID,
        error_code=None, error_status=None, error_detail=None,
    )
    steps = [{
        "stepIndex": 0, "kind": "synthetic",
        "status": step_status,
        "transaction": {
            "status": tx_status, "receiptStatus": receipt, "canonical": canonical,
        },
    }]
    assert _operation_response(operation, False, steps).chain_verified is expected
