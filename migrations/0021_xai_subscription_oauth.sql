-- SuperGrok / X Premium+ device-code OAuth sits beside ChatGPT device-code.
-- Metered xAI API keys remain a separate cognition connector.

ALTER TABLE cognitive_oauth_sessions
    DROP CONSTRAINT IF EXISTS cognitive_oauth_sessions_provider_id_check;

ALTER TABLE cognitive_oauth_sessions
    ADD CONSTRAINT cognitive_oauth_sessions_provider_id_check
    CHECK (provider_id IN ('openai.chatgpt', 'xai.subscription'));

COMMENT ON TABLE cognitive_oauth_sessions IS
    'Single-use ChatGPT and SuperGrok device-code OAuth sessions. Secrets stay off the operator surface.';
