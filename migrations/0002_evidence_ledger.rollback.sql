-- Safe rollback for an unactivated PKT-02 installation.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM evidence_artifact_versions LIMIT 1)
       OR EXISTS (SELECT 1 FROM evidence_ledger LIMIT 1) THEN
        RAISE EXCEPTION
            'evidence store contains data; rollback is prohibited, use forward repair'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

DROP TABLE derived_invalidation_events;
DROP TABLE evidence_deletion_tombstones;
DROP TABLE projection_fences;
DROP TABLE evidence_legal_hold_events;
DROP TABLE evidence_checkpoints;
DROP TABLE evidence_ledger;
DROP TABLE evidence_artifact_versions;
DROP FUNCTION reject_evidence_mutation();
