DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM canonical_records_current WHERE record_type IN ('ConnectorGrant','ConfirmedTransactionDate'))
       OR EXISTS (SELECT 1 FROM canonical_record_versions WHERE record_type IN ('ConnectorGrant','ConfirmedTransactionDate')) THEN
        RAISE EXCEPTION 'rollback refused: ontology 0.3 canonical records cannot be represented by 0.2' USING ERRCODE='55000';
    END IF;
END;
$$;
BEGIN;
ALTER TABLE canonical_records_current DROP CONSTRAINT canonical_records_current_schema_version_check;
ALTER TABLE canonical_record_versions DROP CONSTRAINT canonical_record_versions_schema_version_check;
ALTER TABLE canonical_record_versions DISABLE TRIGGER canonical_record_versions_append_only;
UPDATE canonical_records_current SET schema_version='buyer-ops/0.2.0', record=jsonb_set(record,'{schemaVersion}','"buyer-ops/0.2.0"'::jsonb);
UPDATE canonical_record_versions SET schema_version='buyer-ops/0.2.0', record=jsonb_set(record,'{schemaVersion}','"buyer-ops/0.2.0"'::jsonb);
ALTER TABLE canonical_record_versions ENABLE TRIGGER canonical_record_versions_append_only;
ALTER TABLE canonical_records_current ADD CONSTRAINT canonical_records_current_schema_version_check CHECK (schema_version='buyer-ops/0.2.0');
ALTER TABLE canonical_record_versions ADD CONSTRAINT canonical_record_versions_schema_version_check CHECK (schema_version='buyer-ops/0.2.0');
COMMIT;
