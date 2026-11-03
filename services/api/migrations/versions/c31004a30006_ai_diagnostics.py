"""Add private, unsigned diagnostic records without changing risk_reports."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c31004a30006"
down_revision = "c31003a30005"
branch_labels = None
depends_on = None


def upgrade():
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table("ai_diagnostics",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("namespace_id", uuid, nullable=False),
        sa.Column("procurement_id", uuid, nullable=False),
        sa.Column("operation_id", uuid, nullable=False),
        sa.Column("creator_user_id", uuid, nullable=False),
        sa.Column("purchase_order_version_id", uuid, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("context_source", sa.String(24), nullable=False),
        sa.Column("evidence_version", sa.Numeric(20, 0), nullable=False),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("snapshot_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_fingerprint", sa.String(64), nullable=False),
        sa.Column("evidence_hash", sa.String(66), nullable=False),
        sa.Column("input_hash", sa.String(66), nullable=False),
        sa.Column("input_sha256", sa.String(66), nullable=False),
        sa.Column("input_json", postgresql.JSONB(), nullable=False),
        sa.Column("report_storage_key", sa.String(255)),
        sa.Column("report_hash", sa.String(66)),
        sa.Column("report_sha256", sa.String(66)),
        sa.Column("report_size_bytes", sa.Integer()),
        sa.Column("report_body", postgresql.JSONB()),
        sa.Column("error_code", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("operation_id", name="uq_ai_diagnostic_operation"),
        sa.ForeignKeyConstraint(["namespace_id"], ["deployment_instances.id"]),
        sa.ForeignKeyConstraint(["procurement_id", "namespace_id"], ["procurements.id", "procurements.namespace_id"], name="fk_ai_diagnostic_proc_ns"),
        sa.ForeignKeyConstraint(["operation_id", "namespace_id"], ["operations.id", "operations.namespace_id"], name="fk_ai_diagnostic_op_ns"),
        sa.ForeignKeyConstraint(["creator_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["purchase_order_version_id"], ["document_versions.id"]),
        sa.CheckConstraint("status IN ('queued','completed','failed','requires_attention')", name="ck_ai_diagnostic_status"),
        sa.CheckConstraint("context_source IN ('user_declared','demo_generated')", name="ck_ai_diagnostic_context_source"),
        sa.CheckConstraint("evidence_version >= 1 AND evidence_version <= 18446744073709551615", name="ck_ai_diagnostic_evidence_version"),
        sa.CheckConstraint("status <> 'completed' OR (report_storage_key IS NOT NULL AND report_hash IS NOT NULL AND report_sha256 IS NOT NULL AND report_size_bytes > 0 AND report_body IS NOT NULL)", name="ck_ai_diagnostic_completed_report"),
    )
    op.execute("""
    CREATE FUNCTION pog_ai_diagnostic_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'private diagnostic history is immutable'; END IF;
      IF (to_jsonb(NEW) - ARRAY['status','report_storage_key','report_hash','report_sha256','report_size_bytes','report_body','error_code'])
          IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status','report_storage_key','report_hash','report_sha256','report_size_bytes','report_body','error_code']) THEN
        RAISE EXCEPTION 'diagnostic input scope is immutable';
      END IF;
      IF OLD.status <> 'queued' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'terminal diagnostic report is immutable';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER immutable_ai_diagnostic BEFORE UPDATE OR DELETE ON ai_diagnostics
      FOR EACH ROW EXECUTE FUNCTION pog_ai_diagnostic_immutable();
    """)


def downgrade():
    raise RuntimeError("AI diagnostics migration is forward-only; preserve private evidence and audit history")
