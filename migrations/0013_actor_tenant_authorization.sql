-- OPEN-025 ActorTenantAuthorization current projection and append-only versions.

CREATE TABLE IF NOT EXISTS actor_tenant_authorization_versions (
    tenant_id text NOT NULL,
    record_id text NOT NULL,
    authorization_version integer NOT NULL,
    actor_id text NOT NULL,
    status text NOT NULL,
    payload jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, record_id, authorization_version),
    CHECK ((payload->>'tenantId') IS NOT DISTINCT FROM tenant_id),
    CHECK ((payload->>'recordId') IS NOT DISTINCT FROM record_id),
    CHECK ((payload->>'actorId') IS NOT DISTINCT FROM actor_id),
    CHECK ((payload->>'authorizationVersion')::integer IS NOT DISTINCT FROM authorization_version)
);

CREATE TABLE IF NOT EXISTS actor_tenant_authorizations_current (
    tenant_id text NOT NULL,
    record_id text NOT NULL,
    authorization_version integer NOT NULL,
    actor_id text NOT NULL,
    status text NOT NULL,
    payload jsonb NOT NULL,
    effective_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, record_id)
);

CREATE OR REPLACE FUNCTION reject_actor_authorization_history_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'append-only';
END;
$$;

DROP TRIGGER IF EXISTS actor_tenant_authorization_versions_append_only
ON actor_tenant_authorization_versions;
CREATE TRIGGER actor_tenant_authorization_versions_append_only
BEFORE UPDATE OR DELETE ON actor_tenant_authorization_versions
FOR EACH ROW EXECUTE FUNCTION reject_actor_authorization_history_mutation();

ALTER TABLE actor_tenant_authorization_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE actor_tenant_authorization_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE actor_tenant_authorizations_current ENABLE ROW LEVEL SECURITY;
ALTER TABLE actor_tenant_authorizations_current FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS actor_tenant_authorization_versions_policy
ON actor_tenant_authorization_versions;
CREATE POLICY actor_tenant_authorization_versions_policy
ON actor_tenant_authorization_versions
USING (
    tenant_id = current_setting('app.tenant_id', true)
    OR actor_id = current_setting('app.actor_id', true)
)
WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS actor_tenant_authorizations_current_policy
ON actor_tenant_authorizations_current;
CREATE POLICY actor_tenant_authorizations_current_policy
ON actor_tenant_authorizations_current
USING (
    tenant_id = current_setting('app.tenant_id', true)
    OR actor_id = current_setting('app.actor_id', true)
)
WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
