-- OPEN-027: versioned acknowledgment configuration and append-only decisions/outcomes.
CREATE TABLE ingress_ack_config_versions (
    tenant_id text NOT NULL, config_type text NOT NULL,
    config_id text NOT NULL, record_version integer NOT NULL CHECK (record_version > 0),
    config jsonb NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, config_type, config_id, record_version),
    CHECK (config_type IN ('opt_out_lexicon', 'acknowledgment_policy')),
    CHECK ((config->>'tenantId') IS NOT DISTINCT FROM tenant_id),
    CHECK ((config->>'messageType') IS NOT DISTINCT FROM config_type),
    CHECK ((config->>'recordVersion')::integer IS NOT DISTINCT FROM record_version),
    CHECK (config->>'schemaVersion' = 'ot01-ingress/1.1.0')
);
CREATE TABLE ingress_ack_configs_current (
    tenant_id text NOT NULL, config_type text NOT NULL,
    config_id text NOT NULL, record_version integer NOT NULL CHECK (record_version > 0),
    status text NOT NULL, config jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, config_type, config_id),
    FOREIGN KEY (tenant_id, config_type, config_id, record_version)
        REFERENCES ingress_ack_config_versions (tenant_id, config_type, config_id, record_version)
);
CREATE TABLE ingress_acknowledgment_decisions (
    tenant_id text NOT NULL, decision_id text NOT NULL, request_id text NOT NULL,
    idempotency_key text NOT NULL, request_digest text NOT NULL,
    decision jsonb NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, decision_id), UNIQUE (tenant_id, request_id),
    UNIQUE (tenant_id, idempotency_key),
    CHECK ((decision->>'tenantId') IS NOT DISTINCT FROM tenant_id),
    CHECK ((decision->>'decisionId') IS NOT DISTINCT FROM decision_id),
    CHECK (decision->>'schemaVersion' = 'ot01-ingress/1.1.0')
);
CREATE TABLE ingress_acknowledgment_outcomes (
    tenant_id text NOT NULL, outcome_id text NOT NULL, decision_id text NOT NULL,
    outcome jsonb NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, outcome_id),
    FOREIGN KEY (tenant_id, decision_id) REFERENCES ingress_acknowledgment_decisions (tenant_id, decision_id),
    CHECK ((outcome->>'tenantId') IS NOT DISTINCT FROM tenant_id),
    CHECK ((outcome->>'outcomeId') IS NOT DISTINCT FROM outcome_id)
);
CREATE UNIQUE INDEX ingress_acknowledgment_outcome_event_uidx
    ON ingress_acknowledgment_outcomes (tenant_id, (outcome->>'outcomeEventId'));

ALTER TABLE ingress_ack_config_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingress_ack_config_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY ingress_ack_config_versions_tenant_policy ON ingress_ack_config_versions
    USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
ALTER TABLE ingress_ack_configs_current ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingress_ack_configs_current FORCE ROW LEVEL SECURITY;
CREATE POLICY ingress_ack_configs_current_tenant_policy ON ingress_ack_configs_current
    USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
ALTER TABLE ingress_acknowledgment_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingress_acknowledgment_decisions FORCE ROW LEVEL SECURITY;
CREATE POLICY ingress_acknowledgment_decisions_tenant_policy ON ingress_acknowledgment_decisions
    USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
ALTER TABLE ingress_acknowledgment_outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingress_acknowledgment_outcomes FORCE ROW LEVEL SECURITY;
CREATE POLICY ingress_acknowledgment_outcomes_tenant_policy ON ingress_acknowledgment_outcomes
    USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE TRIGGER ingress_ack_config_versions_append_only BEFORE UPDATE OR DELETE ON ingress_ack_config_versions FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
CREATE TRIGGER ingress_acknowledgment_decisions_append_only BEFORE UPDATE OR DELETE ON ingress_acknowledgment_decisions FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
CREATE TRIGGER ingress_acknowledgment_outcomes_append_only BEFORE UPDATE OR DELETE ON ingress_acknowledgment_outcomes FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
