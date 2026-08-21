-- Rollback is refused once any permit has a dispatch claim because removing the
-- claim would make an already-dispatched external effect replayable.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM habitat_effect_permits WHERE dispatch_claimed_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'rollback refused: effect dispatch claims exist';
    END IF;
END $$;

DROP INDEX IF EXISTS habitat_effect_permits_dispatch_claim_idx;
ALTER TABLE habitat_effect_permits DROP COLUMN IF EXISTS dispatch_claimed_at;
