-- Production rollback is refused: dropping OPEN-019 identity or digest-conflict
-- evidence would erase attributable ingress reconstruction.
DO $$
BEGIN
    RAISE EXCEPTION 'external message identity migration rollback refused; use governed forward repair'
        USING ERRCODE = '55000';
END;
$$;
