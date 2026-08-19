-- OPEN-019–024 runtime enforcement and control-plane persistence.
-- Every statement is restart-safe; semantic rollback is refusal-gated.

ALTER TABLE inbound_events ADD COLUMN IF NOT EXISTS connector_id text;
ALTER TABLE inbound_events ADD COLUMN IF NOT EXISTS external_message_id text;
ALTER TABLE inbound_events ADD COLUMN IF NOT EXISTS external_event_id text;
CREATE UNIQUE INDEX IF NOT EXISTS inbound_events_stable_message_key
    ON inbound_events (tenant_id, connector_id, provider_account_ref, external_message_id)
    WHERE connector_id IS NOT NULL AND external_message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS inbound_message_conflicts (
    tenant_id text NOT NULL,
    conflict_id text NOT NULL,
    connector_id text NOT NULL,
    provider_account_ref text NOT NULL,
    external_message_id text NOT NULL,
    original_event_id text NOT NULL,
    conflicting_event_id text NOT NULL,
    original_payload_digest text NOT NULL,
    conflicting_payload_digest text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, conflict_id)
);

CREATE TABLE IF NOT EXISTS closure_records (
    tenant_id text NOT NULL,
    record_id text NOT NULL,
    record_version integer NOT NULL,
    record_type text NOT NULL,
    identity_key text NOT NULL,
    status text NOT NULL,
    payload jsonb NOT NULL,
    observed_at timestamptz NOT NULL,
    effective_from timestamptz NOT NULL,
    effective_to timestamptz,
    expires_at timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, record_id, record_version),
    CHECK ((payload->>'tenantId') IS NOT DISTINCT FROM tenant_id),
    CHECK ((payload->>'recordId') IS NOT DISTINCT FROM record_id),
    CHECK ((payload->>'recordVersion')::integer IS NOT DISTINCT FROM record_version),
    CHECK ((payload->>'recordType') IS NOT DISTINCT FROM record_type)
);

ALTER TABLE closure_records ADD COLUMN IF NOT EXISTS record_version integer NOT NULL DEFAULT 1;
ALTER TABLE closure_records ADD COLUMN IF NOT EXISTS identity_key text;
ALTER TABLE closure_records ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'current';
ALTER TABLE closure_records ADD COLUMN IF NOT EXISTS effective_from timestamptz;
ALTER TABLE closure_records ADD COLUMN IF NOT EXISTS effective_to timestamptz;
UPDATE closure_records SET identity_key = record_id WHERE identity_key IS NULL;
UPDATE closure_records SET effective_from = observed_at WHERE effective_from IS NULL;
ALTER TABLE closure_records ALTER COLUMN identity_key SET NOT NULL;
ALTER TABLE closure_records ALTER COLUMN effective_from SET NOT NULL;
DO $$
DECLARE
    key_columns text[];
BEGIN
    SELECT array_agg(att.attname ORDER BY ord.ordinality)
    INTO key_columns
    FROM pg_constraint con
    JOIN unnest(con.conkey) WITH ORDINALITY AS ord(attnum, ordinality) ON true
    JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ord.attnum
    WHERE con.conrelid = 'closure_records'::regclass AND con.contype = 'p';
    IF key_columns IS DISTINCT FROM ARRAY['tenant_id', 'record_id', 'record_version'] THEN
        ALTER TABLE closure_records DROP CONSTRAINT closure_records_pkey;
        ALTER TABLE closure_records ADD PRIMARY KEY (tenant_id, record_id, record_version);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS closure_records_current (
    tenant_id text NOT NULL,
    record_type text NOT NULL,
    identity_key text NOT NULL,
    record_id text NOT NULL,
    record_version integer NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (tenant_id, record_type, identity_key),
    FOREIGN KEY (tenant_id, record_id, record_version)
        REFERENCES closure_records (tenant_id, record_id, record_version)
);

CREATE TABLE IF NOT EXISTS telemetry_observations (
    tenant_id text NOT NULL,
    observation_id text NOT NULL,
    metric_id text NOT NULL,
    payload jsonb NOT NULL,
    observed_at timestamptz NOT NULL,
    source_digest text NOT NULL,
    PRIMARY KEY (tenant_id, observation_id)
);

CREATE TABLE IF NOT EXISTS release_gate_evidence (
    tenant_id text NOT NULL,
    evidence_id text NOT NULL,
    gate_id text NOT NULL,
    payload jsonb NOT NULL,
    artifact_digest text NOT NULL,
    observed_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS release_activation_decisions (
    tenant_id text NOT NULL,
    decision_id text NOT NULL,
    capability_id text NOT NULL,
    payload jsonb NOT NULL,
    decided_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, decision_id)
);

CREATE TABLE IF NOT EXISTS ingress_attribution (
    tenant_id text NOT NULL,
    attribution_id text NOT NULL,
    payload_digest text NOT NULL,
    payload jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, attribution_id)
);

CREATE TABLE IF NOT EXISTS ingress_consent_presentation (
    tenant_id text NOT NULL,
    evidence_id text NOT NULL,
    payload_digest text NOT NULL,
    payload jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS operator_actor_tenancies (
    tenant_id text NOT NULL,
    actor_id text NOT NULL,
    state text NOT NULL CHECK (state IN ('active', 'revoked')),
    PRIMARY KEY (tenant_id, actor_id)
);

ALTER TABLE inbound_message_conflicts ENABLE ROW LEVEL SECURITY;
ALTER TABLE inbound_message_conflicts FORCE ROW LEVEL SECURITY;
ALTER TABLE closure_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE closure_records FORCE ROW LEVEL SECURITY;
ALTER TABLE closure_records_current ENABLE ROW LEVEL SECURITY;
ALTER TABLE closure_records_current FORCE ROW LEVEL SECURITY;
ALTER TABLE telemetry_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE telemetry_observations FORCE ROW LEVEL SECURITY;
ALTER TABLE release_gate_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE release_gate_evidence FORCE ROW LEVEL SECURITY;
ALTER TABLE release_activation_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE release_activation_decisions FORCE ROW LEVEL SECURITY;
ALTER TABLE ingress_attribution ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingress_attribution FORCE ROW LEVEL SECURITY;
ALTER TABLE ingress_consent_presentation ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingress_consent_presentation FORCE ROW LEVEL SECURITY;
ALTER TABLE operator_actor_tenancies ENABLE ROW LEVEL SECURITY;
ALTER TABLE operator_actor_tenancies FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS inbound_message_conflicts_tenant_policy ON inbound_message_conflicts;
CREATE POLICY inbound_message_conflicts_tenant_policy ON inbound_message_conflicts
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
DROP POLICY IF EXISTS closure_records_tenant_policy ON closure_records;
CREATE POLICY closure_records_tenant_policy ON closure_records
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
DROP POLICY IF EXISTS closure_records_current_tenant_policy ON closure_records_current;
CREATE POLICY closure_records_current_tenant_policy ON closure_records_current
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
DROP POLICY IF EXISTS telemetry_observations_tenant_policy ON telemetry_observations;
CREATE POLICY telemetry_observations_tenant_policy ON telemetry_observations
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
DROP POLICY IF EXISTS release_gate_evidence_tenant_policy ON release_gate_evidence;
CREATE POLICY release_gate_evidence_tenant_policy ON release_gate_evidence
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
DROP POLICY IF EXISTS release_activation_decisions_tenant_policy ON release_activation_decisions;
CREATE POLICY release_activation_decisions_tenant_policy ON release_activation_decisions
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
DROP POLICY IF EXISTS ingress_attribution_tenant_policy ON ingress_attribution;
CREATE POLICY ingress_attribution_tenant_policy ON ingress_attribution
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
DROP POLICY IF EXISTS ingress_consent_presentation_tenant_policy ON ingress_consent_presentation;
CREATE POLICY ingress_consent_presentation_tenant_policy ON ingress_consent_presentation
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
DROP POLICY IF EXISTS operator_actor_tenancies_tenant_policy ON operator_actor_tenancies;
CREATE POLICY operator_actor_tenancies_tenant_policy ON operator_actor_tenancies
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP TRIGGER IF EXISTS inbound_message_conflicts_append_only ON inbound_message_conflicts;
CREATE TRIGGER inbound_message_conflicts_append_only BEFORE UPDATE OR DELETE
ON inbound_message_conflicts FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
DROP TRIGGER IF EXISTS closure_records_append_only ON closure_records;
CREATE TRIGGER closure_records_append_only BEFORE UPDATE OR DELETE
ON closure_records FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
DROP TRIGGER IF EXISTS telemetry_observations_append_only ON telemetry_observations;
CREATE TRIGGER telemetry_observations_append_only BEFORE UPDATE OR DELETE
ON telemetry_observations FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
DROP TRIGGER IF EXISTS release_gate_evidence_append_only ON release_gate_evidence;
CREATE TRIGGER release_gate_evidence_append_only BEFORE UPDATE OR DELETE
ON release_gate_evidence FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
DROP TRIGGER IF EXISTS release_activation_decisions_append_only ON release_activation_decisions;
CREATE TRIGGER release_activation_decisions_append_only BEFORE UPDATE OR DELETE
ON release_activation_decisions FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
