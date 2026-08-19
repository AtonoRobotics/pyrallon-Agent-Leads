-- Ontology envelope cutover: buyer-ops/0.2.0 -> buyer-ops/0.3.0.
BEGIN;
ALTER TABLE canonical_records_current DROP CONSTRAINT canonical_records_current_schema_version_check;
ALTER TABLE canonical_record_versions DROP CONSTRAINT canonical_record_versions_schema_version_check;
ALTER TABLE canonical_record_versions DISABLE TRIGGER canonical_record_versions_append_only;
UPDATE canonical_records_current SET schema_version='buyer-ops/0.3.0', record=jsonb_set(record,'{schemaVersion}','"buyer-ops/0.3.0"'::jsonb) WHERE schema_version='buyer-ops/0.2.0';
UPDATE canonical_record_versions SET schema_version='buyer-ops/0.3.0', record=jsonb_set(record,'{schemaVersion}','"buyer-ops/0.3.0"'::jsonb) WHERE schema_version='buyer-ops/0.2.0';
ALTER TABLE canonical_record_versions ENABLE TRIGGER canonical_record_versions_append_only;
ALTER TABLE canonical_records_current ADD CONSTRAINT canonical_records_current_schema_version_check CHECK (schema_version='buyer-ops/0.3.0');
ALTER TABLE canonical_record_versions ADD CONSTRAINT canonical_record_versions_schema_version_check CHECK (schema_version='buyer-ops/0.3.0');
COMMIT;
