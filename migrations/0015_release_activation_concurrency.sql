-- Release Activation 1.1 optimistic concurrency and deterministic current ordering.

ALTER TABLE release_activation_decisions
    ADD COLUMN IF NOT EXISTS activation_version integer;

WITH ranked AS (
    SELECT tenant_id, decision_id,
           row_number() OVER (
               PARTITION BY tenant_id, capability_id
               ORDER BY decided_at, decision_id
           ) AS activation_version
    FROM release_activation_decisions
)
UPDATE release_activation_decisions AS decisions
SET activation_version = ranked.activation_version
FROM ranked
WHERE decisions.tenant_id = ranked.tenant_id
  AND decisions.decision_id = ranked.decision_id
  AND decisions.activation_version IS NULL;

ALTER TABLE release_activation_decisions
    ALTER COLUMN activation_version SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS release_activation_decisions_version_uidx
ON release_activation_decisions (tenant_id, capability_id, activation_version);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'release_activation_expected_version_matches'
          AND conrelid = 'release_activation_decisions'::regclass
    ) THEN
        ALTER TABLE release_activation_decisions
            ADD CONSTRAINT release_activation_expected_version_matches
            CHECK (
                (payload->>'schemaVersion') IS DISTINCT FROM 'release-activation/1.1.0'
                OR (payload->>'expectedActivationVersion')::integer = activation_version - 1
            ) NOT VALID;
    END IF;
END $$;
