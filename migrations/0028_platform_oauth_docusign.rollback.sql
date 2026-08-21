DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM platform_oauth_clients WHERE issuer = 'docusign' LIMIT 1)
    THEN
        RAISE EXCEPTION 'rollback refused: DocuSign app identity exists';
    END IF;
END $$;

ALTER TABLE platform_oauth_clients
    DROP CONSTRAINT IF EXISTS platform_oauth_clients_issuer_check;

ALTER TABLE platform_oauth_clients
    ADD CONSTRAINT platform_oauth_clients_issuer_check
    CHECK (issuer IN ('google', 'microsoft', 'twilio'));
