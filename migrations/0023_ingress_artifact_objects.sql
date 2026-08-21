-- PKT-02 / PKT-05: durable encrypted source objects for configured ingress.
-- The application stores only encrypted bytes here; the plaintext digest and
-- evidence metadata remain separately addressable in evidence_artifact_versions.

CREATE TABLE ingress_artifact_objects (
    tenant_id text NOT NULL,
    artifact_id text NOT NULL,
    encrypted_object_ref text NOT NULL,
    encrypted_blob bytea NOT NULL,
    object_lock_until timestamptz,
    provider_legal_hold boolean NOT NULL DEFAULT false,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, artifact_id),
    UNIQUE (tenant_id, encrypted_object_ref)
);

ALTER TABLE ingress_artifact_objects ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingress_artifact_objects FORCE ROW LEVEL SECURITY;

CREATE POLICY ingress_artifact_objects_tenant_policy ON ingress_artifact_objects
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

CREATE TRIGGER ingress_artifact_objects_append_only BEFORE UPDATE OR DELETE
ON ingress_artifact_objects FOR EACH ROW EXECUTE FUNCTION reject_evidence_mutation();

COMMENT ON TABLE ingress_artifact_objects IS
    'Encrypted ingress payloads; append-only and tenant-scoped.';
