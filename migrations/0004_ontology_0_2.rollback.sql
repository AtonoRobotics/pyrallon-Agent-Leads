-- Stored 0.2.0 records may use types and fields unknown to 0.1.0.
-- Automatic rollback would silently reinterpret canonical history and is prohibited.
DO $$
BEGIN
    RAISE EXCEPTION 'rollback is prohibited; restore a pre-cutover backup or forward-repair on buyer-ops/0.2.x'
        USING ERRCODE = '55000';
END;
$$;

