-- Rollback refused while OPEN-025 authorization evidence exists.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM actor_tenant_authorizations_current LIMIT 1)
       OR EXISTS (SELECT 1 FROM actor_tenant_authorization_versions LIMIT 1)
    THEN
        RAISE EXCEPTION 'rollback refused: OPEN-025 authorization evidence exists';
    END IF;
END $$;

DROP TABLE IF EXISTS actor_tenant_authorizations_current;
DROP TABLE IF EXISTS actor_tenant_authorization_versions;
