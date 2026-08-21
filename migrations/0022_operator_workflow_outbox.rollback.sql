DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM operator_workflow_signal_receipts LIMIT 1)
       OR EXISTS (SELECT 1 FROM operator_workflow_outbox LIMIT 1) THEN
        RAISE EXCEPTION
            'operator workflow outbox contains data; rollback refused, use forward repair'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

DROP TRIGGER operator_workflow_signal_receipts_append_only ON operator_workflow_signal_receipts;
DROP TABLE operator_workflow_signal_receipts;
DROP TABLE operator_workflow_outbox;
