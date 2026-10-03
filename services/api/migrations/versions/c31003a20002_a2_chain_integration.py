"""A2 local-chain integration persistence.

Revision ID: c31003a20002
Revises: 84fcc48891be
Create Date: 2026-10-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c31003a20002"
down_revision = "84fcc48891be"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deployment_instances", sa.Column("rpc_url", sa.String(255)))
    op.add_column("deployment_instances", sa.Column("manifest_sha256", sa.String(64)))
    op.add_column("deployment_instances", sa.Column("manifest_json", postgresql.JSONB()))

    op.drop_constraint("ck_a1_project_chain_status", "projects", type_="check")
    op.create_check_constraint(
        "ck_a2_project_chain_status", "projects",
        "chain_status IN ('off_chain_draft','create_queued','active','closing','refundable','closed')",
    )
    op.add_column("projects", sa.Column("chain_tx_hash", sa.String(66)))
    op.add_column("projects", sa.Column("chain_block_number", sa.BigInteger()))

    op.drop_constraint("ck_a1_proc_chain_status", "procurements", type_="check")
    op.create_check_constraint(
        "ck_a2_proc_chain_status", "procurements",
        "chain_status IN ('off_chain_draft','create_queued','created','po_queued','po_recorded',"
        "'ai_pre_queued','pre_assessed','reserve_vote_queued','reserve_approval_pending',"
        "'reserve_queued','reserved','invoice_queued','invoice_recorded','receipt_queued',"
        "'receipt_confirmed','final_assessed','release_approval_pending','funds_released',"
        "'settlement_recorded','settlement_approval_pending','payment_confirmed',"
        "'cancellation_approval_pending','cancelled')",
    )
    for name in (
        "po_hash", "request_hash", "goods_request_hash", "pre_evidence_hash",
        "pre_assessment_id", "invoice_hash", "goods_hash", "receipt_digest", "chain_tx_hash",
    ):
        op.add_column("procurements", sa.Column(name, sa.String(66)))
    op.add_column("procurements", sa.Column("reserved_amount_atomic", sa.Numeric(78, 0)))
    op.add_column("procurements", sa.Column("invoice_amount_atomic", sa.Numeric(78, 0)))
    op.add_column("procurements", sa.Column("chain_block_number", sa.BigInteger()))

    op.add_column("chain_transactions", sa.Column("step_id", sa.UUID()))
    op.add_column("chain_transactions", sa.Column("namespace_id", sa.UUID()))
    op.add_column("chain_transactions", sa.Column("caller_address", sa.String(42)))
    op.add_column("chain_transactions", sa.Column("to_address", sa.String(42)))
    op.add_column("chain_transactions", sa.Column("chain_id", sa.BigInteger()))
    op.add_column("chain_transactions", sa.Column("evm_nonce_text", sa.String(78)))
    op.add_column("chain_transactions", sa.Column("calldata", sa.Text()))
    op.add_column("chain_transactions", sa.Column("value_text", sa.String(78)))
    op.add_column("chain_transactions", sa.Column("envelope_hash", sa.String(66)))
    op.add_column(
        "chain_transactions", sa.Column("status", sa.String(32), nullable=False, server_default="prepared")
    )
    op.add_column("chain_transactions", sa.Column("submitted_at", sa.DateTime(timezone=True)))
    op.add_column("chain_transactions", sa.Column("confirmed_at", sa.DateTime(timezone=True)))
    op.create_foreign_key(
        "fk_chain_tx_step", "chain_transactions", "operation_steps", ["step_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_chain_tx_namespace", "chain_transactions", "deployment_instances",
        ["namespace_id"], ["id"],
    )
    op.create_unique_constraint("uq_chain_tx_step", "chain_transactions", ["step_id"])
    op.create_unique_constraint(
        "uq_chain_tx_caller_nonce", "chain_transactions",
        ["namespace_id", "caller_address", "evm_nonce_text"],
    )
    op.create_check_constraint(
        "ck_chain_tx_status", "chain_transactions",
        "status IN ('prepared','sending','submitted','confirmed','failed','requires_attention','invalidated_instance')",
    )

    op.create_table(
        "signing_requests",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("namespace_id", sa.UUID(), sa.ForeignKey("deployment_instances.id"), nullable=False),
        sa.Column("operation_id", sa.UUID(), sa.ForeignKey("operations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("procurement_id", sa.UUID(), sa.ForeignKey("procurements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("signer_user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("contract_address", sa.String(42), nullable=False),
        sa.Column("signer_wallet", sa.String(42), nullable=False),
        sa.Column("nonce_text", sa.String(78), nullable=False),
        sa.Column("deadline_text", sa.String(20), nullable=False),
        sa.Column("policy_epoch", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("typed_data", postgresql.JSONB(), nullable=False),
        sa.Column("context_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("digest", sa.String(66), nullable=False),
        sa.Column("signature", sa.Text()),
        sa.Column("submitted_operation_id", sa.UUID(), sa.ForeignKey("operations.id")),
        sa.Column("authorized_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('ai_pre','reserve','receipt')", name="ck_signing_kind"),
        sa.CheckConstraint(
            "status IN ('prepared','signed','queued','confirmed','failed','requires_attention','invalidated_instance')",
            name="ck_signing_status",
        ),
        sa.CheckConstraint("deadline_text ~ '^[0-9]+$'", name="ck_signing_deadline_text"),
        sa.CheckConstraint("nonce_text ~ '^[0-9]+$'", name="ck_signing_nonce_text"),
        sa.CheckConstraint("policy_epoch BETWEEN 0 AND 4294967295", name="ck_signing_policy_epoch"),
        sa.UniqueConstraint("operation_id", name="uq_signing_request_operation"),
        sa.UniqueConstraint(
            "namespace_id", "contract_address", "signer_wallet", "nonce_text", "kind",
            name="uq_signing_request_nonce_family",
        ),
    )
    op.create_table(
        "ledger_projections",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("namespace_id", sa.UUID(), sa.ForeignKey("deployment_instances.id"), nullable=False),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_address", sa.String(42), nullable=False),
        sa.Column("deposits_atomic", sa.Numeric(78, 0), nullable=False, server_default="0"),
        sa.Column("reserved_atomic", sa.Numeric(78, 0), nullable=False, server_default="0"),
        sa.Column("released_atomic", sa.Numeric(78, 0), nullable=False, server_default="0"),
        sa.Column("returned_atomic", sa.Numeric(78, 0), nullable=False, server_default="0"),
        sa.Column("policy_epoch", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("threshold", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("block_number", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("block_hash", sa.String(66)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("namespace_id", "project_id", name="uq_ledger_projection_project"),
        sa.CheckConstraint(
            "deposits_atomic >= 0 AND reserved_atomic >= 0 AND released_atomic >= 0 AND returned_atomic >= 0",
            name="ck_ledger_amounts_nonnegative",
        ),
        sa.CheckConstraint("policy_epoch BETWEEN 0 AND 4294967295", name="ck_ledger_policy_epoch"),
    )
    op.create_table(
        "donor_credit_projections",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("namespace_id", sa.UUID(), sa.ForeignKey("deployment_instances.id"), nullable=False),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("donor_wallet", sa.String(42), nullable=False),
        sa.Column("credit_atomic", sa.Numeric(78, 0), nullable=False, server_default="0"),
        sa.Column("block_number", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("namespace_id", "project_id", "donor_wallet", name="uq_donor_credit"),
        sa.CheckConstraint("credit_atomic >= 0", name="ck_donor_credit_nonnegative"),
    )


def downgrade() -> None:
    op.drop_table("donor_credit_projections")
    op.drop_table("ledger_projections")
    op.drop_table("signing_requests")
    op.drop_constraint("ck_chain_tx_status", "chain_transactions", type_="check")
    op.drop_constraint("uq_chain_tx_caller_nonce", "chain_transactions", type_="unique")
    op.drop_constraint("uq_chain_tx_step", "chain_transactions", type_="unique")
    op.drop_constraint("fk_chain_tx_namespace", "chain_transactions", type_="foreignkey")
    op.drop_constraint("fk_chain_tx_step", "chain_transactions", type_="foreignkey")
    for name in (
        "confirmed_at", "submitted_at", "status", "envelope_hash", "value_text", "calldata",
        "evm_nonce_text", "chain_id", "to_address", "caller_address", "namespace_id", "step_id",
    ):
        op.drop_column("chain_transactions", name)
    for name in (
        "chain_block_number", "chain_tx_hash", "receipt_digest", "goods_hash",
        "invoice_amount_atomic", "invoice_hash", "reserved_amount_atomic", "pre_assessment_id",
        "pre_evidence_hash", "goods_request_hash", "request_hash", "po_hash",
    ):
        op.drop_column("procurements", name)
    op.drop_constraint("ck_a2_proc_chain_status", "procurements", type_="check")
    op.create_check_constraint("ck_a1_proc_chain_status", "procurements", "chain_status = 'off_chain_draft'")
    op.drop_column("projects", "chain_block_number")
    op.drop_column("projects", "chain_tx_hash")
    op.drop_constraint("ck_a2_project_chain_status", "projects", type_="check")
    op.create_check_constraint("ck_a1_project_chain_status", "projects", "chain_status = 'off_chain_draft'")
    op.drop_column("deployment_instances", "manifest_json")
    op.drop_column("deployment_instances", "manifest_sha256")
    op.drop_column("deployment_instances", "rpc_url")
