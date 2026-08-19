-- PKT-02: content-minimized artifact metadata and tamper-evident evidence.

CREATE TABLE evidence_artifact_versions (
    tenant_id text NOT NULL,
    artifact_id text NOT NULL,
    version bigint NOT NULL CHECK (version >= 1),
    encrypted_object_ref text,
    encryption_key_ref text NOT NULL,
    artifact_digest text NOT NULL CHECK (artifact_digest ~ '^sha256:[0-9a-f]{64}$'),
    provenance jsonb NOT NULL,
    classification text NOT NULL,
    retention_class text NOT NULL,
    purpose text NOT NULL,
    captured_at timestamptz NOT NULL,
    retain_until timestamptz,
    object_lock_until timestamptz,
    provider_legal_hold boolean NOT NULL DEFAULT false,
    artifact_state text NOT NULL CHECK (artifact_state IN ('active', 'deleted', 'anonymized')),
    tombstone_id text,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, artifact_id, version),
    CHECK (
        (artifact_state = 'active' AND encrypted_object_ref IS NOT NULL AND tombstone_id IS NULL)
        OR
        (artifact_state IN ('deleted', 'anonymized') AND encrypted_object_ref IS NULL
            AND tombstone_id IS NOT NULL)
    )
);

CREATE TABLE evidence_ledger (
    tenant_id text NOT NULL,
    sequence bigint NOT NULL CHECK (sequence >= 1),
    event_id text NOT NULL,
    event_type text NOT NULL CHECK (event_type IN (
        'material_observation', 'context_manifest', 'authority_decision', 'approval',
        'outbound_communication', 'inbound_communication', 'canonical_mutation',
        'external_effect_request', 'provider_receipt', 'workflow_transition',
        'correction', 'deletion'
    )),
    occurred_at timestamptz NOT NULL,
    captured_at timestamptz NOT NULL,
    classification text NOT NULL,
    retention_class text NOT NULL,
    purpose text NOT NULL,
    payload_digest text NOT NULL CHECK (payload_digest ~ '^sha256:[0-9a-f]{64}$'),
    provenance_refs jsonb NOT NULL CHECK (jsonb_array_length(provenance_refs) >= 1),
    artifact_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    canonical_record_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    workflow_id text,
    effect_attempt_id text,
    prior_hash text NOT NULL CHECK (prior_hash ~ '^sha256:[0-9a-f]{64}$'),
    entry_hash text NOT NULL CHECK (entry_hash ~ '^sha256:[0-9a-f]{64}$'),
    PRIMARY KEY (tenant_id, sequence),
    UNIQUE (tenant_id, event_id),
    UNIQUE (tenant_id, entry_hash),
    CHECK (jsonb_typeof(provenance_refs) = 'array'),
    CHECK (jsonb_typeof(artifact_ids) = 'array'),
    CHECK (jsonb_typeof(canonical_record_ids) = 'array')
);

CREATE TABLE evidence_checkpoints (
    tenant_id text NOT NULL,
    through_sequence bigint NOT NULL CHECK (through_sequence >= 1),
    head_hash text NOT NULL CHECK (head_hash ~ '^sha256:[0-9a-f]{64}$'),
    signed_at timestamptz NOT NULL,
    signer_key_id text NOT NULL,
    signature text NOT NULL,
    PRIMARY KEY (tenant_id, through_sequence, signer_key_id),
    FOREIGN KEY (tenant_id, through_sequence) REFERENCES evidence_ledger (tenant_id, sequence)
);

CREATE TABLE evidence_legal_hold_events (
    tenant_id text NOT NULL,
    hold_event_id text NOT NULL,
    hold_id text NOT NULL,
    artifact_id text NOT NULL,
    action text NOT NULL CHECK (action IN ('placed', 'released')),
    authority_ref text NOT NULL,
    occurred_at timestamptz NOT NULL,
    evidence_event_id text NOT NULL,
    PRIMARY KEY (tenant_id, hold_event_id),
    FOREIGN KEY (tenant_id, evidence_event_id) REFERENCES evidence_ledger (tenant_id, event_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE projection_fences (
    tenant_id text NOT NULL,
    fence_sequence bigint NOT NULL CHECK (fence_sequence >= 1),
    fence_id text NOT NULL,
    target_ref text NOT NULL,
    target_kind text NOT NULL CHECK (target_kind IN ('subject', 'evidence', 'descendants')),
    cause_event_id text NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, fence_sequence),
    UNIQUE (tenant_id, fence_id),
    FOREIGN KEY (tenant_id, cause_event_id) REFERENCES evidence_ledger (tenant_id, event_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE evidence_deletion_tombstones (
    tenant_id text NOT NULL,
    tombstone_id text NOT NULL,
    deletion_event_id text NOT NULL,
    deleted_record_class text NOT NULL,
    deleted_at timestamptz NOT NULL,
    reason_code text NOT NULL,
    projection_fence_sequence bigint NOT NULL,
    PRIMARY KEY (tenant_id, tombstone_id),
    FOREIGN KEY (tenant_id, deletion_event_id) REFERENCES evidence_ledger (tenant_id, event_id)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (tenant_id, projection_fence_sequence)
        REFERENCES projection_fences (tenant_id, fence_sequence)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE derived_invalidation_events (
    tenant_id text NOT NULL,
    invalidation_event_id text NOT NULL,
    tombstone_id text NOT NULL,
    derived_store text NOT NULL CHECK (derived_store IN (
        'object_index', 'pgvector', 'neo4j', 'summary', 'memory', 'cache', 'evaluation_corpus'
    )),
    store_sequence bigint NOT NULL CHECK (store_sequence >= 1),
    action text NOT NULL CHECK (
        action IN ('requested', 'deleted', 'anonymized', 'unsupported', 'failed')
    ),
    occurred_at timestamptz NOT NULL,
    worker_ref text,
    PRIMARY KEY (tenant_id, invalidation_event_id),
    UNIQUE (tenant_id, tombstone_id, derived_store, store_sequence),
    FOREIGN KEY (tenant_id, tombstone_id)
        REFERENCES evidence_deletion_tombstones (tenant_id, tombstone_id)
);

ALTER TABLE evidence_artifact_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_legal_hold_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE projection_fences ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_deletion_tombstones ENABLE ROW LEVEL SECURITY;
ALTER TABLE derived_invalidation_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_artifact_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE evidence_ledger FORCE ROW LEVEL SECURITY;
ALTER TABLE evidence_checkpoints FORCE ROW LEVEL SECURITY;
ALTER TABLE evidence_legal_hold_events FORCE ROW LEVEL SECURITY;
ALTER TABLE projection_fences FORCE ROW LEVEL SECURITY;
ALTER TABLE evidence_deletion_tombstones FORCE ROW LEVEL SECURITY;
ALTER TABLE derived_invalidation_events FORCE ROW LEVEL SECURITY;

CREATE POLICY evidence_artifact_versions_tenant_policy ON evidence_artifact_versions
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY evidence_ledger_tenant_policy ON evidence_ledger
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY evidence_checkpoints_tenant_policy ON evidence_checkpoints
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY evidence_legal_hold_events_tenant_policy ON evidence_legal_hold_events
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY projection_fences_tenant_policy ON projection_fences
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY evidence_deletion_tombstones_tenant_policy ON evidence_deletion_tombstones
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY derived_invalidation_events_tenant_policy ON derived_invalidation_events
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE FUNCTION reject_evidence_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'evidence records are append-only' USING ERRCODE = '55000';
END;
$$;

CREATE TRIGGER evidence_artifact_versions_append_only BEFORE UPDATE OR DELETE
ON evidence_artifact_versions FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation();
CREATE TRIGGER evidence_ledger_append_only BEFORE UPDATE OR DELETE
ON evidence_ledger FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation();
CREATE TRIGGER evidence_checkpoints_append_only BEFORE UPDATE OR DELETE
ON evidence_checkpoints FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation();
CREATE TRIGGER evidence_legal_hold_events_append_only BEFORE UPDATE OR DELETE
ON evidence_legal_hold_events FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation();
CREATE TRIGGER projection_fences_append_only BEFORE UPDATE OR DELETE
ON projection_fences FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation();
CREATE TRIGGER evidence_deletion_tombstones_append_only BEFORE UPDATE OR DELETE
ON evidence_deletion_tombstones FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation();
CREATE TRIGGER derived_invalidation_events_append_only BEFORE UPDATE OR DELETE
ON derived_invalidation_events FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation();
