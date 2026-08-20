ALTER TABLE cognitive_oauth_sessions
    DROP CONSTRAINT IF EXISTS cognitive_oauth_sessions_provider_id_check;

ALTER TABLE cognitive_oauth_sessions
    ADD CONSTRAINT cognitive_oauth_sessions_provider_id_check
    CHECK (provider_id = 'openai.chatgpt');
