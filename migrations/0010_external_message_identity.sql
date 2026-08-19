-- OPEN-019: durable inbound identity is tenant + connector + account + external message ID.
-- provider_event_id remains a trace key and is not the message identity.

ALTER TABLE inbound_events
    ADD COLUMN IF NOT EXISTS connector_id text,
    ADD COLUMN IF NOT EXISTS external_message_id text,
    ADD COLUMN IF NOT EXISTS external_event_id text;

UPDATE inbound_events
SET
    connector_id = coalesce(connector_id, provider_account_ref),
    external_message_id = coalesce(external_message_id, provider_event_id),
    external_event_id = coalesce(external_event_id, provider_event_id)
WHERE connector_id IS NULL OR external_message_id IS NULL;

ALTER TABLE inbound_events
    ALTER COLUMN connector_id SET NOT NULL,
    ALTER COLUMN external_message_id SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS inbound_events_external_message_uidx
    ON inbound_events (tenant_id, connector_id, provider_account_ref, external_message_id);

ALTER TABLE inbound_events
    DROP CONSTRAINT IF EXISTS inbound_events_tenant_id_provider_account_ref_provider_event_id_key;

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

ALTER TABLE inbound_message_conflicts ENABLE ROW LEVEL SECURITY;
ALTER TABLE inbound_message_conflicts FORCE ROW LEVEL SECURITY;
CREATE POLICY inbound_message_conflicts_tenant_policy ON inbound_message_conflicts
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
