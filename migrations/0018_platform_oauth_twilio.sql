-- Twilio Connect App SID is the platform OAuth application identity,
-- stored with Google/Microsoft. Per-account Twilio tokens stay in
-- connector_credentials after Connect.

ALTER TABLE platform_oauth_clients
    DROP CONSTRAINT IF EXISTS platform_oauth_clients_issuer_check;

ALTER TABLE platform_oauth_clients
    ADD CONSTRAINT platform_oauth_clients_issuer_check
    CHECK (issuer IN ('google', 'microsoft', 'twilio'));

COMMENT ON TABLE platform_oauth_clients IS
    'Encrypted platform OAuth application credentials (Google, Microsoft, Twilio Connect). Set from the operator app. Not mailbox passwords or Twilio auth tokens.';
