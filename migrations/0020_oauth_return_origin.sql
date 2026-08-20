-- Where to send the browser after Google/Microsoft callback when the
-- OAuth redirect URI is a different HTTPS hostname than the operator tab.

ALTER TABLE connector_oauth_sessions
    ADD COLUMN IF NOT EXISTS return_origin text;

COMMENT ON COLUMN connector_oauth_sessions.return_origin IS
    'Operator page origin to restore after OAuth. Not sent to the provider.';
