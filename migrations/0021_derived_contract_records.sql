-- Durable, inactive storage for the qualification-readiness and availability-booking families.
-- This migration does not activate an application writer or any provider effect.

CREATE TABLE IF NOT EXISTS derived_contract_records (
    tenant_id text NOT NULL,
    contract_family text NOT NULL,
    message_type text NOT NULL,
    record_id text NOT NULL,
    record_version integer NOT NULL,
    schema_version text NOT NULL,
    payload jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, contract_family, message_type, record_id, record_version),
    CHECK (
        (
            contract_family = 'qualification_readiness'
            AND schema_version = 'qualification-readiness/1.0.0'
            AND message_type IN (
                'qualification_policy',
                'qualification_input_set',
                'next_question_decision',
                'readiness_decision'
            )
        ) OR (
            contract_family = 'availability_booking'
            AND schema_version = 'availability-booking/1.0.0'
            AND message_type IN (
                'calendar_provider_binding',
                'availability_policy',
                'calendar_snapshot',
                'slot_set',
                'booking_command',
                'booking_result',
                'booking_reconciliation'
            )
        )
    ),
    CHECK ((payload->>'tenantId') IS NOT DISTINCT FROM tenant_id),
    CHECK ((payload->>'messageType') IS NOT DISTINCT FROM message_type),
    CHECK ((payload->>'schemaVersion') IS NOT DISTINCT FROM schema_version),
    CHECK (
        record_id IS NOT DISTINCT FROM CASE message_type
            WHEN 'qualification_policy' THEN payload->>'policyId'
            WHEN 'qualification_input_set' THEN payload->>'inputSetId'
            WHEN 'next_question_decision' THEN payload->>'decisionId'
            WHEN 'readiness_decision' THEN payload->>'decisionId'
            WHEN 'calendar_provider_binding' THEN payload->>'bindingId'
            WHEN 'availability_policy' THEN payload->>'policyId'
            WHEN 'calendar_snapshot' THEN payload->>'snapshotId'
            WHEN 'slot_set' THEN payload->>'slotSetId'
            WHEN 'booking_command' THEN payload->>'commandId'
            WHEN 'booking_result' THEN payload->>'resultId'
            WHEN 'booking_reconciliation' THEN payload->>'reconciliationId'
        END
    ),
    CHECK (
        CASE message_type
            WHEN 'qualification_policy'
                THEN record_version = (payload->>'version')::integer
            WHEN 'calendar_provider_binding'
                THEN record_version = (payload->>'version')::integer
            WHEN 'availability_policy'
                THEN record_version = (payload->>'version')::integer
            ELSE record_version = 1 AND NOT (payload ? 'version')
        END
    )
);

ALTER TABLE derived_contract_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE derived_contract_records FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS derived_contract_records_tenant_policy ON derived_contract_records;
CREATE POLICY derived_contract_records_tenant_policy ON derived_contract_records
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP TRIGGER IF EXISTS derived_contract_records_append_only ON derived_contract_records;
CREATE TRIGGER derived_contract_records_append_only
BEFORE UPDATE OR DELETE ON derived_contract_records
FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
