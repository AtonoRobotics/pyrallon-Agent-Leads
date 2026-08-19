-- OPEN-028: versioned operator policy and atomic command admission support.

-- Forward-repair the derived actor-tenancy projection from canonical Authorization truth.
ALTER TABLE operator_actor_tenancies ADD COLUMN authorization_id text;
DELETE FROM operator_actor_tenancies;
ALTER TABLE operator_actor_tenancies DROP CONSTRAINT operator_actor_tenancies_pkey;
ALTER TABLE operator_actor_tenancies DROP COLUMN state;
ALTER TABLE operator_actor_tenancies ALTER COLUMN authorization_id SET NOT NULL;
ALTER TABLE operator_actor_tenancies
    ADD PRIMARY KEY (actor_id, tenant_id, authorization_id);
INSERT INTO operator_actor_tenancies (actor_id, tenant_id, authorization_id)
SELECT record->>'granteeId', tenant_id, record_id
FROM canonical_records_current
WHERE record_type = 'Authorization'
  AND record->>'status' = 'active'
  AND record->>'authorizationState' = 'active';

CREATE TABLE operator_policy_versions (
    tenant_id text NOT NULL,
    policy_id text NOT NULL,
    record_version integer NOT NULL CHECK (record_version > 0),
    policy jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, policy_id, record_version),
    CHECK ((policy->>'tenant_id') IS NOT DISTINCT FROM tenant_id),
    CHECK ((policy->>'policy_id') IS NOT DISTINCT FROM policy_id),
    CHECK ((policy->>'record_version')::integer IS NOT DISTINCT FROM record_version),
    CHECK (policy->>'message_type' = 'operator_policy'),
    CHECK (policy->>'schema_version' = 'operator-surface/1.1.0')
);

CREATE TABLE operator_policies_current (
    tenant_id text NOT NULL,
    policy_id text NOT NULL,
    record_version integer NOT NULL CHECK (record_version > 0),
    status text NOT NULL,
    policy jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, policy_id),
    FOREIGN KEY (tenant_id, policy_id, record_version)
        REFERENCES operator_policy_versions (tenant_id, policy_id, record_version),
    CHECK ((policy->>'tenant_id') IS NOT DISTINCT FROM tenant_id),
    CHECK ((policy->>'policy_id') IS NOT DISTINCT FROM policy_id),
    CHECK ((policy->>'record_version')::integer IS NOT DISTINCT FROM record_version),
    CHECK ((policy->>'status') IS NOT DISTINCT FROM status)
);

ALTER TABLE operator_policy_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE operator_policy_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY operator_policy_versions_tenant_policy ON operator_policy_versions
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

ALTER TABLE operator_policies_current ENABLE ROW LEVEL SECURITY;
ALTER TABLE operator_policies_current FORCE ROW LEVEL SECURITY;
CREATE POLICY operator_policies_current_tenant_policy ON operator_policies_current
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE TRIGGER operator_policy_versions_append_only BEFORE UPDATE OR DELETE
ON operator_policy_versions FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
