"""Private diagnostics; never a RiskReport, authorization or chain assessment."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, TimestampMixin


class AIDiagnostic(TimestampMixin, Base):
    __tablename__ = "ai_diagnostics"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_ai_diagnostic_operation"),
        ForeignKeyConstraint(["procurement_id", "namespace_id"], ["procurements.id", "procurements.namespace_id"], name="fk_ai_diagnostic_proc_ns"),
        ForeignKeyConstraint(["operation_id", "namespace_id"], ["operations.id", "operations.namespace_id"], name="fk_ai_diagnostic_op_ns"),
        CheckConstraint("status IN ('queued','completed','failed','requires_attention')", name="ck_ai_diagnostic_status"),
        CheckConstraint("context_source IN ('user_declared','demo_generated')", name="ck_ai_diagnostic_context_source"),
        CheckConstraint("evidence_version >= 1 AND evidence_version <= 18446744073709551615", name="ck_ai_diagnostic_evidence_version"),
        CheckConstraint("status <> 'completed' OR (report_storage_key IS NOT NULL AND report_hash IS NOT NULL AND report_sha256 IS NOT NULL AND report_size_bytes > 0 AND report_body IS NOT NULL)", name="ck_ai_diagnostic_completed_report"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("deployment_instances.id"), nullable=False)
    procurement_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    creator_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    purchase_order_version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("document_versions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    context_source: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_version: Mapped[int] = mapped_column(Numeric(20, 0), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(66), nullable=False)
    input_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    report_storage_key: Mapped[str | None] = mapped_column(String(255))
    report_hash: Mapped[str | None] = mapped_column(String(66))
    report_sha256: Mapped[str | None] = mapped_column(String(66))
    report_size_bytes: Mapped[int | None] = mapped_column(Integer)
    report_body: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(80))
