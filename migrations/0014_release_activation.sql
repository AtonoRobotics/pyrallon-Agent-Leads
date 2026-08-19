-- OPEN-026 exact signed-payload history. Selection remains explicit by record_id.

CREATE TABLE IF NOT EXISTS release_activation_versions (
    tenant_id text NOT NULL,
    record_id text NOT NULL,
    environment text NOT NULL,
    release_id text NOT NULL,
    status text NOT NULL,
    payload jsonb NOT NULL,
    observed_at timestamptz NOT NULL,
    effective_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, record_id),
    CHECK ((payload->>'tenantId') IS NOT DISTINCT FROM tenant_id),
    CHECK ((payload->>'recordId') IS NOT DISTINCT FROM record_id),
    CHECK ((payload->>'environment') IS NOT DISTINCT FROM environment),
    CHECK ((payload->>'releaseId') IS NOT DISTINCT FROM release_id),
    CHECK ((payload->>'status') IS NOT DISTINCT FROM status)
);

ALTER TABLE release_activation_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE release_activation_versions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS release_activation_versions_tenant_policy
ON release_activation_versions;
CREATE POLICY release_activation_versions_tenant_policy
ON release_activation_versions
USING (tenant_id = current_setting('app.tenant_id', true))
WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP TRIGGER IF EXISTS release_activation_versions_append_only
ON release_activation_versions;
CREATE TRIGGER release_activation_versions_append_only
BEFORE UPDATE OR DELETE ON release_activation_versions
FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
