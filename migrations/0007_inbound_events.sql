-- PKT-05: authenticated, tenant-scoped inbound event idempotency registry.

CREATE TABLE inbound_events (
    tenant_id text NOT NULL,
    inbound_event_id text NOT NULL,
    provider_account_ref text NOT NULL,
    provider_event_id text NOT NULL,
    channel text NOT NULL CHECK (channel IN ('form', 'email', 'sms')),
    received_at timestamptz NOT NULL,
    payload_artifact_id text NOT NULL,
    payload_digest text NOT NULL CHECK (payload_digest ~ '^[a-zA-Z0-9_-]+:[0-9a-fA-F]{32,}$'),
    envelope jsonb NOT NULL,
    admitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, inbound_event_id),
    UNIQUE (tenant_id, provider_account_ref, provider_event_id),
    CHECK ((envelope->>'providerEventId') IS NOT DISTINCT FROM provider_event_id),
    CHECK ((envelope->>'providerAccountRef') IS NOT DISTINCT FROM provider_account_ref),
    CHECK ((envelope->>'payloadArtifactId') IS NOT DISTINCT FROM payload_artifact_id),
    CHECK ((envelope->>'payloadDigest') IS NOT DISTINCT FROM payload_digest)
);

ALTER TABLE inbound_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE inbound_events FORCE ROW LEVEL SECURITY;
CREATE POLICY inbound_events_tenant_policy ON inbound_events
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE TRIGGER inbound_events_append_only BEFORE UPDATE OR DELETE
ON inbound_events FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
