DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM esignature_operation_records LIMIT 1) THEN
        RAISE EXCEPTION 'rollback refused: e-signature operation evidence exists';
    END IF;
END $$;

DROP TABLE IF EXISTS esignature_operation_records;
