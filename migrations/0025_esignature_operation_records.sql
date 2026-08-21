-- Durable append-only provider operation state for governed e-signature effects.
CREATE TABLE IF NOT EXISTS esignature_operation_records (
    tenant_id text NOT NULL,
    operation_id text NOT NULL,
    agreement_id text NOT NULL,
    provider_envelope_id text,
    state text NOT NULL CHECK (state IN ('presented', 'pending', 'completed', 'failed')),
    provider_status text,
    payload jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, operation_id),
    CHECK ((payload->>'tenantId') IS NOT DISTINCT FROM tenant_id),
    CHECK ((payload->>'agreementId') IS NOT DISTINCT FROM agreement_id)
);

ALTER TABLE esignature_operation_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE esignature_operation_records FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS esignature_operation_records_tenant_policy ON esignature_operation_records;
CREATE POLICY esignature_operation_records_tenant_policy ON esignature_operation_records
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP TRIGGER IF EXISTS esignature_operation_records_append_only ON esignature_operation_records;
CREATE TRIGGER esignature_operation_records_append_only
BEFORE UPDATE OR DELETE ON esignature_operation_records
FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
