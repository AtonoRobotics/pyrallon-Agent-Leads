DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM operator_policy_versions LIMIT 1)
       OR EXISTS (SELECT 1 FROM operator_command_results LIMIT 1) THEN
        RAISE EXCEPTION 'rollback refused: operator policy or command evidence exists';
    END IF;
END $$;

DROP TABLE operator_policies_current;
DROP TABLE operator_policy_versions;
