-- PKT-03/FR-14: a redeemed effect permit may authorize exactly one dispatch attempt.

ALTER TABLE habitat_effect_permits
    ADD COLUMN IF NOT EXISTS dispatch_claimed_at timestamptz;

CREATE INDEX IF NOT EXISTS habitat_effect_permits_dispatch_claim_idx
    ON habitat_effect_permits (tenant_id, permit_digest, dispatch_claimed_at);

COMMENT ON COLUMN habitat_effect_permits.dispatch_claimed_at IS
    'Atomic single-use claim taken immediately before connector dispatch; retries reconcile the EffectAttempt.';
