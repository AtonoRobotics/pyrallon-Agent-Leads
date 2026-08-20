-- Rollback refused while cognitive credential evidence exists.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM cognitive_credentials LIMIT 1)
    THEN
        RAISE EXCEPTION 'rollback refused: cognitive credential evidence exists';
    END IF;
END $$;

DROP TABLE IF EXISTS cognitive_oauth_sessions;
DROP TABLE IF EXISTS cognitive_credentials;
