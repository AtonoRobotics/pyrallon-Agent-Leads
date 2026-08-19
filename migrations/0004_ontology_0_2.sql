-- PKT-00/01 contract cutover: buyer-ops/0.1.0 -> buyer-ops/0.2.0.
-- Run only after the application can validate 0.2.0 and before accepting 0.2.0 writes.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM canonical_records_current WHERE record_type = 'EpistemicItem'
        UNION ALL
        SELECT 1 FROM canonical_record_versions WHERE record_type = 'EpistemicItem'
    ) THEN
        RAISE EXCEPTION 'ontology 0.2.0 migration requires EpistemicItem forward repair; run the typed conversion tool first'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

ALTER TABLE canonical_records_current
    DROP CONSTRAINT canonical_records_current_schema_version_check;
ALTER TABLE canonical_record_versions
    DROP CONSTRAINT canonical_record_versions_schema_version_check;

ALTER TABLE canonical_record_versions DISABLE TRIGGER canonical_record_versions_append_only;

UPDATE canonical_records_current
SET schema_version = 'buyer-ops/0.2.0',
    record = jsonb_set(
        CASE record_type
            WHEN 'BuyingParty' THEN record || '{"decisionAuthorityState":"unconfirmed"}'::jsonb
            WHEN 'BuyerJourney' THEN record || '{"territory":"unspecified"}'::jsonb
            ELSE record
        END,
        '{schemaVersion}', '"buyer-ops/0.2.0"'::jsonb
    ) || jsonb_build_object('status', CASE WHEN record->>'status' IN ('active','inactive','superseded','tombstoned','invalid') THEN record->>'status' ELSE 'active' END);

UPDATE canonical_record_versions
SET schema_version = 'buyer-ops/0.2.0',
    record = jsonb_set(
        CASE record_type
            WHEN 'BuyingParty' THEN record || '{"decisionAuthorityState":"unconfirmed"}'::jsonb
            WHEN 'BuyerJourney' THEN record || '{"territory":"unspecified"}'::jsonb
            ELSE record
        END,
        '{schemaVersion}', '"buyer-ops/0.2.0"'::jsonb
    ) || jsonb_build_object('status', CASE WHEN record->>'status' IN ('active','inactive','superseded','tombstoned','invalid') THEN record->>'status' ELSE 'active' END);

ALTER TABLE canonical_record_versions ENABLE TRIGGER canonical_record_versions_append_only;

ALTER TABLE canonical_records_current
    ADD CONSTRAINT canonical_records_current_schema_version_check
    CHECK (schema_version = 'buyer-ops/0.2.0');
ALTER TABLE canonical_record_versions
    ADD CONSTRAINT canonical_record_versions_schema_version_check
    CHECK (schema_version = 'buyer-ops/0.2.0');
