-- Durable operator workflow signals. PostgreSQL remains canonical; Temporal is a signal target only.

CREATE TABLE operator_workflow_outbox (
    tenant_id text NOT NULL,
    outbox_id text NOT NULL,
    command_id text NOT NULL,
    idempotency_key text NOT NULL,
    workflow_id text NOT NULL,
    run_id text NOT NULL,
    signal_name text NOT NULL CHECK (signal_name IN ('pause', 'resume', 'canonical_changed')),
    signal_id text NOT NULL,
    signal_payload jsonb NOT NULL,
    state text NOT NULL CHECK (state IN ('pending', 'dispatching', 'dispatched', 'failed')),
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    failure_code text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    dispatching_at timestamptz,
    dispatched_at timestamptz,
    PRIMARY KEY (tenant_id, outbox_id),
    UNIQUE (tenant_id, command_id),
    UNIQUE (tenant_id, signal_id)
);

CREATE TABLE operator_workflow_signal_receipts (
    tenant_id text NOT NULL,
    receipt_id text NOT NULL,
    outbox_id text NOT NULL,
    command_id text NOT NULL,
    workflow_id text NOT NULL,
    run_id text NOT NULL,
    signal_name text NOT NULL CHECK (signal_name IN ('pause', 'resume', 'canonical_changed')),
    attempt integer NOT NULL CHECK (attempt >= 1),
    state text NOT NULL CHECK (state IN ('delivered', 'failed')),
    provider_receipt_id text,
    failure_code text,
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, receipt_id),
    UNIQUE (tenant_id, outbox_id, attempt),
    FOREIGN KEY (tenant_id, outbox_id)
        REFERENCES operator_workflow_outbox (tenant_id, outbox_id)
);

ALTER TABLE operator_workflow_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE operator_workflow_outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY operator_workflow_outbox_tenant_policy ON operator_workflow_outbox
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

ALTER TABLE operator_workflow_signal_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE operator_workflow_signal_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY operator_workflow_signal_receipts_tenant_policy ON operator_workflow_signal_receipts
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE TRIGGER operator_workflow_signal_receipts_append_only BEFORE UPDATE OR DELETE
ON operator_workflow_signal_receipts FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
