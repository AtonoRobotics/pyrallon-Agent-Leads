-- Cognitive credential identities and encrypted material.
-- Secrets never enter gateway CredentialIdentity records or the operator UI.
-- Live cognition stays fail-closed until signed release-activation.

CREATE TABLE IF NOT EXISTS cognitive_oauth_sessions (
    tenant_id text NOT NULL,
    session_id text NOT NULL,
    actor_id text NOT NULL,
    provider_id text NOT NULL,
    device_code text NOT NULL,
    code_verifier text NOT NULL,
    user_code text NOT NULL,
    verification_uri text NOT NULL,
    poll_interval_seconds integer NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, session_id),
    CHECK (provider_id = 'openai.chatgpt')
);

CREATE TABLE IF NOT EXISTS cognitive_credentials (
    tenant_id text NOT NULL,
    identity_ref text NOT NULL,
    provider_id text NOT NULL,
    auth_class text NOT NULL,
    billing_class text NOT NULL,
    provider_account_ref text NOT NULL,
    ciphertext bytea NOT NULL,
    nonce bytea NOT NULL,
    key_ref text NOT NULL,
    identity_record jsonb NOT NULL,
    token_expires_at timestamptz,
    bound_at timestamptz NOT NULL,
    status text NOT NULL,
    PRIMARY KEY (tenant_id, identity_ref),
    CHECK (status IN ('bound', 'revoked', 'expired')),
    CHECK (auth_class IN (
        'subscription_oauth', 'workspace_access_token', 'service_account',
        'metered_api', 'cloud_iam', 'local_endpoint'
    )),
    CHECK (billing_class IN ('subscription', 'metered', 'internal')),
    CHECK (octet_length(nonce) = 12),
    CHECK (octet_length(ciphertext) > 16)
);

CREATE INDEX IF NOT EXISTS cognitive_oauth_sessions_expiry_idx
    ON cognitive_oauth_sessions (expires_at);

ALTER TABLE cognitive_oauth_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE cognitive_oauth_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE cognitive_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE cognitive_credentials FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS cognitive_oauth_sessions_tenant_policy ON cognitive_oauth_sessions;
CREATE POLICY cognitive_oauth_sessions_tenant_policy
ON cognitive_oauth_sessions
USING (tenant_id = current_setting('app.tenant_id', true))
WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

DROP POLICY IF EXISTS cognitive_credentials_tenant_policy ON cognitive_credentials;
CREATE POLICY cognitive_credentials_tenant_policy
ON cognitive_credentials
USING (tenant_id = current_setting('app.tenant_id', true))
WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

COMMENT ON TABLE cognitive_credentials IS
    'AES-256-GCM cognitive tokens and API keys. Operator UI receives identityRef, authClass, billingClass, providerAccountRef, and state.';
COMMENT ON TABLE cognitive_oauth_sessions IS
    'Single-use ChatGPT device-code OAuth sessions. Secrets stay off the operator surface.';
