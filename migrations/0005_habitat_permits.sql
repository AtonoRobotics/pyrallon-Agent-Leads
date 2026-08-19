-- PKT-03: transaction-bound authority decisions and single-use effect permits.

CREATE TABLE habitat_authority_decisions (
    tenant_id text NOT NULL,
    decision_id text NOT NULL,
    intent_id text NOT NULL,
    trace_id text NOT NULL,
    ordering_token bigint NOT NULL,
    decided_at timestamptz NOT NULL,
    decision text NOT NULL,
    reason text NOT NULL,
    policy_id text,
    policy_version text,
    intent jsonb NOT NULL,
    authoritative_state jsonb NOT NULL,
    authoritative_versions jsonb NOT NULL,
    permit_digest text,
    PRIMARY KEY (tenant_id, decision_id)
);

CREATE TABLE habitat_effect_permits (
    tenant_id text NOT NULL,
    permit_digest text NOT NULL,
    intent_id text NOT NULL,
    principal_id text NOT NULL,
    action_class text NOT NULL,
    connector_binding_id text NOT NULL,
    target_resource_type text NOT NULL,
    target_resource_id text NOT NULL,
    recipient_type text NOT NULL,
    recipient_id text NOT NULL,
    payload_digest text NOT NULL,
    idempotency_key text NOT NULL,
    canonical_version_vector jsonb NOT NULL,
    issued_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK (expires_at > issued_at),
    redeemed_at timestamptz,
    state text NOT NULL CHECK (state IN ('issued', 'redeemed', 'revoked', 'expired')),
    PRIMARY KEY (tenant_id, permit_digest),
    UNIQUE (tenant_id, intent_id),
    UNIQUE (tenant_id, idempotency_key),
    CHECK ((state = 'redeemed') = (redeemed_at IS NOT NULL))
);

CREATE INDEX habitat_authority_decisions_intent_idx
    ON habitat_authority_decisions (tenant_id, intent_id, decided_at);

ALTER TABLE habitat_authority_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE habitat_authority_decisions FORCE ROW LEVEL SECURITY;
ALTER TABLE habitat_effect_permits ENABLE ROW LEVEL SECURITY;
ALTER TABLE habitat_effect_permits FORCE ROW LEVEL SECURITY;

CREATE POLICY habitat_authority_decisions_tenant_policy
    ON habitat_authority_decisions
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY habitat_effect_permits_tenant_policy
    ON habitat_effect_permits
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE TRIGGER habitat_authority_decisions_append_only
BEFORE UPDATE OR DELETE ON habitat_authority_decisions
FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();

COMMENT ON TABLE habitat_authority_decisions IS
    'Append-only DW2-C1 evidence captured under the same ordering transaction as permit redemption.';
COMMENT ON TABLE habitat_effect_permits IS
    'No bearer token is stored; only a keyed digest and exact effect bindings are durable.';
