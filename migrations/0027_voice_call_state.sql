-- FR-26: durable inbound-only voice lifecycle and recording-consent state.
-- Voice participation and recording permission are separate state machines.

CREATE TABLE voice_call_events (
    tenant_id text NOT NULL,
    event_id text NOT NULL,
    call_sid text NOT NULL,
    provider_account_ref text NOT NULL,
    event_type text NOT NULL,
    payload jsonb NOT NULL,
    observed_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, event_id)
);

CREATE INDEX voice_call_events_call_idx
    ON voice_call_events (tenant_id, call_sid, observed_at, event_id);

ALTER TABLE voice_call_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE voice_call_events FORCE ROW LEVEL SECURITY;

CREATE POLICY voice_call_events_tenant_policy ON voice_call_events
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE TRIGGER voice_call_events_append_only BEFORE UPDATE OR DELETE
ON voice_call_events FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation();

CREATE TABLE voice_call_current (
    tenant_id text NOT NULL,
    call_sid text NOT NULL,
    provider_account_ref text NOT NULL,
    from_number text NOT NULL,
    to_number text NOT NULL,
    lifecycle_state text NOT NULL CHECK (lifecycle_state IN
        ('received', 'connected', 'completed', 'failed', 'transferred')),
    ai_disclosure_state text NOT NULL CHECK (ai_disclosure_state IN
        ('pending', 'delivered')) DEFAULT 'pending',
    recording_state text NOT NULL CHECK (recording_state IN
        ('not_requested', 'refused', 'consented', 'revoked')) DEFAULT 'not_requested',
    recording_consent_evidence_id text,
    recording_consent_at timestamptz,
    recording_refusal_at timestamptz,
    recording_revoked_at timestamptz,
    last_event_id text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, call_sid)
);

ALTER TABLE voice_call_current ENABLE ROW LEVEL SECURITY;
ALTER TABLE voice_call_current FORCE ROW LEVEL SECURITY;

CREATE POLICY voice_call_current_tenant_policy ON voice_call_current
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

COMMENT ON TABLE voice_call_events IS
    'Append-only authenticated inbound voice events and recording evidence.';
COMMENT ON TABLE voice_call_current IS
    'Current tenant-scoped inbound voice state; recording permission is independent of participation.';
