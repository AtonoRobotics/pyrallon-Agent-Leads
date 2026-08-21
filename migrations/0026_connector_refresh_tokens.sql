-- Preserve OAuth refresh material separately from the short-lived access token.
-- Both values remain encrypted and tenant-scoped; neither enters canonical records.

ALTER TABLE connector_credentials
    ADD COLUMN IF NOT EXISTS refresh_ciphertext bytea,
    ADD COLUMN IF NOT EXISTS refresh_nonce bytea;

ALTER TABLE connector_credentials
    DROP CONSTRAINT IF EXISTS connector_credentials_refresh_nonce_length;
ALTER TABLE connector_credentials
    ADD CONSTRAINT connector_credentials_refresh_nonce_length
    CHECK (refresh_nonce IS NULL OR octet_length(refresh_nonce) = 12);

COMMENT ON COLUMN connector_credentials.refresh_ciphertext IS
    'AES-256-GCM encrypted OAuth refresh token; null for non-refreshable credentials.';
COMMENT ON COLUMN connector_credentials.refresh_nonce IS
    'AES-GCM nonce for refresh_ciphertext; null when no refresh token exists.';
