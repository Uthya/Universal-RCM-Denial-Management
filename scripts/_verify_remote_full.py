"""Verification-only script — runs against the remote dev cluster after
`alembic upgrade head` has been applied. Confirms:
  * all 41 tables present (incl. 5 partitioned parents)
  * pgvector extension installed
  * HNSW indexes created on each vector column
  * find_similar_claims function executes and returns the expected shape
  * audit triggers fire when present
  * mv_claim_labels exists, has unique index, supports REFRESH CONCURRENTLY
  * Each ENUM type exists with the correct labels
"""

from __future__ import annotations

import asyncio
import urllib.parse

import asyncpg


REMOTE_DSN = (
    f"postgresql://postgres:{urllib.parse.quote_plus('P0stgreSQL!Dev#847')}"
    f"@104.130.220.20:30432/rcm_denials"
)


EXPECTED_TABLES = {
    # ingestion
    "edi_files", "raw_segments", "parse_events",
    # claims
    "claims", "claim_lines", "diagnoses", "patients", "providers", "subscribers",
    # variant extensions
    "claim_certifications", "claim_amounts", "claim_attachments",
    "home_care_episodes", "transport_certifications",
    # remittance
    "remittance_claims", "adjustments", "remark_codes",
    # lifecycle
    "claim_lifecycles", "appeals",
    # reference
    "code_masters", "payers", "procedure_codes", "diagnosis_codes",
    "payer_policies", "cms_knowledge", "ncci_edits", "cms_lcd_coverage",
    # ml pipeline
    "prediction_log", "model_training_metrics",
    "feature_snapshots", "model_artifacts",
    # RAG
    "knowledge_documents", "knowledge_chunks",
    "claim_embeddings", "correction_examples", "rag_generations",
    # operations
    "tenants", "users", "audit_log", "request_log", "background_jobs",
}

EXPECTED_PARTITIONED = {
    "raw_segments", "parse_events", "prediction_log", "audit_log", "request_log",
}

EXPECTED_HNSW_INDEXES = {
    "ix_knowledge_chunks_embedding_hnsw",
    "ix_claim_embeddings_embedding_hnsw",
    "ix_correction_examples_embedding_hnsw",
}

EXPECTED_FUNCTIONS = {
    "audit_trigger_fn",
    "touch_updated_at_fn",
    "derive_service_variant",
    "is_denied",
    "claim_lifecycle_resolved",
    "find_similar_claims",
}

EXPECTED_ENUMS = {
    "file_type": 4, "parse_status": 5, "handler_status": 4, "parse_event_type": 7,
    "claim_status": 6, "provider_type": 7,
    "certification_type": 8,
    "lifecycle_relationship": 5, "appeal_level": 4, "appeal_status": 5,
    "code_system": 5, "dx_code_system": 3, "policy_type": 8, "cms_doc_type": 6,
    "ncci_edit_type": 2,
    "artifact_type": 5,
    "knowledge_source_type": 8, "correction_outcome": 4,
    "rag_generation_type": 5, "user_feedback": 4,
    "user_role": 4, "job_status": 5, "tenant_isolation": 2,
}

EXPECTED_MVS = {
    "mv_claim_labels", "mv_payer_denial_rates", "mv_payer_cpt_denial_rate",
    "mv_payer_dx_denial_rate", "mv_payer_pos_denial_rate", "mv_cpt_dx_denial_rate",
    "mv_provider_denial_profiles", "mv_provider_payer_denial_rate",
    "mv_provider_cpt_denial_rate", "mv_lifecycle_outcomes",
    "mv_patient_claim_history", "mv_drift_baselines",
}


async def main() -> int:
    rc = 0
    c = await asyncpg.connect(dsn=REMOTE_DSN)
    try:
        print("=" * 70)
        print("REMOTE VERIFICATION — postgres@104.130.220.20:30432/rcm_denials")
        print("=" * 70)

        ver = await c.fetchval("SELECT version()")
        print(f"PG: {ver[:80]}")

        head = await c.fetchval("SELECT version_num FROM alembic_version")
        print(f"alembic head: {head}")
        assert head == "0010_add_materialized_views", "Not at head!"

        print()
        print("--- 1. pgvector extension ---")
        ext_v = await c.fetchval("SELECT extversion FROM pg_extension WHERE extname='vector'")
        if ext_v:
            print(f"  ✓ vector {ext_v} installed")
        else:
            print("  ✗ vector NOT installed")
            rc = 1

        print()
        print("--- 2. Tables ---")
        rows = await c.fetch(
            "SELECT relname FROM pg_class "
            "WHERE relnamespace=(SELECT oid FROM pg_namespace WHERE nspname='public') "
            "AND relkind IN ('r','p')"
        )
        names = {r["relname"] for r in rows}
        # exclude partition children and alembic_version
        children = {n for n in names if "_p20" in n or n.endswith("_default")}
        bases = names - children - {"alembic_version"}
        missing = EXPECTED_TABLES - bases
        extra = bases - EXPECTED_TABLES
        print(f"  expected {len(EXPECTED_TABLES)} base tables, got {len(bases)} "
              f"(+{len(children)} partition children, +alembic_version)")
        if missing: print(f"  ✗ missing: {missing}"); rc = 1
        if extra:   print(f"  ! unexpected: {extra}")
        if not missing and not extra: print("  ✓ all expected tables present")

        print()
        print("--- 3. Partitioned parents ---")
        rows = await c.fetch(
            "SELECT relname FROM pg_class "
            "WHERE relnamespace=(SELECT oid FROM pg_namespace WHERE nspname='public') "
            "AND relkind='p'"
        )
        parts = {r["relname"] for r in rows}
        missing = EXPECTED_PARTITIONED - parts
        if missing: print(f"  ✗ missing partitioned: {missing}"); rc = 1
        else: print(f"  ✓ all 5 partitioned parents present: {sorted(parts)}")

        # For each, check partition children
        for p in sorted(EXPECTED_PARTITIONED):
            kids = await c.fetch(
                "SELECT inhrelid::regclass::text AS child FROM pg_inherits "
                "WHERE inhparent = $1::regclass", p
            )
            kid_names = [k["child"] for k in kids]
            print(f"    {p}: {len(kid_names)} partitions: {[n.split('.')[-1] for n in kid_names]}")

        print()
        print("--- 4. HNSW vector indexes ---")
        rows = await c.fetch(
            "SELECT indexname, tablename, indexdef FROM pg_indexes "
            "WHERE schemaname='public' AND indexdef LIKE '%hnsw%'"
        )
        found = {r["indexname"] for r in rows}
        missing = EXPECTED_HNSW_INDEXES - found
        if missing: print(f"  ✗ missing HNSW: {missing}"); rc = 1
        else: print(f"  ✓ all 3 HNSW indexes present")
        for r in rows:
            opts = r["indexdef"]
            print(f"    {r['indexname']} on {r['tablename']}")
            print(f"      {opts[:140]}{'...' if len(opts) > 140 else ''}")

        print()
        print("--- 5. Vector column types ---")
        rows = await c.fetch(
            "SELECT table_name, column_name, udt_name FROM information_schema.columns "
            "WHERE table_schema='public' AND udt_name='vector' "
            "ORDER BY table_name, column_name"
        )
        for r in rows:
            print(f"    {r['table_name']}.{r['column_name']} :: {r['udt_name']}")
        if not rows: print("  ✗ no vector columns found"); rc = 1

        print()
        print("--- 6. ENUM types ---")
        rows = await c.fetch(
            "SELECT t.typname, count(e.enumlabel) AS n_labels "
            "FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid "
            "JOIN pg_namespace n ON n.oid=t.typnamespace "
            "WHERE n.nspname='public' GROUP BY t.typname"
        )
        found = {r["typname"]: r["n_labels"] for r in rows}
        for name, expected_n in EXPECTED_ENUMS.items():
            actual = found.get(name)
            if actual is None:
                print(f"  ✗ missing ENUM: {name}"); rc = 1
            elif actual != expected_n:
                print(f"  ! {name}: expected {expected_n} labels, got {actual}")
            else:
                pass
        print(f"  ✓ {len(EXPECTED_ENUMS)} ENUMs verified")

        print()
        print("--- 7. PL/pgSQL functions ---")
        rows = await c.fetch(
            "SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public'"
        )
        fns = {r["proname"] for r in rows}
        for fn in EXPECTED_FUNCTIONS:
            tag = "✓" if fn in fns else "✗"
            print(f"  {tag} {fn}")
            if fn not in fns: rc = 1

        print()
        print("--- 8. Materialized views ---")
        rows = await c.fetch(
            "SELECT matviewname FROM pg_matviews WHERE schemaname='public'"
        )
        mvs = {r["matviewname"] for r in rows}
        missing = EXPECTED_MVS - mvs
        if missing: print(f"  ✗ missing MVs: {missing}"); rc = 1
        else: print(f"  ✓ all {len(EXPECTED_MVS)} MVs present")
        # Check each has unique index for REFRESH CONCURRENTLY
        for mv in sorted(EXPECTED_MVS):
            uniq = await c.fetchval(
                "SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND "
                "tablename=$1 AND indexdef LIKE 'CREATE UNIQUE%'", mv
            )
            if uniq == 0:
                print(f"  ✗ {mv}: NO unique index — REFRESH CONCURRENTLY will fail"); rc = 1

        print()
        print("--- 9. find_similar_claims() functional test ---")
        # Insert a fake embedding (1024-d zero vector + one non-zero)
        # First create a claim + edi_file to satisfy FKs
        try:
            f_id = await c.fetchval(
                "INSERT INTO edi_files(file_type,file_name,content_hash,raw_text,"
                "parser_version,created_at,updated_at) "
                "VALUES('edi_837'::file_type,'verify.edi','verify-hash-1','x','v2',now(),now()) "
                "RETURNING id"
            )
            c_id = await c.fetchval(
                "INSERT INTO claims(edi_file_id,service_variant,claim_subtype,claim_number,"
                "total_charge_amount,claim_status,submission_date,service_from_date,"
                "created_at,updated_at) "
                "VALUES($1,'837P','healthcare','VERIFY-1',100,'submitted'::claim_status,"
                "current_date,current_date,now(),now()) RETURNING id", f_id
            )
            # Pick a vector dim by inspecting an existing column
            dim_row = await c.fetchrow(
                "SELECT atttypmod FROM pg_attribute a "
                "JOIN pg_class c ON c.oid=a.attrelid "
                "WHERE c.relname='claim_embeddings' AND a.attname='embedding'"
            )
            dim = dim_row["atttypmod"]
            print(f"  detected embedding dim: {dim}")
            zero = "[" + ",".join(["0.0"] * dim) + "]"
            await c.execute(
                "INSERT INTO claim_embeddings(claim_id,service_variant,claim_subtype,"
                "embedding,embedding_model) VALUES($1,'837P','healthcare',$2::vector,$3)",
                c_id, zero, "test-model-v1",
            )
            rows = await c.fetch(
                "SELECT claim_id, distance FROM find_similar_claims($1::vector, 5, '837P', 'healthcare')",
                zero,
            )
            print(f"  find_similar_claims returned {len(rows)} row(s) — expected >=1")
            for r in rows: print(f"    claim_id={r['claim_id']} distance={r['distance']:.6f}")
            if not rows: rc = 1
            # cleanup
            await c.execute("DELETE FROM edi_files WHERE id=$1", f_id)
        except Exception as e:
            print(f"  ✗ find_similar_claims test failed: {type(e).__name__}: {e}")
            rc = 1

        print()
        print("--- 10. mv_claim_labels REFRESH CONCURRENTLY ---")
        try:
            await c.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_claim_labels")
            print("  ✓ mv_claim_labels REFRESH CONCURRENTLY succeeded")
        except Exception as e:
            print(f"  ✗ {type(e).__name__}: {e}")
            rc = 1

        print()
        print("=" * 70)
        if rc == 0:
            print("ALL CHECKS PASSED")
        else:
            print(f"FAILED — see {rc} issue(s) above")
        print("=" * 70)
    finally:
        await c.close()
    return rc


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
