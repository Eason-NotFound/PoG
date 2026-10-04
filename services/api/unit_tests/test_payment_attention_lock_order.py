"""Connection-free regression for the rollback attention transaction's lock lane."""
from contextlib import nullcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest

from pog_api.errors import APIError
from pog_api.models import DeploymentInstance, Operation, OperationStep
from pog_api.payment_worker import PaymentWorker


@pytest.mark.parametrize("initial_status", ["queued", "invalidated_instance"])
def test_attention_locks_namespace_before_operation_step_and_preserves_invalidation(initial_status):
    operation = SimpleNamespace(id=uuid4(), namespace_id=uuid4(), status=initial_status,
        principal_id=None, result_resource_type=None, result_resource_id=None)
    step = SimpleNamespace(id=uuid4(), status="queued")
    calls, audits = [], []
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def begin(self):
            return nullcontext()
        def get(self, model, identifier, **kwargs):
            calls.append((model, kwargs.get("with_for_update", False)))
            return {Operation: operation, OperationStep: step, DeploymentInstance: SimpleNamespace(id=operation.namespace_id)}[model]
        def add(self, item):
            audits.append(item)
    worker = object.__new__(PaymentWorker)
    worker.factory = Session
    worker._attention(operation.id, step.id, None, None, APIError(409, "payment_step_invalid", "Fixed safe message"))
    assert calls == [(Operation, False), (DeploymentInstance, True), (Operation, True), (OperationStep, True)]
    if initial_status == "invalidated_instance":
        assert operation.status == "invalidated_instance" and step.status == "queued" and not audits
    else:
        assert operation.status == step.status == "requires_attention" and len(audits) == 1
