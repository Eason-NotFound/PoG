"""Additive A2 review guards, renewable authorization and canonical policy.

Revision ID: c31003a20004
Revises: c31003a20003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c31003a20004"
down_revision = "c31003a20003"
branch_labels = None
depends_on = None
UINT256_MAX = 2**256 - 1


def upgrade():
    # Never rewrite published migrations or mutate historical authorization rows.
    # Published 003 already replaced the constraint with a partial index.
    op.drop_index("uq_signing_request_nonce_family", table_name="signing_requests")
    op.create_index("uq_signing_request_nonce_family", "signing_requests", ["namespace_id", "contract_address", "signer_wallet", "nonce_text", "kind"], unique=True, postgresql_where=sa.text("status NOT IN ('expired','invalidated_stale','invalidated_instance','invalidated_not_broadcast')"))
    op.drop_constraint("ck_signing_status", "signing_requests", type_="check")
    op.create_check_constraint("ck_signing_status", "signing_requests", "status IN ('prepared','signed','queued','confirmed','failed','requires_attention','invalidated_instance','expired','invalidated_stale','invalidated_not_broadcast')")
    op.create_check_constraint("ck_signing_terminal_unsubmitted", "signing_requests", "status NOT IN ('expired','invalidated_stale') OR submitted_operation_id IS NULL")
    for column, bound in (("nonce", UINT256_MAX), ("deadline", 2**64 - 1)):
        name = f"ck_signing_{column}_text"
        op.drop_constraint(name, "signing_requests", type_="check")
        op.create_check_constraint(name, "signing_requests", f"{column}_text ~ '^(0|[1-9][0-9]*)$' AND {column}_text::numeric <= {bound}")
    op.drop_constraint("uq_chain_tx_caller_nonce", "chain_transactions", type_="unique")
    op.create_index("uq_chain_tx_caller_nonce", "chain_transactions", ["namespace_id", "caller_address", "evm_nonce_text"], unique=True, postgresql_where=sa.text("status <> 'not_broadcast'"))
    op.drop_constraint("ck_chain_tx_status", "chain_transactions", type_="check")
    op.create_check_constraint("ck_chain_tx_status", "chain_transactions", "status IN ('prepared','sending','submitted','confirmed','failed','requires_attention','invalidated_instance','not_broadcast')")
    for column in ("reserved_amount_atomic", "invoice_amount_atomic"):
        name = "ck_proc_reserved_uint256" if column.startswith("reserved") else "ck_proc_invoice_uint256"
        op.create_check_constraint(name, "procurements", f"{column} >= 0 AND {column} <= {UINT256_MAX}")
    op.drop_constraint("ck_ledger_amounts_nonnegative", "ledger_projections", type_="check")
    op.create_check_constraint("ck_ledger_amounts_nonnegative", "ledger_projections", " AND ".join(f"{column} >= 0 AND {column} <= {UINT256_MAX}" for column in ("deposits_atomic", "reserved_atomic", "released_atomic", "returned_atomic")))
    op.drop_constraint("ck_donor_credit_nonnegative", "donor_credit_projections", type_="check")
    op.create_check_constraint("ck_donor_credit_nonnegative", "donor_credit_projections", f"credit_atomic >= 0 AND credit_atomic <= {UINT256_MAX}")
    op.add_column("chain_events", sa.Column("block_number", sa.BigInteger()))
    op.add_column("chain_events", sa.Column("transaction_index", sa.Integer()))
    op.create_table(
        "policy_projections",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("namespace_id", sa.UUID(), sa.ForeignKey("deployment_instances.id"), nullable=False),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_epoch", sa.BigInteger(), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("approver_wallets", postgresql.JSONB(), nullable=False),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("block_hash", sa.String(66), nullable=False),
        sa.Column("tx_hash", sa.String(66), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("namespace_id", "project_id", name="uq_policy_projection_project"),
        sa.CheckConstraint("policy_epoch BETWEEN 0 AND 4294967295", name="ck_policy_projection_epoch"),
        sa.CheckConstraint("threshold > 0", name="ck_policy_projection_threshold"),
    )


def downgrade():
    # Rollback must not silently discard renewal history or resurrect expired auth.
    raise RuntimeError("A2 review migration is forward-only; restore a verified backup for rollback")
