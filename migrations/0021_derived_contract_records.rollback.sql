DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM derived_contract_records LIMIT 1) THEN
        RAISE EXCEPTION 'rollback refused: derived contract-family records exist';
    END IF;
END $$;

DROP TABLE IF EXISTS derived_contract_records;
