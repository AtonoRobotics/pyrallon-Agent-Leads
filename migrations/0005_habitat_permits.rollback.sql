-- Production rollback is intentionally refused: dropping permit/decision evidence would erase
-- attributable authority ordering. Forward repair or an explicitly governed archival migration is required.
DO $$
BEGIN
    RAISE EXCEPTION 'habitat permit migration rollback refused; use governed forward repair'
        USING ERRCODE = '55000';
END;
$$;
