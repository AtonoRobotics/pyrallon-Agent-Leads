-- Safe rollback for an unactivated OT-01 identity mapping installation.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM external_identity_mappings_current LIMIT 1)
       OR EXISTS (SELECT 1 FROM identity_resolution_cases LIMIT 1) THEN
        RAISE EXCEPTION
            'identity mapping store contains data; rollback is prohibited, use forward repair'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

DROP TABLE identity_resolution_cases;
DROP TABLE external_identity_mapping_versions;
DROP TABLE external_identity_mappings_current;
