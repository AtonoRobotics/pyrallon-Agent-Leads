-- DocuSign OAuth application credentials use the same encrypted platform store.
ALTER TABLE platform_oauth_clients
    DROP CONSTRAINT IF EXISTS platform_oauth_clients_issuer_check;

ALTER TABLE platform_oauth_clients
    ADD CONSTRAINT platform_oauth_clients_issuer_check
    CHECK (issuer IN ('google', 'microsoft', 'twilio', 'docusign'));

COMMENT ON TABLE platform_oauth_clients IS
    'Encrypted platform OAuth application credentials for Google, Microsoft, Twilio Connect, and DocuSign.';
