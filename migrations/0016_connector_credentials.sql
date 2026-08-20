-- Connector 3rd-party authorization sessions and encrypted credential bindings.
-- Secrets never enter canonical records, logs, or the operator UI.

CREATE TABLE IF NOT EXISTS connector_oauth_sessions (
    tenant_id text NOT NULL,
    session_id text NOT NULL,
    actor_id text NOT NULL,
    connector_id text NOT NULL,
    grant_id text NOT NULL,
    redirect_uri text NOT NULL,
    code_verifier text NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, session_id)
);

CREATE TABLE IF NOT EXISTS connector_credentials (
    tenant_id text NOT NULL,
    grant_id text NOT NULL,
    connector_id text NOT NULL,
    provider text NOT NULL,
    provider_account_ref text NOT NULL,
    scopes text[] NOT NULL,
    ciphertext bytea NOT NULL,
    nonce bytea NOT NULL,
    key_ref text NOT NULL,
    token_expires_at timestamptz,
    bound_at timestamptz NOT NULL,
    status text NOT NULL,
    PRIMARY KEY (tenant_id, grant_id),
    CHECK (status IN ('bound', 'revoked', 'expired')),
    CHECK (octet_length(nonce) = 12),
    CHECK (octet_length(ciphertext) > 16)
);

CREATE INDEX IF NOT EXISTS connector_oauth_sessions_expiry_idx
    ON connector_oauth_sessions (expires_at);

ALTER TABLE connector_oauth_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector_oauth_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE connector_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector_credentials FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS connector_oauth_sessions_tenant_policy ON connector_oauth_sessions;
CREATE POLICY connector_oauth_sessions_tenant_policy
ON connector_oauth_sessions
USING (tenant_id = current_setting('app.tenant_id', true))
WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS connector_credentials_tenant_policy ON connector_credentials;
CREATE POLICY connector_credentials_tenant_policy
ON connector_credentials
USING (tenant_id = current_setting('app.tenant_id', true))
WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

COMMENT ON TABLE connector_credentials IS
    'AES-256-GCM provider tokens. The operator surface receives only provider_account_ref and status.';
COMMENT ON TABLE connector_oauth_sessions IS
    'Single-use PKCE sessions for Google Workspace and Microsoft 365 authorization.';
