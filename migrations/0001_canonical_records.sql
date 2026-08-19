-- PKT-01 foundation: PostgreSQL is the canonical authority.
-- Records are stored only after generated ontology validation.

CREATE TABLE canonical_records_current (
    tenant_id text NOT NULL,
    record_id text NOT NULL,
    version bigint NOT NULL CHECK (version >= 1),
    record_type text NOT NULL,
    schema_version text NOT NULL CHECK (schema_version = 'buyer-ops/0.1.0'),
    record jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, record_id),
    CHECK ((record->>'tenantId') IS NOT DISTINCT FROM tenant_id),
    CHECK ((record->>'id') IS NOT DISTINCT FROM record_id),
    CHECK (((record->>'version')::bigint) IS NOT DISTINCT FROM version),
    CHECK ((record->>'recordType') IS NOT DISTINCT FROM record_type),
    CHECK ((record->>'schemaVersion') IS NOT DISTINCT FROM schema_version)
);

CREATE TABLE canonical_record_versions (
    tenant_id text NOT NULL,
    record_id text NOT NULL,
    version bigint NOT NULL CHECK (version >= 1),
    record_type text NOT NULL,
    schema_version text NOT NULL CHECK (schema_version = 'buyer-ops/0.1.0'),
    record jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, record_id, version),
    FOREIGN KEY (tenant_id, record_id)
        REFERENCES canonical_records_current (tenant_id, record_id)
        DEFERRABLE INITIALLY DEFERRED,
    CHECK ((record->>'tenantId') IS NOT DISTINCT FROM tenant_id),
    CHECK ((record->>'id') IS NOT DISTINCT FROM record_id),
    CHECK (((record->>'version')::bigint) IS NOT DISTINCT FROM version),
    CHECK ((record->>'recordType') IS NOT DISTINCT FROM record_type),
    CHECK ((record->>'schemaVersion') IS NOT DISTINCT FROM schema_version)
);

CREATE INDEX canonical_records_current_tenant_type_idx
    ON canonical_records_current (tenant_id, record_type);

ALTER TABLE canonical_records_current ENABLE ROW LEVEL SECURITY;
ALTER TABLE canonical_record_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE canonical_records_current FORCE ROW LEVEL SECURITY;
ALTER TABLE canonical_record_versions FORCE ROW LEVEL SECURITY;

CREATE POLICY canonical_records_current_tenant_policy
    ON canonical_records_current
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE POLICY canonical_record_versions_tenant_policy
    ON canonical_record_versions
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE FUNCTION reject_canonical_history_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'canonical record history is append-only'
        USING ERRCODE = '55000';
END;
$$;

CREATE TRIGGER canonical_record_versions_append_only
BEFORE UPDATE OR DELETE ON canonical_record_versions
FOR EACH ROW EXECUTE FUNCTION reject_canonical_history_mutation();

COMMENT ON TABLE canonical_records_current IS
    'Current tenant-scoped ontology records; writes require generated contract validation.';
COMMENT ON TABLE canonical_record_versions IS
    'Append-only historical ontology record versions for reconstruction.';
