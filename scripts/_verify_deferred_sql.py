"""Verification-only: apply the non-vector portions of migrations 0008 + 0009 directly
to confirm SQL parses and PL/pgSQL compiles in PG 16.

Not part of the alembic chain. Tear down after running."""

from __future__ import annotations

import asyncio
import asyncpg


TRIGGER_FN = """
CREATE OR REPLACE FUNCTION audit_trigger_fn() RETURNS TRIGGER AS $$
DECLARE
    v_actor      TEXT;
    v_request_id UUID;
    v_diff       JSONB;
    v_row_id     BIGINT;
BEGIN
    BEGIN
        v_actor := NULLIF(current_setting('audit.user', true), '');
    EXCEPTION WHEN OTHERS THEN v_actor := NULL; END;
    BEGIN
        v_request_id := NULLIF(current_setting('audit.request_id', true), '')::uuid;
    EXCEPTION WHEN OTHERS THEN v_request_id := NULL; END;

    IF TG_OP = 'INSERT' THEN
        v_diff   := jsonb_build_object('new', to_jsonb(NEW));
        v_row_id := (to_jsonb(NEW) ->> 'id')::bigint;
        INSERT INTO audit_log (event_at, actor, table_name, operation, row_id, diff, request_id)
        VALUES (now(), v_actor, TG_TABLE_NAME, 'I', v_row_id, v_diff, v_request_id);
        RETURN NEW;
    ELSIF TG_OP = 'UPDATE' THEN
        v_diff   := jsonb_build_object('old', to_jsonb(OLD), 'new', to_jsonb(NEW));
        v_row_id := (to_jsonb(NEW) ->> 'id')::bigint;
        INSERT INTO audit_log (event_at, actor, table_name, operation, row_id, diff, request_id)
        VALUES (now(), v_actor, TG_TABLE_NAME, 'U', v_row_id, v_diff, v_request_id);
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        v_diff   := jsonb_build_object('old', to_jsonb(OLD));
        v_row_id := (to_jsonb(OLD) ->> 'id')::bigint;
        INSERT INTO audit_log (event_at, actor, table_name, operation, row_id, diff, request_id)
        VALUES (now(), v_actor, TG_TABLE_NAME, 'D', v_row_id, v_diff, v_request_id);
        RETURN OLD;
    END IF;
    RETURN NULL;
END$$ LANGUAGE plpgsql;
"""

DERIVE_FN = """
CREATE OR REPLACE FUNCTION derive_service_variant(p_edi_file_id BIGINT) RETURNS TEXT AS $$
DECLARE gs08 TEXT;
BEGIN
    SELECT split_part(raw_segment_text, E'*', 9) INTO gs08
      FROM raw_segments WHERE edi_file_id = p_edi_file_id AND segment_name = 'GS' LIMIT 1;
    RETURN CASE
        WHEN gs08 LIKE '005010X222%' THEN '837P'
        WHEN gs08 LIKE '005010X223%' THEN '837I'
        WHEN gs08 LIKE '005010X224%' THEN '837D'
        WHEN gs08 LIKE '005010X221%' THEN '835'
        ELSE NULL
    END;
END$$ LANGUAGE plpgsql STABLE;
"""

IS_DENIED_FN = """
CREATE OR REPLACE FUNCTION is_denied(p_claim_id BIGINT) RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
      SELECT 1 FROM remittance_claims WHERE claim_id = p_claim_id AND claim_status_code = '4'
    );
END$$ LANGUAGE plpgsql STABLE;
"""

LIFECYCLE_FN = """
CREATE OR REPLACE FUNCTION claim_lifecycle_resolved(p_claim_id BIGINT) RETURNS BOOLEAN AS $$
DECLARE v_found BOOLEAN;
BEGIN
    WITH RECURSIVE chain AS (
        SELECT id, claim_status FROM claims WHERE id = p_claim_id
        UNION ALL
        SELECT c.id, c.claim_status FROM claim_lifecycles cl
        JOIN claims c ON c.id = cl.child_claim_id
        JOIN chain ch ON ch.id = cl.parent_claim_id
    )
    SELECT EXISTS (SELECT 1 FROM chain WHERE claim_status IN ('paid', 'partially_paid')) INTO v_found;
    RETURN COALESCE(v_found, FALSE);
END$$ LANGUAGE plpgsql STABLE;
"""

# MV from mig 0010 — also vector-independent
MV_CLAIM_LABELS = """
DROP MATERIALIZED VIEW IF EXISTS mv_claim_labels;
CREATE MATERIALIZED VIEW mv_claim_labels AS
SELECT
    c.id                AS claim_id,
    c.service_variant,
    c.claim_subtype,
    c.payer_id,
    c.billing_provider_id,
    c.rendering_provider_id,
    c.patient_id,
    c.service_from_date,
    CASE
        WHEN bool_or(rc.claim_status_code = '4') THEN 1
        WHEN bool_or(rc.claim_status_code IN ('1','2','3','19','20')) THEN 0
        ELSE NULL
    END AS denied
FROM claims c
LEFT JOIN remittance_claims rc ON rc.claim_id = c.id
WHERE (c.frequency_code IS NULL OR c.frequency_code = '1')
  AND c.service_from_date IS NOT NULL
GROUP BY
    c.id, c.service_variant, c.claim_subtype,
    c.payer_id, c.billing_provider_id, c.rendering_provider_id,
    c.patient_id, c.service_from_date
HAVING (
    bool_or(rc.claim_status_code = '4') OR
    bool_or(rc.claim_status_code IN ('1','2','3','19','20'))
);
CREATE UNIQUE INDEX mv_claim_labels_pkey ON mv_claim_labels (claim_id);
"""


async def main():
    c = await asyncpg.connect(
        host="localhost", port=5432,
        user="postgres", password="postgres",
        database="rcm_v2_verify",
    )

    for name, sql in [
        ("audit_trigger_fn", TRIGGER_FN),
        ("derive_service_variant", DERIVE_FN),
        ("is_denied", IS_DENIED_FN),
        ("claim_lifecycle_resolved", LIFECYCLE_FN),
    ]:
        try:
            await c.execute(sql)
            print(f"  OK   compile {name}")
        except Exception as e:
            print(f"  FAIL compile {name}: {e}")

    # Functional sanity
    try:
        await c.execute(
            "CREATE TRIGGER trg_audit_claims AFTER INSERT OR UPDATE OR DELETE ON claims "
            "FOR EACH ROW EXECUTE FUNCTION audit_trigger_fn()"
        )
        print("  OK   attach audit trigger to claims")
    except Exception as e:
        print(f"  FAIL attach trigger: {e}")

    import datetime as dt
    f2 = await c.fetchval(
        "INSERT INTO edi_files(file_type,file_name,content_hash,raw_text,parser_version,created_at,updated_at) "
        "VALUES('edi_837'::file_type,'audit-test.edi','hash-audit','x','v2',now(),now()) RETURNING id"
    )
    cid = await c.fetchval(
        "INSERT INTO claims(edi_file_id,service_variant,claim_subtype,claim_number,total_charge_amount,"
        "claim_status,submission_date,service_from_date,created_at,updated_at) "
        "VALUES($1,'837P','healthcare','AUDIT-1',100,'submitted'::claim_status,current_date,current_date,now(),now()) RETURNING id",
        f2,
    )
    audits = await c.fetchval(
        "SELECT count(*) FROM audit_log WHERE table_name='claims' AND row_id=$1", cid
    )
    print(f"  AUDIT TEST: claim id={cid}, audit_log rows={audits} (expect 1)")

    # is_denied check
    is_d1 = await c.fetchval("SELECT is_denied($1)", cid)
    print(f"  is_denied (no remit yet): {is_d1} (expect False)")
    await c.execute(
        "INSERT INTO remittance_claims(claim_id,claim_status_code,billed_amount,paid_amount,"
        "created_at,updated_at) VALUES($1,'4',100,0,now(),now())", cid
    )
    is_d2 = await c.fetchval("SELECT is_denied($1)", cid)
    print(f"  is_denied (CLP02='4'): {is_d2} (expect True)")

    # derive_service_variant
    await c.execute(
        "INSERT INTO raw_segments(created_at,edi_file_id,segment_name,segment_position,"
        "raw_segment_text,handler_status) VALUES(now(),$1,'GS',0,"
        "'GS*HC*SENDER*RECV*20260605*1200*123*X*005010X222A1','handled'::handler_status)", f2
    )
    sv = await c.fetchval("SELECT derive_service_variant($1)", f2)
    print(f"  derive_service_variant: {sv} (expect 837P)")

    # MV — create + refresh
    try:
        await c.execute(MV_CLAIM_LABELS)
        print("  OK   mv_claim_labels created")
        await c.execute("REFRESH MATERIALIZED VIEW mv_claim_labels")
        mv_rows = await c.fetchval("SELECT count(*) FROM mv_claim_labels")
        print(f"  mv_claim_labels rows after refresh: {mv_rows} (expect 1, the denied claim)")
        await c.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_claim_labels")
        print("  OK   mv_claim_labels REFRESH CONCURRENTLY works (unique index present)")
    except Exception as e:
        print(f"  FAIL mv_claim_labels: {e}")

    # cleanup
    await c.execute("DROP TRIGGER IF EXISTS trg_audit_claims ON claims")
    await c.execute("DELETE FROM edi_files WHERE id=$1", f2)
    await c.execute("DROP MATERIALIZED VIEW IF EXISTS mv_claim_labels")
    await c.execute("DROP FUNCTION IF EXISTS claim_lifecycle_resolved(BIGINT)")
    await c.execute("DROP FUNCTION IF EXISTS is_denied(BIGINT)")
    await c.execute("DROP FUNCTION IF EXISTS derive_service_variant(BIGINT)")
    await c.execute("DROP FUNCTION IF EXISTS audit_trigger_fn()")
    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
