"""Smoke test: every mapped class registers with Base.metadata.

If a model file has a syntax error or a missing FK target this test will
fail loudly — far better than discovering it during alembic upgrade.
"""

from __future__ import annotations


def test_all_models_register_with_metadata():
    from rcm.core.database import Base
    import rcm.models  # noqa: F401  registers all mappers

    table_names = set(Base.metadata.tables.keys())

    expected = {
        # Domain A
        "edi_files", "raw_segments", "parse_events",
        # Domain B
        "claims", "claim_lines", "diagnoses",
        "patients", "providers", "subscribers",
        # Domain C
        "claim_certifications", "claim_amounts", "claim_attachments",
        "home_care_episodes", "transport_certifications",
        # Domain D
        "remittance_claims", "adjustments", "remark_codes",
        # Domain E
        "claim_lifecycles", "appeals",
        # Domain F
        "code_masters", "payers", "procedure_codes", "diagnosis_codes",
        "payer_policies", "cms_knowledge", "ncci_edits", "cms_lcd_coverage",
        # Domain G
        "prediction_log", "model_training_metrics",
        "feature_snapshots", "model_artifacts",
        # Domain H
        "knowledge_documents", "knowledge_chunks",
        "claim_embeddings", "correction_examples", "rag_generations",
        # Domain I
        "tenants", "users", "audit_log", "request_log", "background_jobs",
    }
    missing = expected - table_names
    assert not missing, f"Tables missing from metadata: {missing}"


def test_enum_values_are_canonical():
    """ENUM values are the contract with PG — never accidentally rename them."""
    from rcm.core.enums import (
        ClaimStatus,
        FileType,
        HandlerStatus,
        PolicyType,
        UserRole,
    )

    assert {s.value for s in ClaimStatus} == {
        "submitted", "paid", "denied", "partially_paid", "void", "pending"
    }
    assert {f.value for f in FileType} == {"edi_837", "edi_835", "edi_277", "edi_999"}
    assert {h.value for h in HandlerStatus} == {
        "handled", "skipped_unhandled", "parse_error", "validator_dropped"
    }
    assert "referral_required" in {p.value for p in PolicyType}
    assert {r.value for r in UserRole} == {
        "viewer", "biller", "billing_admin", "system_admin"
    }
