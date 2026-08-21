DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM voice_call_events LIMIT 1)
       OR EXISTS (SELECT 1 FROM voice_call_current LIMIT 1) THEN
        RAISE EXCEPTION 'rollback refused: voice call state exists';
    END IF;
END $$;

DROP TABLE IF EXISTS voice_call_current;
DROP TABLE IF EXISTS voice_call_events;
