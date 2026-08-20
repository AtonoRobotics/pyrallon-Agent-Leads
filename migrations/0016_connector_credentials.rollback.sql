-- Rollback refused while connector authorization evidence exists.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM connector_credentials LIMIT 1)
       OR EXISTS (SELECT 1 FROM connector_oauth_sessions LIMIT 1)
    THEN
        RAISE EXCEPTION 'rollback refused: connector credential evidence exists';
    END IF;
END $$;

DROP TABLE IF EXISTS connector_credentials;
DROP TABLE IF EXISTS connector_oauth_sessions;
