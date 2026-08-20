-- Rollback refused while platform OAuth clients exist.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM platform_oauth_clients LIMIT 1)
    THEN
        RAISE EXCEPTION 'rollback refused: platform OAuth client evidence exists';
    END IF;
END $$;

DROP TABLE IF EXISTS platform_oauth_clients;
