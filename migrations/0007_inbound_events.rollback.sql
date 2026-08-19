-- Safe only before inbound capture activation. Durable admitted events require forward repair.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM inbound_events LIMIT 1) THEN
        RAISE EXCEPTION
            'inbound event registry contains data; rollback refused, use forward repair'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

DROP TRIGGER inbound_events_append_only ON inbound_events;
DROP TABLE inbound_events;
