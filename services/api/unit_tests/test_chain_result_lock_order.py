"""Late chain results keep namespace-first locking and never revive old work."""
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest

from pog_api import worker as module
from pog_api.models import ChainTransaction, DeploymentInstance, Operation, OperationStep
from pog_api.worker import ChainWorker

CALLBACKS = ("_not_broadcast", "_unknown", "_submitted")
HASH = "0x" + "11" * 32


def fixture_worker(monkeypatch, state="pending", known_hash=None):
    namespace = SimpleNamespace(id=uuid4(), active=state != "inactive")
    terminal = state if state in {"invalidated_instance", "confirmed"} else None
    row = SimpleNamespace(id=uuid4(), namespace_id=namespace.id, operation_id=uuid4(), step_id=uuid4(),
        status=terminal or "sending", tx_hash=known_hash, submitted_at=None, envelope_hash="fixed-public-hash")
    operation = SimpleNamespace(id=row.operation_id, status=terminal or "queued", error_code="original_reason",
        error_status=409, error_detail="Original private-scope decision", principal_id=None,
        result_resource_type=None, result_resource_id=None)
    step = SimpleNamespace(id=row.step_id, status=terminal or "prepared")
    calls, signing, audits = [], [], []
    class Session:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def begin(self): return nullcontext()
        def get(self, model, identifier, **kwargs):
            calls.append((model, kwargs))
            return {ChainTransaction: row, DeploymentInstance: namespace, Operation: operation, OperationStep: step}[model]
    instance = object.__new__(ChainWorker)
    instance.factory = Session
    monkeypatch.setattr(instance, "_signing_status", lambda *args: signing.append(args[-1]))
    def failed(session, current_step, current_op, **kwargs):
        current_step.status = current_op.status = "failed"
        signing.append("failed")
    monkeypatch.setattr(instance, "_failed_step", failed)
    monkeypatch.setattr(module, "audit", lambda *args, **kwargs: audits.append(kwargs))
    return instance, row, operation, step, calls, signing, audits


def invoke(instance, callback, row):
    getattr(instance, callback)(row.id, HASH if callback == "_submitted" else TimeoutError("Fixed safe fixture error"))


@pytest.mark.parametrize("callback", CALLBACKS)
@pytest.mark.parametrize("state", ("inactive", "invalidated_instance", "confirmed"))
def test_late_result_preserves_terminal_work_but_saves_known_hash(monkeypatch, callback, state):
    instance, row, operation, step, calls, signing, audits = fixture_worker(monkeypatch, state)
    before = (row.status, operation.status, step.status, operation.error_code)
    invoke(instance, callback, row)
    assert calls[:3] == [(ChainTransaction, {}), (DeploymentInstance, {"with_for_update": True}),
                        (ChainTransaction, {"with_for_update": True, "populate_existing": True})]
    assert (row.status, operation.status, step.status, operation.error_code) == before
    assert not signing and not audits
    assert row.tx_hash == (HASH if callback == "_submitted" else None)


@pytest.mark.parametrize("callback,expected", (("_not_broadcast", "failed"), ("_unknown", "requires_attention"), ("_submitted", "submitted")))
def test_pending_result_still_progresses_in_same_namespace(monkeypatch, callback, expected):
    instance, row, operation, step, calls, signing, audits = fixture_worker(monkeypatch)
    invoke(instance, callback, row)
    assert calls[1] == (DeploymentInstance, {"with_for_update": True})
    assert operation.status == step.status == expected
    assert row.status == ("not_broadcast" if callback == "_not_broadcast" else expected)


@pytest.mark.parametrize("state", ("pending", "invalidated_instance", "confirmed"))
def test_conflicting_late_hash_never_replaces_original(monkeypatch, state):
    original = "0x" + "22" * 32
    instance, row, operation, step, calls, signing, audits = fixture_worker(monkeypatch, state, original)
    before = (row.status, operation.status, step.status)
    invoke(instance, "_submitted", row)
    assert row.tx_hash == original and (row.status, operation.status, step.status) == before
    assert not signing and audits[0]["action"] == "chain_worker.late_result_conflict"
