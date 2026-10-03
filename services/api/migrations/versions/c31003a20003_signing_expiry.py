"""Retain expired signing requests without locking an unconsumed nonce.

Revision ID: c31003a20003
Revises: c31003a20002
"""
from alembic import op
import sqlalchemy as sa

revision = "c31003a20003"
down_revision = "c31003a20002"
branch_labels = None
depends_on = None

OLD_STATUSES = "'prepared','signed','queued','confirmed','failed','requires_attention','invalidated_instance'"


def upgrade() -> None:
    op.drop_constraint("uq_signing_request_nonce_family", "signing_requests", type_="unique")
    op.drop_constraint("ck_signing_status", "signing_requests", type_="check")
    op.create_check_constraint("ck_signing_status", "signing_requests", f"status IN ({OLD_STATUSES},'expired')")
    op.create_index(
        "uq_signing_request_nonce_family", "signing_requests",
        ["namespace_id", "contract_address", "signer_wallet", "nonce_text", "kind"],
        unique=True, postgresql_where=sa.text("status <> 'expired'"),
    )


def downgrade() -> None:
    # The previous schema cannot represent retained expired authorizations.
    # Refuse instead of deleting history or silently changing its meaning.
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM signing_requests WHERE status='expired')")):
        raise RuntimeError("Cannot downgrade signing expiry while retained expired requests exist")
    op.drop_index("uq_signing_request_nonce_family", table_name="signing_requests")
    op.drop_constraint("ck_signing_status", "signing_requests", type_="check")
    op.create_check_constraint("ck_signing_status", "signing_requests", f"status IN ({OLD_STATUSES})")
    op.create_unique_constraint(
        "uq_signing_request_nonce_family", "signing_requests",
        ["namespace_id", "contract_address", "signer_wallet", "nonce_text", "kind"],
    )
