-- Platform OAuth application credentials (Google / Microsoft).
-- These are the app's client id/secret, set from the operator UI.
-- Per-user mailbox tokens remain in connector_credentials.

CREATE TABLE IF NOT EXISTS platform_oauth_clients (
    issuer text PRIMARY KEY,
    client_id text NOT NULL,
    directory_id text,
    ciphertext bytea NOT NULL,
    nonce bytea NOT NULL,
    key_ref text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (issuer IN ('google', 'microsoft')),
    CHECK (octet_length(nonce) = 12),
    CHECK (octet_length(ciphertext) > 16)
);

COMMENT ON TABLE platform_oauth_clients IS
    'Encrypted Google/Microsoft OAuth client secrets. Set from the operator app. Not tenant CRM.';
