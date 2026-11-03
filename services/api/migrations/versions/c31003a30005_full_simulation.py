"""Forward-only local full simulation, preserving every published migration."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c31003a30005"
down_revision = "c31003a20004"
branch_labels = None
depends_on = None

# Frozen DDL: this migration never imports mutable application models.
PAYMENT_DDL = [
    "\nCREATE TABLE sim_hkd_accounts (\n\tid UUID NOT NULL, \n\tnamespace_id UUID NOT NULL, \n\tparty_key VARCHAR(80) NOT NULL, \n\towner_user_id UUID, \n\twallet_address VARCHAR(42), \n\trole VARCHAR(24) NOT NULL, \n\tavailable_cents NUMERIC(78, 0) NOT NULL, \n\theld_cents NUMERIC(78, 0) NOT NULL, \n\tfixture_version VARCHAR(40) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_sim_hkd_party UNIQUE (namespace_id, party_key), \n\tCONSTRAINT uq_sim_hkd_id_namespace UNIQUE (id, namespace_id), \n\tCONSTRAINT ck_sim_hkd_role CHECK (role IN ('donor','foundation','supplier','clearing','fixture_equity')), \n\tCONSTRAINT ck_sim_hkd_range CHECK (available_cents BETWEEN -115792089237316195423570985008687907853269984665640564039457584007913129639935 AND 115792089237316195423570985008687907853269984665640564039457584007913129639935 AND held_cents BETWEEN 0 AND 115792089237316195423570985008687907853269984665640564039457584007913129639935), \n\tCONSTRAINT ck_sim_hkd_nonnegative CHECK (role = 'fixture_equity' OR available_cents >= 0), \n\tCONSTRAINT ck_sim_hkd_total CHECK (available_cents + held_cents <= 115792089237316195423570985008687907853269984665640564039457584007913129639935), \n\tFOREIGN KEY(namespace_id) REFERENCES deployment_instances (id) ON DELETE CASCADE, \n\tFOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE CASCADE\n)\n\n",
    "CREATE UNIQUE INDEX uq_sim_hkd_owner ON sim_hkd_accounts (namespace_id, owner_user_id) WHERE owner_user_id IS NOT NULL",
    "\nCREATE TABLE sim_hkd_journals (\n\tid UUID NOT NULL, \n\tnamespace_id UUID NOT NULL, \n\toperation_id UUID, \n\teffect_kind VARCHAR(40) NOT NULL, \n\tfixture_key VARCHAR(128), \n\tdebit_account_id UUID NOT NULL, \n\tcredit_account_id UUID NOT NULL, \n\tcents NUMERIC(78, 0) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_hkd_journal_effect UNIQUE (namespace_id, operation_id, effect_kind), \n\tCONSTRAINT uq_hkd_journal_fixture UNIQUE (namespace_id, fixture_key), \n\tCONSTRAINT uq_hkd_journal_id_namespace UNIQUE (id, namespace_id), \n\tCONSTRAINT fk_hkd_journal_operation_ns FOREIGN KEY(operation_id, namespace_id) REFERENCES operations (id, namespace_id) ON DELETE CASCADE, \n\tCONSTRAINT fk_hkd_journal_debit_ns FOREIGN KEY(debit_account_id, namespace_id) REFERENCES sim_hkd_accounts (id, namespace_id), \n\tCONSTRAINT fk_hkd_journal_credit_ns FOREIGN KEY(credit_account_id, namespace_id) REFERENCES sim_hkd_accounts (id, namespace_id), \n\tCONSTRAINT ck_hkd_journal_cents CHECK (cents > 0 AND cents <= 115792089237316195423570985008687907853269984665640564039457584007913129639935), \n\tCONSTRAINT ck_hkd_journal_parties CHECK (debit_account_id <> credit_account_id), \n\tCONSTRAINT ck_hkd_journal_source CHECK ((operation_id IS NOT NULL AND fixture_key IS NULL) OR (operation_id IS NULL AND fixture_key IS NOT NULL)), \n\tFOREIGN KEY(namespace_id) REFERENCES deployment_instances (id) ON DELETE CASCADE\n)\n\n",
    "\nCREATE TABLE payment_resources (\n\tid UUID NOT NULL, \n\tnamespace_id UUID NOT NULL, \n\toperation_id UUID NOT NULL, \n\tsource_operation_id UUID, \n\tactor_user_id UUID NOT NULL, \n\tproject_id UUID NOT NULL, \n\tprocurement_id UUID, \n\tkind VARCHAR(24) NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\thkd_cents NUMERIC(78, 0) NOT NULL, \n\tamount_atomic NUMERIC(78, 0) NOT NULL, \n\tallocated_atomic NUMERIC(78, 0) NOT NULL, \n\tactor_wallet VARCHAR(42) NOT NULL, \n\tcounterparty_wallet VARCHAR(42) NOT NULL, \n\ttoken_address VARCHAR(42) NOT NULL, \n\tinvoice_version_id UUID, \n\tsource_material JSONB NOT NULL, \n\tchain_proof JSONB, \n\tjournal_ids JSONB NOT NULL, \n\terror_code VARCHAR(80), \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_payment_resource_operation UNIQUE (operation_id), \n\tCONSTRAINT uq_payment_resource_id_namespace UNIQUE (id, namespace_id), \n\tCONSTRAINT fk_payment_resource_operation_ns FOREIGN KEY(operation_id, namespace_id) REFERENCES operations (id, namespace_id) ON DELETE CASCADE, \n\tCONSTRAINT fk_payment_resource_source_ns FOREIGN KEY(source_operation_id, namespace_id) REFERENCES operations (id, namespace_id), \n\tCONSTRAINT fk_payment_resource_project_ns FOREIGN KEY(project_id, namespace_id) REFERENCES projects (id, namespace_id) ON DELETE CASCADE, \n\tCONSTRAINT fk_payment_resource_proc_ns FOREIGN KEY(procurement_id, namespace_id) REFERENCES procurements (id, namespace_id) ON DELETE CASCADE, \n\tCONSTRAINT ck_payment_resource_kind CHECK (kind IN ('funding','redemption','supplier_payment')), \n\tCONSTRAINT ck_payment_resource_status CHECK (status IN ('queued','held','chain_submitted','chain_confirmed','reconciled','failed','requires_attention','invalidated_instance')), \n\tCONSTRAINT ck_payment_resource_amount CHECK (amount_atomic > 0 AND amount_atomic <= 115792089237316195423570985008687907853269984665640564039457584007913129639935 AND hkd_cents > 0 AND hkd_cents <= 115792089237316195423570985008687907853269984665640564039457584007913129639935 AND amount_atomic = hkd_cents * 10000), \n\tCONSTRAINT ck_payment_resource_allocation CHECK (allocated_atomic >= 0 AND allocated_atomic <= amount_atomic), \n\tCONSTRAINT ck_payment_resource_scope CHECK ((kind = 'funding' AND procurement_id IS NULL AND source_operation_id IS NULL) OR (kind <> 'funding' AND procurement_id IS NOT NULL AND source_operation_id IS NOT NULL)), \n\tFOREIGN KEY(namespace_id) REFERENCES deployment_instances (id) ON DELETE CASCADE, \n\tFOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE CASCADE, \n\tFOREIGN KEY(invoice_version_id) REFERENCES document_versions (id) ON DELETE RESTRICT\n)\n\n",
    "CREATE UNIQUE INDEX uq_payment_proc_once ON payment_resources (namespace_id, procurement_id, kind) WHERE procurement_id IS NOT NULL",
    "\nCREATE TABLE funded_claims (\n\tid UUID NOT NULL, \n\tnamespace_id UUID NOT NULL, \n\toperation_id UUID NOT NULL, \n\tfunding_resource_id UUID NOT NULL, \n\tactor_user_id UUID NOT NULL, \n\tproject_id UUID NOT NULL, \n\tdonor_wallet VARCHAR(42) NOT NULL, \n\tamount_atomic NUMERIC(78, 0) NOT NULL, \n\tstatus VARCHAR(24) NOT NULL, \n\tchain_proof JSONB, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_funded_claim_operation UNIQUE (operation_id), \n\tCONSTRAINT fk_funded_claim_operation_ns FOREIGN KEY(operation_id, namespace_id) REFERENCES operations (id, namespace_id) ON DELETE CASCADE, \n\tCONSTRAINT fk_funded_claim_resource_ns FOREIGN KEY(funding_resource_id, namespace_id) REFERENCES payment_resources (id, namespace_id) ON DELETE CASCADE, \n\tCONSTRAINT fk_funded_claim_project_ns FOREIGN KEY(project_id, namespace_id) REFERENCES projects (id, namespace_id) ON DELETE CASCADE, \n\tCONSTRAINT ck_funded_claim_amount CHECK (amount_atomic > 0 AND amount_atomic <= 115792089237316195423570985008687907853269984665640564039457584007913129639935), \n\tCONSTRAINT ck_funded_claim_status CHECK (status IN ('reserved','consumed','requires_attention','invalidated_instance')), \n\tFOREIGN KEY(namespace_id) REFERENCES deployment_instances (id) ON DELETE CASCADE, \n\tFOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE CASCADE\n)\n\n",
    "\nCREATE TABLE payment_evidence (\n\tid UUID NOT NULL, \n\tnamespace_id UUID NOT NULL, \n\tresource_id UUID NOT NULL, \n\tprocurement_id UUID NOT NULL, \n\tkind VARCHAR(24) NOT NULL, \n\tschema_version VARCHAR(64) NOT NULL, \n\tcanonical_bytes BYTEA NOT NULL, \n\tsha256_hex VARCHAR(64) NOT NULL, \n\tkeccak256_hex VARCHAR(64) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_payment_evidence_resource UNIQUE (resource_id), \n\tCONSTRAINT fk_payment_evidence_resource_ns FOREIGN KEY(resource_id, namespace_id) REFERENCES payment_resources (id, namespace_id) ON DELETE CASCADE, \n\tCONSTRAINT fk_payment_evidence_proc_ns FOREIGN KEY(procurement_id, namespace_id) REFERENCES procurements (id, namespace_id) ON DELETE CASCADE, \n\tCONSTRAINT ck_payment_evidence_kind CHECK (kind IN ('conversion','supplier_payment')), \n\tCONSTRAINT ck_payment_evidence_hashes CHECK (char_length(sha256_hex) = 64 AND char_length(keccak256_hex) = 64), \n\tFOREIGN KEY(namespace_id) REFERENCES deployment_instances (id) ON DELETE CASCADE\n)\n\n",
    "\nCREATE TABLE payment_token_outflows (\n\tid UUID NOT NULL, \n\tnamespace_id UUID NOT NULL, \n\tdonor_wallet VARCHAR(42) NOT NULL, \n\ttx_hash VARCHAR(66) NOT NULL, \n\tlog_index INTEGER NOT NULL, \n\tblock_hash VARCHAR(66) NOT NULL, \n\tblock_number INTEGER NOT NULL, \n\tamount_atomic NUMERIC(78, 0) NOT NULL, \n\tclaim_id UUID, \n\tmatched BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_payment_outflow_identity UNIQUE (namespace_id, tx_hash, log_index, block_hash), \n\tCONSTRAINT ck_payment_outflow_amount CHECK (amount_atomic > 0 AND amount_atomic <= 115792089237316195423570985008687907853269984665640564039457584007913129639935), \n\tFOREIGN KEY(namespace_id) REFERENCES deployment_instances (id) ON DELETE CASCADE, \n\tFOREIGN KEY(claim_id) REFERENCES funded_claims (id) ON DELETE CASCADE\n)\n\n"
]

def upgrade():
    op.add_column("signing_requests", sa.Column("nonce_family", sa.String(24)))
    op.execute("UPDATE signing_requests SET nonce_family = CASE WHEN kind='ai_pre' THEN 'registry_ai' WHEN kind='receipt' THEN 'registry_recipient' ELSE 'escrow_human' END")
    op.alter_column("signing_requests", "nonce_family", nullable=False)
    op.drop_index("uq_signing_request_nonce_family", table_name="signing_requests")
    op.execute("CREATE UNIQUE INDEX uq_signing_request_nonce_family ON signing_requests (namespace_id, lower(contract_address), lower(signer_wallet), nonce_text, nonce_family) WHERE status NOT IN ('expired','invalidated_stale','invalidated_instance','invalidated_not_broadcast')")
    op.drop_constraint("ck_signing_kind", "signing_requests", type_="check")
    op.create_check_constraint("ck_signing_kind", "signing_requests", "kind IN ('ai_pre','ai_final','receipt','reserve','release','settlement')")
    op.create_check_constraint("ck_signing_family", "signing_requests", "(kind IN ('ai_pre','ai_final') AND nonce_family='registry_ai') OR (kind='receipt' AND nonce_family='registry_recipient') OR (kind IN ('reserve','release','settlement') AND nonce_family='escrow_human')")
    op.add_column("operation_steps", sa.Column("executor", sa.String(16), nullable=False, server_default="chain"))
    op.create_check_constraint("ck_step_executor", "operation_steps", "executor IN ('chain','payment')")
    op.create_index("ix_step_executor_queue", "operation_steps", ["executor", "status"])
    op.create_unique_constraint("uq_operation_id_namespace", "operations", ["id", "namespace_id"])
    op.create_unique_constraint("uq_chain_tx_id_namespace", "chain_transactions", ["id", "namespace_id"])
    for name in ("final_evidence_hash","final_assessment_id","conversion_evidence_hash","payment_evidence_hash","settlement_hash"):
        op.add_column("procurements", sa.Column(name, sa.String(66)))
    op.add_column("procurements", sa.Column("returned_amount_atomic", sa.Numeric(78,0), nullable=False, server_default="0"))
    op.create_check_constraint("ck_proc_returned_uint256", "procurements", "returned_amount_atomic >= 0 AND returned_amount_atomic <= 115792089237316195423570985008687907853269984665640564039457584007913129639935")
    op.add_column("procurements", sa.Column("source_versions_json", postgresql.JSONB(), nullable=False, server_default="{}"))
    op.drop_constraint("ck_a2_proc_chain_status", "procurements", type_="check")
    op.create_check_constraint("ck_a2_proc_chain_status", "procurements", "chain_status IN ('off_chain_draft','create_queued','created','po_queued','po_recorded','ai_pre_queued','pre_assessed','reserve_vote_queued','reserve_approval_pending','reserve_queued','reserved','invoice_queued','invoice_recorded','receipt_queued','receipt_confirmed','final_assessed','release_approval_pending','funds_released','settlement_recorded','settlement_approval_pending','payment_confirmed','cancellation_approval_pending','cancelled','ai_final_queued','release_vote_queued','release_queued','settlement_queued','settlement_vote_queued','settlement_confirmation_queued')")
    for sql in PAYMENT_DDL:
        op.execute(sql)
    op.execute("""
    CREATE FUNCTION pog_full_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'immutable simulation history'; END $$;
    """)
    for table in ("sim_hkd_journals", "payment_evidence"):
        op.execute(f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION pog_full_immutable()")
    op.execute("""
    CREATE FUNCTION pog_payment_scope_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF (to_jsonb(NEW) - ARRAY['status','allocated_atomic','chain_proof','journal_ids','error_code','updated_at'])
          IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status','allocated_atomic','chain_proof','journal_ids','error_code','updated_at']) THEN
        RAISE EXCEPTION 'payment original scope is immutable';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER immutable_payment_scope BEFORE UPDATE ON payment_resources
      FOR EACH ROW EXECUTE FUNCTION pog_payment_scope_immutable();
    CREATE FUNCTION pog_funded_claim_scope_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF (to_jsonb(NEW) - ARRAY['status','chain_proof'])
          IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status','chain_proof']) THEN
        RAISE EXCEPTION 'funded original scope is immutable';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER immutable_funded_claim_scope BEFORE UPDATE ON funded_claims
      FOR EACH ROW EXECUTE FUNCTION pog_funded_claim_scope_immutable();
    """)
    op.execute("""
    CREATE FUNCTION pog_source_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NOT NEW.source_versions_json @> OLD.source_versions_json THEN
        RAISE EXCEPTION 'original source bindings are append-only';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER immutable_proc_sources BEFORE UPDATE OF source_versions_json ON procurements
      FOR EACH ROW EXECUTE FUNCTION pog_source_append_only();
    """)

def downgrade():
    raise RuntimeError("Full simulation migration is forward-only; restore a verified backup")
