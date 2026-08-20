DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM release_activation_decisions LIMIT 1) THEN
        RAISE EXCEPTION
            'release activation decisions exist; rollback refused, use forward repair'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

ALTER TABLE release_activation_decisions
    DROP CONSTRAINT IF EXISTS release_activation_expected_version_matches;
DROP INDEX IF EXISTS release_activation_decisions_version_uidx;
ALTER TABLE release_activation_decisions DROP COLUMN activation_version;
