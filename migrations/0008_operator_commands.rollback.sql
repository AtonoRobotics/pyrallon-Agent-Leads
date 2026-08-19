-- Safe only before operator mutation activation. Applied command results require forward repair.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM operator_command_results LIMIT 1) THEN
        RAISE EXCEPTION
            'operator command results contain data; rollback refused, use forward repair'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

DROP TRIGGER operator_command_results_append_only ON operator_command_results;
DROP TABLE operator_command_results;
