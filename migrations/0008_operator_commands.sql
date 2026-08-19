-- PKT-10: immutable tenant-scoped operator command idempotency results.

CREATE TABLE operator_command_results (
    tenant_id text NOT NULL,
    idempotency_key text NOT NULL,
    payload_digest text NOT NULL CHECK (payload_digest ~ '^[a-zA-Z0-9_-]+:[0-9a-fA-F]{32,}$'),
    command_id text NOT NULL,
    result jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, idempotency_key),
    UNIQUE (tenant_id, command_id),
    CHECK ((result->>'tenant_id') IS NOT DISTINCT FROM tenant_id),
    CHECK ((result->>'command_id') IS NOT DISTINCT FROM command_id)
);

ALTER TABLE operator_command_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE operator_command_results FORCE ROW LEVEL SECURITY;
CREATE POLICY operator_command_results_tenant_policy ON operator_command_results
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE TRIGGER operator_command_results_append_only BEFORE UPDATE OR DELETE
ON operator_command_results FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
