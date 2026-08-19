-- PKT-01 / OT-01: deterministic external identity mappings and ambiguity cases.

CREATE TABLE external_identity_mappings_current (
    tenant_id text NOT NULL,
    identity_fingerprint text NOT NULL CHECK (identity_fingerprint ~ '^sha256:[0-9a-f]{64}$'),
    mapping_id text NOT NULL,
    version bigint NOT NULL CHECK (version >= 1),
    identity_kind text NOT NULL CHECK (
        identity_kind IN ('verified_email', 'verified_phone', 'provider_identity', 'thread_lineage')
    ),
    normalized_identity text NOT NULL,
    provider_account_ref text,
    purpose text NOT NULL,
    resolution_basis text NOT NULL CHECK (resolution_basis IN (
        'verified_endpoint', 'provider_identity', 'external_mapping',
        'thread_lineage', 'explicit_form_identity'
    )),
    resolution_authority_ref text,
    outcome text NOT NULL CHECK (
        outcome IN ('matched', 'created', 'ambiguous', 'conflict', 'suppressed')
    ),
    person_id text,
    person_version bigint,
    resolution_case_id text,
    candidate_person_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence_ids jsonb NOT NULL CHECK (jsonb_array_length(evidence_ids) >= 1),
    effective_from timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, identity_fingerprint),
    UNIQUE (tenant_id, mapping_id),
    CHECK (jsonb_typeof(candidate_person_ids) = 'array'),
    CHECK (jsonb_typeof(evidence_ids) = 'array'),
    CHECK (
        (outcome IN ('matched', 'created') AND person_id IS NOT NULL
            AND person_version IS NOT NULL AND resolution_case_id IS NULL)
        OR
        (outcome IN ('ambiguous', 'conflict') AND person_id IS NULL
            AND person_version IS NULL AND resolution_case_id IS NOT NULL)
        OR outcome = 'suppressed'
    )
);

CREATE TABLE external_identity_mapping_versions (
    tenant_id text NOT NULL,
    identity_fingerprint text NOT NULL,
    mapping_id text NOT NULL,
    version bigint NOT NULL CHECK (version >= 1),
    mapping jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, mapping_id, version),
    FOREIGN KEY (tenant_id, identity_fingerprint)
        REFERENCES external_identity_mappings_current (tenant_id, identity_fingerprint)
        DEFERRABLE INITIALLY DEFERRED,
    CHECK ((mapping->>'tenantId') IS NOT DISTINCT FROM tenant_id),
    CHECK ((mapping->>'identityFingerprint') IS NOT DISTINCT FROM identity_fingerprint),
    CHECK ((mapping->>'mappingId') IS NOT DISTINCT FROM mapping_id),
    CHECK (((mapping->>'version')::bigint) IS NOT DISTINCT FROM version)
);

CREATE TABLE identity_resolution_cases (
    tenant_id text NOT NULL,
    resolution_case_id text NOT NULL,
    identity_fingerprint text NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('ambiguous', 'conflict')),
    candidate_person_ids jsonb NOT NULL,
    evidence_ids jsonb NOT NULL CHECK (jsonb_array_length(evidence_ids) >= 1),
    created_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, resolution_case_id),
    FOREIGN KEY (tenant_id, identity_fingerprint)
        REFERENCES external_identity_mappings_current (tenant_id, identity_fingerprint)
        DEFERRABLE INITIALLY DEFERRED,
    CHECK (jsonb_typeof(candidate_person_ids) = 'array'),
    CHECK (jsonb_typeof(evidence_ids) = 'array')
);

ALTER TABLE external_identity_mappings_current ENABLE ROW LEVEL SECURITY;
ALTER TABLE external_identity_mapping_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity_resolution_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE external_identity_mappings_current FORCE ROW LEVEL SECURITY;
ALTER TABLE external_identity_mapping_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE identity_resolution_cases FORCE ROW LEVEL SECURITY;

CREATE POLICY external_identity_mappings_current_tenant_policy
ON external_identity_mappings_current
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY external_identity_mapping_versions_tenant_policy
ON external_identity_mapping_versions
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY identity_resolution_cases_tenant_policy ON identity_resolution_cases
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE TRIGGER external_identity_mapping_versions_append_only BEFORE UPDATE OR DELETE
ON external_identity_mapping_versions FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
CREATE TRIGGER identity_resolution_cases_append_only BEFORE UPDATE OR DELETE
ON identity_resolution_cases FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();
