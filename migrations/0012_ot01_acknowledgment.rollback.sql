DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM ingress_ack_config_versions LIMIT 1)
       OR EXISTS (SELECT 1 FROM ingress_acknowledgment_decisions LIMIT 1)
       OR EXISTS (SELECT 1 FROM ingress_acknowledgment_outcomes LIMIT 1) THEN
        RAISE EXCEPTION 'rollback refused: acknowledgment configuration or evidence exists';
    END IF;
END $$;
DROP TABLE ingress_acknowledgment_outcomes;
DROP TABLE ingress_acknowledgment_decisions;
DROP TABLE ingress_ack_configs_current;
DROP TABLE ingress_ack_config_versions;
