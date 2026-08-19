DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM release_activation_versions LIMIT 1) THEN
        RAISE EXCEPTION 'rollback refused: release activation evidence exists';
    END IF;
END $$;

DROP TABLE IF EXISTS release_activation_versions;
