-- Closure records and stable message identities are durable; populated state requires forward repair.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM inbound_message_conflicts LIMIT 1)
       OR EXISTS (SELECT 1 FROM closure_records LIMIT 1)
       OR EXISTS (SELECT 1 FROM telemetry_observations LIMIT 1)
       OR EXISTS (SELECT 1 FROM release_gate_evidence LIMIT 1)
       OR EXISTS (SELECT 1 FROM release_activation_decisions LIMIT 1)
       OR EXISTS (SELECT 1 FROM ingress_attribution LIMIT 1)
       OR EXISTS (SELECT 1 FROM ingress_consent_presentation LIMIT 1)
       OR EXISTS (SELECT 1 FROM operator_actor_tenancies LIMIT 1)
       OR EXISTS (
           SELECT 1 FROM inbound_events
           WHERE connector_id IS NOT NULL OR external_message_id IS NOT NULL
       ) THEN
        RAISE EXCEPTION
            'control-plane closure state exists; rollback refused, use forward repair'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

DROP TABLE operator_actor_tenancies;
DROP TABLE ingress_consent_presentation;
DROP TABLE ingress_attribution;
DROP TABLE release_activation_decisions;
DROP TABLE release_gate_evidence;
DROP TABLE telemetry_observations;
DROP TABLE closure_records_current;
DROP TABLE closure_records;
DROP TABLE inbound_message_conflicts;
DROP INDEX inbound_events_stable_message_key;
ALTER TABLE inbound_events DROP COLUMN external_event_id;
ALTER TABLE inbound_events DROP COLUMN external_message_id;
ALTER TABLE inbound_events DROP COLUMN connector_id;
