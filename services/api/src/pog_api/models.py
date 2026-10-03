from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .amounts import UINT256_MAX


UINT256_SQL = f"value >= 0 AND value <= {UINT256_MAX}"
ROLE_NAMES = (
    "foundation",
    "recipient",
    "donor",
    "human_approver",
    "service_ai",
    "service_payment",
)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Role(Base):
    __tablename__ = "roles"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class WalletAuthorization(TimestampMixin, Base):
    __tablename__ = "wallet_authorizations"
    __table_args__ = (
        UniqueConstraint("user_id", "role_name", name="uq_wallet_auth_user_role"),
        UniqueConstraint("wallet_address", "role_name", name="uq_wallet_auth_wallet_role"),
        CheckConstraint("wallet_address ~ '^0x[0-9a-fA-F]{40}$'", name="ck_wallet_address"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_name: Mapped[str] = mapped_column(
        String(32), ForeignKey("roles.name", ondelete="RESTRICT"), nullable=False
    )
    wallet_address: Mapped[str] = mapped_column(String(42), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class SessionRecord(TimestampMixin, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="ck_session_expiry"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeploymentInstance(TimestampMixin, Base):
    __tablename__ = "deployment_instances"
    __table_args__ = (
        UniqueConstraint("run_id", "instance_id", name="uq_deployment_namespace"),
        CheckConstraint("mode IN ('mock', 'verified')", name="ck_deployment_mode"),
        CheckConstraint("verified = false OR mode = 'verified'", name="ck_verified_mode"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    instance_id: Mapped[str] = mapped_column(String(128), nullable=False)
    chain_id: Mapped[int | None] = mapped_column(Integer)
    genesis_hash: Mapped[str | None] = mapped_column(String(66))
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="mock")
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("namespace_id", "business_id", name="uq_project_namespace_business"),
        UniqueConstraint("id", "namespace_id", name="uq_project_id_namespace"),
        CheckConstraint("chain_status = 'off_chain_draft'", name="ck_a1_project_chain_status"),
        CheckConstraint("business_id ~ '^0x[0-9a-f]{64}$'", name="ck_project_business_id"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("deployment_instances.id"), nullable=False
    )
    business_id: Mapped[str] = mapped_column(String(66), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    public_summary: Mapped[str] = mapped_column(Text, nullable=False)
    foundation_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"))
    recipient_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"))
    human_approver_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id")
    )
    foundation_wallet: Mapped[str] = mapped_column(String(42), nullable=False)
    recipient_wallet: Mapped[str] = mapped_column(String(42), nullable=False)
    asset_symbol: Mapped[str] = mapped_column(String(16), nullable=False, default="mHKD")
    fairness_rule: Mapped[str] = mapped_column(Text, nullable=False)
    chain_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="off_chain_draft"
    )


class Procurement(TimestampMixin, Base):
    __tablename__ = "procurements"
    __table_args__ = (
        UniqueConstraint("namespace_id", "business_id", name="uq_proc_namespace_business"),
        UniqueConstraint("id", "namespace_id", name="uq_proc_id_namespace"),
        ForeignKeyConstraint(
            ["project_id", "namespace_id"],
            ["projects.id", "projects.namespace_id"],
            ondelete="CASCADE",
            name="fk_proc_project_namespace",
        ),
        CheckConstraint("chain_status = 'off_chain_draft'", name="ck_a1_proc_chain_status"),
        CheckConstraint("business_id ~ '^0x[0-9a-f]{64}$'", name="ck_proc_business_id"),
        CheckConstraint(
            f"budget_cap_atomic >= 0 AND budget_cap_atomic <= {UINT256_MAX}",
            name="ck_proc_budget_uint256",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("deployment_instances.id"), nullable=False
    )
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    business_id: Mapped[str] = mapped_column(String(66), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    foundation_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"))
    vendor_wallet: Mapped[str] = mapped_column(String(42), nullable=False)
    budget_cap_atomic: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False)
    chain_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="off_chain_draft"
    )


class Operation(TimestampMixin, Base):
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id",
            "principal_id",
            "operation_kind",
            "idempotency_key",
            name="uq_operation_idempotency",
        ),
        CheckConstraint("char_length(idempotency_key) BETWEEN 1 AND 128", name="ck_idem_length"),
        CheckConstraint(
            "idempotency_key ~ '^[ -~]+$'",
            name="ck_idem_printable_ascii",
        ),
        CheckConstraint(
            "status IN ('awaiting_authorization','queued','submitted','confirmed','failed','requires_attention','invalidated_instance')",
            name="ck_operation_status",
        ),
        CheckConstraint(
            "error_status IS NULL OR error_status BETWEEN 400 AND 599",
            name="ck_operation_error_status",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("deployment_instances.id"), nullable=False
    )
    principal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"))
    operation_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_resource_type: Mapped[str | None] = mapped_column(String(40))
    result_resource_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_status: Mapped[int | None] = mapped_column(Integer)
    error_detail: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class OperationStep(TimestampMixin, Base):
    __tablename__ = "operation_steps"
    __table_args__ = (
        UniqueConstraint("operation_id", "step_index", name="uq_operation_step_index"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.id", ondelete="CASCADE"), nullable=False
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class AuditLog(TimestampMixin, Base):
    __tablename__ = "audit_logs"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    principal_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"))
    operation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("operations.id")
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["procurement_id", "namespace_id"],
            ["procurements.id", "procurements.namespace_id"],
            ondelete="CASCADE",
            name="fk_document_procurement_namespace",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("deployment_instances.id"), nullable=False
    )
    procurement_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"))


class DocumentVersion(TimestampMixin, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_version"),
        UniqueConstraint("storage_key", name="uq_document_storage_key"),
        CheckConstraint("size_bytes BETWEEN 1 AND 10485760", name="ck_document_size"),
        CheckConstraint(
            "content_type IN ('application/pdf','image/jpeg','image/png')",
            name="ck_document_content_type",
        ),
        CheckConstraint("char_length(sha256_hex) = 64", name="ck_document_sha256"),
        CheckConstraint("char_length(keccak256_hex) = 64", name="ck_document_keccak"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256_hex: Mapped[str] = mapped_column(String(64), nullable=False)
    keccak256_hex: Mapped[str] = mapped_column(String(64), nullable=False)
    abi_combination_keccak_hex: Mapped[str | None] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_by_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"))
    referenced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RiskReport(TimestampMixin, Base):
    __tablename__ = "risk_reports"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    procurement_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("procurements.id"))
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str | None] = mapped_column(String(40))
    report_hash: Mapped[str | None] = mapped_column(String(66))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="unavailable")


class ReceiptProof(TimestampMixin, Base):
    __tablename__ = "receipt_proofs"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    procurement_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("procurements.id"))
    evidence_document_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("document_versions.id")
    )
    signer_wallet: Mapped[str | None] = mapped_column(String(42))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="awaiting_authorization")


class ApprovalRecord(TimestampMixin, Base):
    __tablename__ = "approval_records"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    procurement_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("procurements.id")
    )
    project_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("projects.id"))
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    signer_wallet: Mapped[str | None] = mapped_column(String(42))
    nonce_text: Mapped[str | None] = mapped_column(String(78))
    policy_epoch: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="awaiting_authorization")


class ChainTransaction(TimestampMixin, Base):
    __tablename__ = "chain_transactions"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("operations.id"))
    tx_hash: Mapped[str | None] = mapped_column(String(66))
    calldata_hash: Mapped[str | None] = mapped_column(String(66))
    receipt_json: Mapped[dict | None] = mapped_column(JSONB)
    block_hash: Mapped[str | None] = mapped_column(String(66))
    canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ChainEvent(TimestampMixin, Base):
    __tablename__ = "chain_events"
    __table_args__ = (
        UniqueConstraint(
            "namespace_id", "contract_address", "tx_hash", "log_index", "block_hash",
            name="uq_chain_event_identity",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("deployment_instances.id"))
    contract_address: Mapped[str] = mapped_column(String(42), nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    log_index: Mapped[int] = mapped_column(Integer, nullable=False)
    block_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    event_name: Mapped[str] = mapped_column(String(100), nullable=False)
    canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class IndexerCursor(TimestampMixin, Base):
    __tablename__ = "indexer_cursors"
    __table_args__ = (
        UniqueConstraint("namespace_id", "consumer_name", name="uq_indexer_cursor"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("deployment_instances.id"))
    consumer_name: Mapped[str] = mapped_column(String(80), nullable=False)
    next_block: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_block_hash: Mapped[str | None] = mapped_column(String(66))


class PaymentOperationLink(TimestampMixin, Base):
    __tablename__ = "payment_operation_links"
    __table_args__ = (
        UniqueConstraint("provider_operation_id", name="uq_payment_provider_operation"),
        CheckConstraint(
            f"amount_atomic >= 0 AND amount_atomic <= {UINT256_MAX}",
            name="ck_payment_amount_uint256",
        ),
        CheckConstraint("mode IN ('unavailable','mock')", name="ck_payment_mode"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    procurement_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("procurements.id"))
    provider_operation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount_atomic: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="unavailable")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="unavailable")
