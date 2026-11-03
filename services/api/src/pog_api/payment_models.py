"""Namespace-bound simulation accounting; never a bank or token balance oracle."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
                        Index, Integer, LargeBinary, Numeric, String, UniqueConstraint, event,
                        func, inspect, text)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .amounts import UINT256_MAX
from .models import Base, TimestampMixin


class SimHKDAccount(TimestampMixin, Base):
    __tablename__ = "sim_hkd_accounts"
    __table_args__ = (
        UniqueConstraint("namespace_id", "party_key", name="uq_sim_hkd_party"),
        UniqueConstraint("id", "namespace_id", name="uq_sim_hkd_id_namespace"),
        Index("uq_sim_hkd_owner", "namespace_id", "owner_user_id", unique=True,
              postgresql_where=text("owner_user_id IS NOT NULL")),
        CheckConstraint("role IN ('donor','foundation','supplier','clearing','fixture_equity')", name="ck_sim_hkd_role"),
        CheckConstraint(f"available_cents BETWEEN -{UINT256_MAX} AND {UINT256_MAX} AND held_cents BETWEEN 0 AND {UINT256_MAX}", name="ck_sim_hkd_range"),
        CheckConstraint("role = 'fixture_equity' OR available_cents >= 0", name="ck_sim_hkd_nonnegative"),
        CheckConstraint(f"available_cents + held_cents <= {UINT256_MAX}", name="ck_sim_hkd_total"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("deployment_instances.id", ondelete="CASCADE"), nullable=False)
    party_key: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    wallet_address: Mapped[str | None] = mapped_column(String(42))
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    available_cents: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False, default=0)
    held_cents: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False, default=0)
    fixture_version: Mapped[str] = mapped_column(String(40), nullable=False)


class HKDJournal(TimestampMixin, Base):
    """A fixed two-entry balanced journal: debit -cents; credit +cents."""
    __tablename__ = "sim_hkd_journals"
    __table_args__ = (
        UniqueConstraint("namespace_id", "operation_id", "effect_kind", name="uq_hkd_journal_effect"),
        UniqueConstraint("namespace_id", "fixture_key", name="uq_hkd_journal_fixture"),
        UniqueConstraint("id", "namespace_id", name="uq_hkd_journal_id_namespace"),
        ForeignKeyConstraint(["operation_id", "namespace_id"], ["operations.id", "operations.namespace_id"], ondelete="CASCADE", name="fk_hkd_journal_operation_ns"),
        ForeignKeyConstraint(["debit_account_id", "namespace_id"], ["sim_hkd_accounts.id", "sim_hkd_accounts.namespace_id"], name="fk_hkd_journal_debit_ns"),
        ForeignKeyConstraint(["credit_account_id", "namespace_id"], ["sim_hkd_accounts.id", "sim_hkd_accounts.namespace_id"], name="fk_hkd_journal_credit_ns"),
        CheckConstraint(f"cents > 0 AND cents <= {UINT256_MAX}", name="ck_hkd_journal_cents"),
        CheckConstraint("debit_account_id <> credit_account_id", name="ck_hkd_journal_parties"),
        CheckConstraint("(operation_id IS NOT NULL AND fixture_key IS NULL) OR (operation_id IS NULL AND fixture_key IS NOT NULL)", name="ck_hkd_journal_source"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("deployment_instances.id", ondelete="CASCADE"), nullable=False)
    operation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    effect_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    fixture_key: Mapped[str | None] = mapped_column(String(128))
    debit_account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    credit_account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    cents: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False)


class PaymentResource(TimestampMixin, Base):
    __tablename__ = "payment_resources"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_payment_resource_operation"),
        UniqueConstraint("id", "namespace_id", name="uq_payment_resource_id_namespace"),
        Index("uq_payment_proc_once", "namespace_id", "procurement_id", "kind", unique=True,
              postgresql_where=text("procurement_id IS NOT NULL")),
        ForeignKeyConstraint(["operation_id", "namespace_id"], ["operations.id", "operations.namespace_id"], ondelete="CASCADE", name="fk_payment_resource_operation_ns"),
        ForeignKeyConstraint(["source_operation_id", "namespace_id"], ["operations.id", "operations.namespace_id"], name="fk_payment_resource_source_ns"),
        ForeignKeyConstraint(["project_id", "namespace_id"], ["projects.id", "projects.namespace_id"], ondelete="CASCADE", name="fk_payment_resource_project_ns"),
        ForeignKeyConstraint(["procurement_id", "namespace_id"], ["procurements.id", "procurements.namespace_id"], ondelete="CASCADE", name="fk_payment_resource_proc_ns"),
        CheckConstraint("kind IN ('funding','redemption','supplier_payment')", name="ck_payment_resource_kind"),
        CheckConstraint("status IN ('queued','held','chain_submitted','chain_confirmed','reconciled','failed','requires_attention','invalidated_instance')", name="ck_payment_resource_status"),
        CheckConstraint(f"amount_atomic > 0 AND amount_atomic <= {UINT256_MAX} AND hkd_cents > 0 AND hkd_cents <= {UINT256_MAX} AND amount_atomic = hkd_cents * 10000", name="ck_payment_resource_amount"),
        CheckConstraint("allocated_atomic >= 0 AND allocated_atomic <= amount_atomic", name="ck_payment_resource_allocation"),
        CheckConstraint("(kind = 'funding' AND procurement_id IS NULL AND source_operation_id IS NULL) OR (kind <> 'funding' AND procurement_id IS NOT NULL AND source_operation_id IS NOT NULL)", name="ck_payment_resource_scope"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("deployment_instances.id", ondelete="CASCADE"), nullable=False)
    operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source_operation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    actor_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    procurement_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    hkd_cents: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False)
    amount_atomic: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False)
    allocated_atomic: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False, default=0)
    actor_wallet: Mapped[str] = mapped_column(String(42), nullable=False)
    counterparty_wallet: Mapped[str] = mapped_column(String(42), nullable=False)
    token_address: Mapped[str] = mapped_column(String(42), nullable=False)
    invoice_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="RESTRICT"))
    source_material: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    chain_proof: Mapped[dict | None] = mapped_column(JSONB)
    journal_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    error_code: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class FundedClaim(TimestampMixin, Base):
    __tablename__ = "funded_claims"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_funded_claim_operation"),
        ForeignKeyConstraint(["operation_id", "namespace_id"], ["operations.id", "operations.namespace_id"], ondelete="CASCADE", name="fk_funded_claim_operation_ns"),
        ForeignKeyConstraint(["funding_resource_id", "namespace_id"], ["payment_resources.id", "payment_resources.namespace_id"], ondelete="CASCADE", name="fk_funded_claim_resource_ns"),
        ForeignKeyConstraint(["project_id", "namespace_id"], ["projects.id", "projects.namespace_id"], ondelete="CASCADE", name="fk_funded_claim_project_ns"),
        CheckConstraint(f"amount_atomic > 0 AND amount_atomic <= {UINT256_MAX}", name="ck_funded_claim_amount"),
        CheckConstraint("status IN ('reserved','consumed','requires_attention','invalidated_instance')", name="ck_funded_claim_status"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("deployment_instances.id", ondelete="CASCADE"), nullable=False)
    operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    funding_resource_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    donor_wallet: Mapped[str] = mapped_column(String(42), nullable=False)
    amount_atomic: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="reserved")
    chain_proof: Mapped[dict | None] = mapped_column(JSONB)


class PaymentEvidence(TimestampMixin, Base):
    __tablename__ = "payment_evidence"
    __table_args__ = (
        UniqueConstraint("resource_id", name="uq_payment_evidence_resource"),
        ForeignKeyConstraint(["resource_id", "namespace_id"], ["payment_resources.id", "payment_resources.namespace_id"], ondelete="CASCADE", name="fk_payment_evidence_resource_ns"),
        ForeignKeyConstraint(["procurement_id", "namespace_id"], ["procurements.id", "procurements.namespace_id"], ondelete="CASCADE", name="fk_payment_evidence_proc_ns"),
        CheckConstraint("kind IN ('conversion','supplier_payment')", name="ck_payment_evidence_kind"),
        CheckConstraint("char_length(sha256_hex) = 64 AND char_length(keccak256_hex) = 64", name="ck_payment_evidence_hashes"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("deployment_instances.id", ondelete="CASCADE"), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    procurement_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sha256_hex: Mapped[str] = mapped_column(String(64), nullable=False)
    keccak256_hex: Mapped[str] = mapped_column(String(64), nullable=False)


class CanonicalOutflow(TimestampMixin, Base):
    __tablename__ = "payment_token_outflows"
    __table_args__ = (
        UniqueConstraint("namespace_id", "tx_hash", "log_index", "block_hash", name="uq_payment_outflow_identity"),
        CheckConstraint(f"amount_atomic > 0 AND amount_atomic <= {UINT256_MAX}", name="ck_payment_outflow_amount"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("deployment_instances.id", ondelete="CASCADE"), nullable=False)
    donor_wallet: Mapped[str] = mapped_column(String(42), nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    log_index: Mapped[int] = mapped_column(Integer, nullable=False)
    block_hash: Mapped[str] = mapped_column(String(66), nullable=False)
    block_number: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_atomic: Mapped[Decimal] = mapped_column(Numeric(78, 0), nullable=False)
    claim_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("funded_claims.id", ondelete="CASCADE"))
    matched: Mapped[bool] = mapped_column(Boolean, nullable=False)


@event.listens_for(HKDJournal, "before_update")
@event.listens_for(HKDJournal, "before_delete")
@event.listens_for(PaymentEvidence, "before_update")
@event.listens_for(PaymentEvidence, "before_delete")
def _immutable_record(mapper, connection, target):
    raise ValueError("Simulation journal and payment evidence are immutable")


@event.listens_for(PaymentResource, "before_update")
def _immutable_payment_scope(mapper, connection, target):
    immutable = ("namespace_id", "operation_id", "source_operation_id", "actor_user_id",
                 "project_id", "procurement_id", "kind", "hkd_cents", "amount_atomic",
                 "actor_wallet", "counterparty_wallet", "token_address", "invoice_version_id", "source_material")
    if any(inspect(target).attrs[name].history.has_changes() for name in immutable):
        raise ValueError("Payment scope and original source material are immutable")


@event.listens_for(FundedClaim, "before_update")
def _immutable_claim_scope(mapper, connection, target):
    immutable = ("id", "namespace_id", "operation_id", "funding_resource_id", "actor_user_id",
                 "project_id", "donor_wallet", "amount_atomic", "created_at")
    if any(inspect(target).attrs[name].history.has_changes() for name in immutable):
        raise ValueError("Funded allocation scope and amount are immutable")


PAYMENT_TABLES = (SimHKDAccount.__table__, HKDJournal.__table__, PaymentResource.__table__,
                  FundedClaim.__table__, PaymentEvidence.__table__, CanonicalOutflow.__table__)
