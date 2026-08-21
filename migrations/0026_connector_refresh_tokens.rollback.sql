-- Refresh-token rollback is refused while credential rows exist to avoid silently
-- discarding the only durable recovery path for provider authorization.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM connector_credentials
        WHERE refresh_ciphertext IS NOT NULL OR refresh_nonce IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'rollback refused: encrypted connector refresh evidence exists';
    END IF;
END $$;

ALTER TABLE connector_credentials
    DROP CONSTRAINT IF EXISTS connector_credentials_refresh_nonce_length;
ALTER TABLE connector_credentials
    DROP COLUMN IF EXISTS refresh_ciphertext,
    DROP COLUMN IF EXISTS refresh_nonce;
