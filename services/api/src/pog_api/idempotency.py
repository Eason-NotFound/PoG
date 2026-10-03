from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import APIError
from .hashing import payload_sha256
from .models import AuditLog, DeploymentInstance, Operation


_IDEMPOTENCY_RE = re.compile(r"^[ -~]{1,128}$")


def validate_idempotency_key(key: str | None) -> str:
    if key is None:
        raise APIError(400, "idempotency_key_required", "Idempotency-Key header is required")
    if not _IDEMPOTENCY_RE.fullmatch(key):
        raise APIError(
            400,
            "invalid_idempotency_key",
            "Idempotency-Key must contain 1..128 printable ASCII characters",
        )
    return key


def ensure_namespace(session: Session, run_id: str, instance_id: str) -> DeploymentInstance:
    namespace = session.scalar(
        select(DeploymentInstance).where(
            DeploymentInstance.run_id == run_id,
            DeploymentInstance.instance_id == instance_id,
        )
    )
    if namespace is None:
        session.execute(
            insert(DeploymentInstance)
            .values(
                schema_version="a1-mock-1",
                run_id=run_id,
                instance_id=instance_id,
                chain_id=None,
                genesis_hash=None,
                mode="mock",
                verified=False,
                active=True,
            )
            .on_conflict_do_nothing(constraint="uq_deployment_namespace")
        )
        namespace = session.scalar(
            select(DeploymentInstance).where(
                DeploymentInstance.run_id == run_id,
                DeploymentInstance.instance_id == instance_id,
            )
        )
        if namespace is None:
            raise RuntimeError("Failed to create or load deployment namespace")
    if not namespace.active:
        raise APIError(409, "deployment_instance_inactive", "Deployment namespace is inactive")
    return namespace


def ensure_verified_namespace(session: Session, gateway) -> DeploymentInstance:
    """Activate exactly one verified local-chain namespace and invalidate old pending work."""
    gateway.verify()
    current = session.scalar(
        select(DeploymentInstance).where(
            DeploymentInstance.run_id == gateway.run_id,
            DeploymentInstance.instance_id == gateway.instance_id,
        )
    )
    if current is None:
        session.execute(
            insert(DeploymentInstance).values(
                schema_version="a2-chain-1", run_id=gateway.run_id,
                instance_id=gateway.instance_id, chain_id=31337,
                genesis_hash=gateway.manifest["chain"]["genesisHash"],
                mode="verified", verified=True, active=True, rpc_url=gateway.rpc_url,
                manifest_sha256=gateway.manifest_sha256, manifest_json=gateway.manifest,
            ).on_conflict_do_nothing(constraint="uq_deployment_namespace")
        )
        current = session.scalar(
            select(DeploymentInstance).where(
                DeploymentInstance.run_id == gateway.run_id,
                DeploymentInstance.instance_id == gateway.instance_id,
            )
        )
    if current is None:
        raise RuntimeError("Failed to create verified A2 namespace")
    if current.manifest_sha256 != gateway.manifest_sha256:
        raise APIError(503, "deployment_manifest_changed", "Active deployment manifest changed")
    stale = session.scalars(
        select(DeploymentInstance).where(
            DeploymentInstance.active.is_(True), DeploymentInstance.id != current.id,
            DeploymentInstance.mode == "verified",
        )
    ).all()
    for item in stale:
        item.active = False
        from .security import utcnow
        item.invalidated_at = utcnow()
        session.execute(
            Operation.__table__.update().where(
                Operation.namespace_id == item.id,
                Operation.status.in_(("awaiting_authorization", "queued", "submitted")),
            ).values(status="invalidated_instance", error_code="deployment_instance_changed",
                     error_status=409, error_detail="Local-chain instance changed")
        )
        from .models import SigningRequest, ChainTransaction
        session.execute(
            SigningRequest.__table__.update().where(
                SigningRequest.namespace_id == item.id,
                SigningRequest.status.in_(("prepared", "signed", "queued", "requires_attention")),
            ).values(status="invalidated_instance")
        )
        session.execute(
            ChainTransaction.__table__.update().where(
                ChainTransaction.namespace_id == item.id,
                ChainTransaction.status.in_(("prepared", "sending", "submitted")),
            ).values(status="invalidated_instance")
        )
    if not current.active:
        raise APIError(409, "deployment_instance_inactive", "Deployment namespace is inactive")
    return current


def begin_operation(
    session: Session,
    *,
    namespace_id: UUID,
    principal_id: UUID,
    operation_kind: str,
    idempotency_key: str,
    validated_payload: dict[str, Any],
) -> tuple[Operation, bool]:
    digest = payload_sha256(validated_payload)
    operation = Operation(
        namespace_id=namespace_id,
        principal_id=principal_id,
        operation_kind=operation_kind,
        idempotency_key=idempotency_key,
        payload_hash=digest,
        status="awaiting_authorization",
    )
    nested = session.begin_nested()
    try:
        session.add(operation)
        session.flush()
        nested.commit()
        return operation, False
    except IntegrityError:
        nested.rollback()
        existing = session.scalar(
            select(Operation).where(
                Operation.namespace_id == namespace_id,
                Operation.principal_id == principal_id,
                Operation.operation_kind == operation_kind,
                Operation.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            raise
        if existing.payload_hash != digest:
            raise APIError(
                409,
                "idempotency_payload_conflict",
                "Idempotency-Key was already used with a different validated payload",
                operation_id=str(existing.id),
            )
        return existing, True


def audit(
    session: Session,
    *,
    principal_id: UUID | None,
    action: str,
    outcome: str,
    operation_id: UUID | None = None,
    resource_type: str | None = None,
    resource_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            principal_id=principal_id,
            operation_id=operation_id,
            action=action,
            outcome=outcome,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata_json=metadata or {},
        )
    )
