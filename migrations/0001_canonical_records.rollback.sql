-- Safe rollback for an unactivated PKT-01 installation.
-- Canonical data is never discarded by rollback; a populated store requires forward repair.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM canonical_records_current LIMIT 1)
       OR EXISTS (SELECT 1 FROM canonical_record_versions LIMIT 1) THEN
        RAISE EXCEPTION
            'canonical store contains data; rollback is prohibited, use a forward repair migration'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

DROP TABLE canonical_record_versions;
DROP TABLE canonical_records_current;
DROP FUNCTION reject_canonical_history_mutation();
