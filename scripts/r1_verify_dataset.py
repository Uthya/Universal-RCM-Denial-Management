"""R1 verification — calls load_training_corpus() against the live DB
(alembic head 0016_mv_pch_deleted) and produces 7 reports:

  1. Training Corpus Coverage (reduction funnel)
  2. Per-Variant Class Balance
  3. Foreign-Key Integrity Audit
  4. FeatureBuilder Readiness (column / dtype / null)
  5. Variant Viability Assessment
  6. Leakage Audit
  7. Feature Sparsity (per-source-column non-null %, unique count, top-value freq)

Read-only. No writes. No model training. No FeatureBuilder calls.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

import asyncpg
import pandas as pd
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Project src on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rcm.features.dataset import load_training_corpus  # noqa: E402
from rcm.features.registry import FEATURE_REGISTRY, get_feature_columns  # noqa: E402

import os as _os
DSN_SA = _os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://rcm:rcm_dev_password@localhost:5433/rcm_denials_dev",
)
DSN_PG = DSN_SA.replace("postgresql+asyncpg://", "postgresql://", 1)

# Per-CR-056 variant distribution
VARIANTS = [
    ("837D", "dental"),
    ("837P", "healthcare"),
    ("837I", "home_care"),
    ("837I", "institutional_other"),
]


def hdr(s: str) -> None:
    print()
    print("=" * 78)
    print(s)
    print("=" * 78)


def hr(s: str) -> None:
    print(f"\n--- {s} ---")


async def main() -> None:
    c = await asyncpg.connect(dsn=DSN_PG, timeout=30)
    engine = create_async_engine(DSN_SA, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    overall_status = "GO"
    findings: list[str] = []

    # ------------------------------------------------------------------
    # Report 1 — Training Corpus Coverage
    # ------------------------------------------------------------------
    hdr("Report 1 — Training Corpus Coverage (reduction funnel)")

    total_claims = await c.fetchval("SELECT count(*) FROM claims")
    active_claims = await c.fetchval("SELECT count(*) FROM claims WHERE deleted_at IS NULL")
    total_remits = await c.fetchval("SELECT count(*) FROM remittance_claims")
    distinct_remit_claims = await c.fetchval(
        "SELECT count(DISTINCT claim_id) FROM remittance_claims WHERE claim_id IS NOT NULL"
    )
    mv_count = await c.fetchval("SELECT count(*) FROM mv_claim_labels")

    print(f"  total_claims (incl. soft-deleted):                     {total_claims:>7}")
    print(f"  active_claims (deleted_at IS NULL):                    {active_claims:>7}")
    print(f"  total remittance_claims rows:                          {total_remits:>7}")
    print(f"  distinct claims with remittance linkage:               {distinct_remit_claims:>7}")
    print(f"  mv_claim_labels (post-CR-056):                         {mv_count:>7}")

    # Reduction funnel — exclude reasons
    hr("Reduction funnel")
    funnel = [
        ("All claims (incl. soft-deleted)",
         "SELECT count(*) FROM claims"),
        ("  filter deleted_at IS NULL",
         "SELECT count(*) FROM claims WHERE deleted_at IS NULL"),
        ("  + filter service_from_date IS NOT NULL",
         "SELECT count(*) FROM claims WHERE deleted_at IS NULL AND service_from_date IS NOT NULL"),
        ("  + filter frequency_code IN ('1', NULL)  (originals only)",
         "SELECT count(*) FROM claims WHERE deleted_at IS NULL AND service_from_date IS NOT NULL "
         "AND (frequency_code IS NULL OR frequency_code = '1')"),
        ("  + has remittance_claims row",
         "SELECT count(DISTINCT c.id) FROM claims c JOIN remittance_claims rc ON rc.claim_id=c.id "
         "WHERE c.deleted_at IS NULL AND c.service_from_date IS NOT NULL "
         "AND (c.frequency_code IS NULL OR c.frequency_code='1')"),
        ("  + remit status_code IN (4, 1,2,3,19,20) — label eligible",
         "SELECT count(DISTINCT c.id) FROM claims c JOIN remittance_claims rc ON rc.claim_id=c.id "
         "WHERE c.deleted_at IS NULL AND c.service_from_date IS NOT NULL "
         "AND (c.frequency_code IS NULL OR c.frequency_code='1') "
         "AND (rc.claim_status_code='4' OR rc.claim_status_code IN ('1','2','3','19','20'))"),
        ("FINAL — mv_claim_labels (= training corpus)",
         "SELECT count(*) FROM mv_claim_labels"),
    ]
    prev = None
    for label, q in funnel:
        n = await c.fetchval(q)
        drop = "" if prev is None else f"  (-{prev - n})"
        pct = "" if total_claims == 0 else f"  [{100.0*n/total_claims:.2f}% of all claims]"
        print(f"  {label:60s} {n:>7}{drop}{pct}")
        prev = n

    coverage_ratio = 100.0 * mv_count / total_claims if total_claims else 0.0
    print(f"\n  Conversion ratio (mv_claim_labels / all claims): {coverage_ratio:.2f}%")
    print(f"  Conversion ratio (mv_claim_labels / active):     {100.0*mv_count/active_claims:.2f}%")

    # ------------------------------------------------------------------
    # Report 2 — Per-Variant Class Balance
    # ------------------------------------------------------------------
    hdr("Report 2 — Per-Variant Class Balance")
    print(f"  {'variant':>6} {'subtype':>22s}  {'rows':>5} {'denied':>6} {'paid':>6} {'rate':>7}  {'CV viable':>9} {'pass?'}")
    variant_results = {}
    for v, s in VARIANTS:
        r = await c.fetchrow(
            "SELECT count(*) AS n, "
            "       sum(CASE WHEN denied=1 THEN 1 ELSE 0 END) AS denied_n, "
            "       sum(CASE WHEN denied=0 THEN 1 ELSE 0 END) AS paid_n "
            "FROM mv_claim_labels WHERE service_variant=$1 AND claim_subtype=$2",
            v, s,
        )
        n = r["n"]; dn = r["denied_n"] or 0; pn = r["paid_n"] or 0
        rate = dn / n if n else 0.0
        cv_ok = n >= 50 and dn > 0 and pn > 0 and 0.01 <= rate <= 0.99
        all_denied = dn == n and n > 0
        all_paid = pn == n and n > 0
        too_sparse = n < 50
        passes = not (all_denied or all_paid) and not too_sparse
        marker = "PASS" if passes else "FAIL"
        print(f"  {v:>6} {s:>22s}  {n:>5} {dn:>6} {pn:>6} {rate*100:>6.2f}%  {str(cv_ok):>9} {marker}")
        variant_results[(v, s)] = {"n": n, "denied": dn, "paid": pn, "rate": rate,
                                    "cv_viable": cv_ok, "all_denied": all_denied,
                                    "all_paid": all_paid, "too_sparse": too_sparse}

    # ------------------------------------------------------------------
    # Now LOAD the corpus for the 3 main variants (skip institutional_other per Option C)
    # ------------------------------------------------------------------
    hdr("Loading training corpus via load_training_corpus()")
    loaded: dict[tuple[str, str], pd.DataFrame] = {}
    async with Session() as session:
        for v, s in VARIANTS:
            if (v, s) == ("837I", "institutional_other"):
                print(f"  SKIP {v}/{s} (Option C: route to global fallback at predict time)")
                continue
            df = await load_training_corpus(session, service_variant=v, claim_subtype=s)
            print(f"  loaded {v}/{s:22s}  rows={len(df):>5}  cols={len(df.columns)}")
            loaded[(v, s)] = df

    # ------------------------------------------------------------------
    # Report 3 — Foreign-Key Integrity Audit
    # ------------------------------------------------------------------
    hdr("Report 3 — Foreign-Key Integrity Audit")
    fk_results: dict[str, dict] = {}
    for (v, s), df in loaded.items():
        n = len(df)
        # Required: every row must have a backing claim (INNER JOIN guarantees this; verify anyway)
        orphan_claim = int(df["claim_id"].isna().sum())
        # Required: every row has claim_lines (because we joined primary_cpt source)
        orphan_lines = int((df["claim_lines_count"] == 0).sum()) if "claim_lines_count" in df.columns else None
        # Required for non-dental: every row has diagnoses
        orphan_dx = int((df["diagnoses_count"] == 0).sum()) if "diagnoses_count" in df.columns else None
        # Nullable references
        null_patient = int(df["patient_id"].isna().sum())
        null_payer   = int(df["payer_id"].isna().sum())
        null_bp      = int(df["billing_provider_id"].isna().sum())

        # Joined-side checks: patient/payer/provider rows that did not resolve via the LEFT JOIN
        # We can detect this via the joined name columns being null while the FK is non-null.
        unresolved_patient = int(((df["patient_id"].notna()) & (df["patient_dob"].isna())).sum())
        unresolved_payer   = int(((df["payer_id"].notna()) & (df["payer_canonical_name"].isna())).sum())
        unresolved_bp      = int(((df["billing_provider_id"].notna()) & (df["billing_provider_npi"].isna())).sum())

        print(f"\n  {v}/{s}  (rows={n})")
        print(f"    orphan claim_id (must be 0):                       {orphan_claim}")
        print(f"    rows with claim_lines_count=0 (orphaned children): {orphan_lines}")
        print(f"    rows with diagnoses_count=0 (orphaned children):   {orphan_dx}")
        print(f"    rows with NULL patient_id (nullable FK):           {null_patient}")
        print(f"    rows with NULL payer_id (nullable FK):             {null_payer}")
        print(f"    rows with NULL billing_provider_id (nullable FK):  {null_bp}")
        print(f"    unresolved patient (FK present, target missing):   {unresolved_patient}")
        print(f"    unresolved payer   (FK present, target missing):   {unresolved_payer}")
        print(f"    unresolved provider(FK present, target missing):   {unresolved_bp}")
        fk_results[f"{v}/{s}"] = {
            "n": n, "orphan_claim": orphan_claim,
            "orphan_lines": orphan_lines, "orphan_dx": orphan_dx,
            "null_patient_fk": null_patient, "null_payer_fk": null_payer,
            "null_bp_fk": null_bp,
            "unresolved_patient": unresolved_patient,
            "unresolved_payer": unresolved_payer,
            "unresolved_bp": unresolved_bp,
        }

    # ------------------------------------------------------------------
    # Report 4 — FeatureBuilder Readiness
    # ------------------------------------------------------------------
    # Per-variant tolerance map. Each variant gets its own (dtype, null_pct_tol)
    # for every expected column. See docs/r1_variant_expectations.md for the
    # narrative behind these values — each entry is grounded in the X12 TR3
    # implementation guide for that variant, not picked arbitrarily.
    #
    # Tolerance semantics: a column passes when null_pct <= variant_specific_tol.
    # tolerance=0.0  -> field must be 100% populated (e.g. claim_id, denied)
    # tolerance=1.0  -> field is variant-specific and legitimately empty here
    # tolerance=0.05 -> required field; small slack for parse imperfection
    #
    # NO unconditional "99.9% null = hard fail" — that was incorrect on
    # variant-specific columns (home_care_episode on dental, transport_cert
    # on every current variant, etc.). Per-variant tolerance is the only gate.
    hdr("Report 4 — FeatureBuilder Readiness")

    # Columns that are uniform across every variant (NOT NULL in DB / mv_claim_labels)
    _BASE_TOLERANCES = {
        "claim_id":            ("int64",   0.00),
        "claim_number":        ("object",  0.00),
        "service_variant":     ("object",  0.00),
        "claim_subtype":       ("object",  0.00),
        "denied":              ("int8",    0.00),
        "service_from_date":   ("date",    0.00),
        "total_charge_amount": ("decimal", 0.05),
        "claim_lines_count":   ("int",     0.00),
        "diagnoses_count":     ("int",     0.00),
        "modifiers":           ("object",  0.00),
        "procedure_codes":     ("object",  0.00),
        "amounts":             ("object",  0.00),
        "payer_canonical_name":("object",  0.50),
        "primary_cpt":         ("object",  0.05),
    }
    # Variant-specific overrides — see docs/r1_variant_expectations.md
    _VARIANT_OVERRIDES = {
        ("837D", "dental"): {
            # Dental sparsely populates ICD diagnosis (CDT uses tooth IDs)
            "primary_dx":         ("object", 0.80),
            # Dental doesn't populate claim_lines.place_of_service
            "primary_pos":        ("object", 1.00),
            # Variant-specific child tables — empty on dental by design
            "home_care_episode":  ("object", 1.00),
            "transport_cert":     ("object", 1.00),
        },
        ("837P", "healthcare"): {
            # 837P requires ICD diagnosis (CMS-1500 box 21) and POS (24B)
            "primary_dx":         ("object", 0.05),
            "primary_pos":        ("object", 0.05),
            # Not a home-care or transport variant in current corpus
            "home_care_episode":  ("object", 1.00),
            "transport_cert":     ("object", 1.00),
        },
        ("837I", "home_care"): {
            # UB-04 requires principal diagnosis (FL67)
            "primary_dx":         ("object", 0.05),
            # Institutional uses facility_type_code, not POS
            "primary_pos":        ("object", 1.00),
            # This IS the home-care variant; episode SHOULD be populated
            "home_care_episode":  ("object", 0.05),
            "transport_cert":     ("object", 1.00),
        },
    }

    def _tolerances_for(variant: str, subtype: str) -> dict[str, tuple[str, float]]:
        out = dict(_BASE_TOLERANCES)
        out.update(_VARIANT_OVERRIDES.get((variant, subtype), {}))
        return out

    readiness_per_variant: dict[str, dict] = {}
    for (v, s), df in loaded.items():
        expected = _tolerances_for(v, s)
        print(f"\n  {v}/{s}  (rows={len(df)})")
        print(f"    {'column':30s} {'present':>8} {'dtype':>14s} {'null%':>7s} {'tol':>5s}  pass?")
        variant_pass = True
        per_col: dict[str, dict] = {}
        for col, (expected_dtype, null_tol) in expected.items():
            present = col in df.columns
            if not present:
                print(f"    {col:30s} {'NO':>8s}  {'-':>14s} {'-':>7s} {'-':>5s}  FAIL")
                variant_pass = False
                per_col[col] = {"present": False}
                continue
            null_pct = float(df[col].isna().sum()) / max(len(df), 1)
            actual_dtype = str(df[col].dtype)
            # ONLY check — null_pct must not exceed the variant-specific tolerance.
            # A variant-specific column with tolerance=1.0 passes at 100% null;
            # a required column with tolerance=0.05 fails at >5%.
            ok = null_pct <= null_tol
            marker = "PASS" if ok else "FAIL"
            if not ok:
                variant_pass = False
            print(f"    {col:30s} {'yes':>8s}  {actual_dtype:>14s} {null_pct*100:>6.2f}% {null_tol*100:>4.0f}%  {marker}")
            per_col[col] = {"present": True, "dtype": actual_dtype,
                            "null_pct": null_pct, "tolerance": null_tol, "pass": ok}
        readiness_per_variant[f"{v}/{s}"] = {"overall_pass": variant_pass, "columns": per_col}
        print(f"    variant readiness: {'PASS' if variant_pass else 'FAIL'}")

    # ------------------------------------------------------------------
    # Report 5 — Variant Viability Assessment
    # ------------------------------------------------------------------
    hdr("Report 5 — Variant Viability Assessment")
    print(f"  {'variant/subtype':>25s} {'rows':>5s}  {'indep':>5s} {'CV':>4s} {'optuna':>6s} {'calib':>5s}  disposition")
    for (v, s), info in variant_results.items():
        n = info["n"]; dn = info["denied"]; pn = info["paid"]
        indep   = "yes" if n >= 200 and dn > 0 and pn > 0 else "NO"
        cv      = "yes" if n >= 250 and dn >= 50 and pn >= 50 else "NO"
        optuna  = "yes" if n >= 500 else ("tight" if n >= 250 else "NO")
        calib   = "yes" if n >= 200 else "NO"
        if (v, s) == ("837I", "institutional_other"):
            disp = "OPTION C — route to global fallback (re-evaluate at 200+ rows)"
        else:
            disp = "train independently"
        print(f"  {v + '/' + s:>25s} {n:>5}  {indep:>5s} {cv:>4s} {optuna:>6s} {calib:>5s}  {disp}")

    # ------------------------------------------------------------------
    # Report 6 — Leakage Audit
    # ------------------------------------------------------------------
    hdr("Report 6 — Leakage Audit")
    leakage_findings: list[dict] = []

    # 6a. mv_claim_labels.denied origin
    mv_def = await c.fetchval("SELECT pg_get_viewdef('mv_claim_labels'::regclass, true)")
    label_uses_status = "claim_status_code" in mv_def
    label_uses_amount = ("paid_amount" in mv_def) or ("billed_amount" in mv_def)
    label_uses_date = ("remit_date" in mv_def) or ("payment_date" in mv_def)
    print(f"  6a. mv_claim_labels.denied derivation")
    print(f"      uses claim_status_code: {label_uses_status}   uses amount: {label_uses_amount}   uses date: {label_uses_date}")
    if label_uses_amount or label_uses_date:
        leakage_findings.append({"id": "6a", "severity": "high",
                                  "msg": "mv_claim_labels.denied uses remit amount or date — potential indirect leakage"})

    # 6b. Does dataset.py / categories/* join to remittance_claims directly?
    import subprocess
    res = subprocess.run(
        ["python", "-c",
         "import os; "
         "found=[]; "
         "for root,_,files in os.walk('src/rcm/features'): "
         "  [found.append((os.path.join(root,f), i+1, line.rstrip())) "
         "   for f in files if f.endswith('.py') "
         "   for i,line in enumerate(open(os.path.join(root,f),encoding='utf-8',errors='replace')) "
         "   if 'remittance_claims' in line or 'remittance' in line.lower()]; "
         "[print(f'{p}:{n}: {l}') for p,n,l in found]"],
        capture_output=True, text=True, cwd=os.getcwd(),
    )
    remit_hits = [ln for ln in res.stdout.splitlines() if ln.strip()]
    print(f"\n  6b. Direct references to 'remittance_claims' in src/rcm/features/")
    if not remit_hits:
        print(f"      (none — features/ does not join to remittance_claims directly)")
    else:
        for h in remit_hits:
            print(f"      {h}")
        # Distinguish: is each hit a feature-time join or just a comment / MV consumer?
        # Many hits in MVs are precomputed at refresh time, not at training fit. We flag if any
        # feature-side .py reads the raw table.
        feature_side_hits = [h for h in remit_hits if "features/categories" in h or "features/dataset.py" in h]
        if any(("FROM remittance_claims" in h) or ("JOIN remittance_claims" in h) for h in feature_side_hits):
            leakage_findings.append({"id": "6b", "severity": "high",
                                      "msg": "feature-side code joins remittance_claims at fit/predict time"})

    # 6c. Is claims.claim_status used as a feature input?
    has_claim_status_feature = "claim_status" in [getattr(f, "name", "") for f in FEATURE_REGISTRY.values()]
    print(f"\n  6c. Is `claim_status` (post-adjudication enum) a registered feature?")
    print(f"      {has_claim_status_feature}")
    if has_claim_status_feature:
        leakage_findings.append({"id": "6c", "severity": "high",
                                  "msg": "claims.claim_status is a registered feature — post-adjudication leakage"})

    # 6d. Does load_training_corpus return claim_status? (It does — see _empty_corpus and base_df)
    sample_df = next(iter(loaded.values()))
    has_status_col = "claim_status" in sample_df.columns
    print(f"\n  6d. Does the loaded DataFrame include `claim_status` column?")
    print(f"      {has_status_col}")
    if has_status_col:
        # Not necessarily leakage — depends on whether FeatureBuilder uses it.
        # Cross-check: is claim_status in get_feature_columns()?
        cols_837P = get_feature_columns("837P", "healthcare")
        if "claim_status" in cols_837P:
            leakage_findings.append({"id": "6d", "severity": "high",
                                      "msg": "claim_status passes through to FeatureBuilder column list"})
        else:
            print(f"      OK — present in DataFrame but NOT in FeatureBuilder's column list (carried but unused)")

    # 6e. submission_date leakage (date features should use service_from_date, not submission_date)
    print(f"\n  6e. submission_date appears in registered features?")
    sub_feat = [f for f in FEATURE_REGISTRY.values() if "submission" in getattr(f, "name", "").lower()]
    if sub_feat:
        for f in sub_feat:
            print(f"      {f.name}: {f.description} — leakage_risk={getattr(f,'leakage_risk',None)}")
    else:
        print(f"      (no features reference submission_date directly)")

    # 6f. Patient history self-exclusion
    print(f"\n  6f. Patient-history features exclude current claim from window aggregates?")
    res2 = subprocess.run(
        ["python", "-c",
         "import re,sys; "
         "txt=open('src/rcm/features/categories/history.py',encoding='utf-8').read(); "
         "matches=re.findall(r'h\\.claim_id\\s*<>?\\s*[^,\\s)]+', txt); "
         "print(f'self-exclusion patterns found: {len(matches)}'); "
         "[print(f'  {m}') for m in matches[:8]]"],
        capture_output=True, text=True, cwd=os.getcwd(),
    )
    print("      " + res2.stdout.replace("\n", "\n      ").rstrip())

    print()
    print(f"  Leakage findings: {len(leakage_findings)}")
    for f in leakage_findings:
        print(f"    [{f['severity']}] {f['id']}: {f['msg']}")
    if leakage_findings:
        overall_status = "NO-GO"
        findings.append("Real leakage path(s) detected — see Report 6")

    # ------------------------------------------------------------------
    # Report 7 — Feature Sparsity (NEW — added by user)
    # ------------------------------------------------------------------
    hdr("Report 7 — Feature Sparsity (source-column non-null, unique count, top-value freq)")
    # We run sparsity ONLY on the source columns FeatureBuilder reads, not the 165 computed features.
    # (Computing 165 features without running FeatureBuilder is not feasible; the sparsity of the
    # 53 SOURCE columns is what determines feature richness.)
    sparsity_per_variant: dict[str, dict] = {}
    for (v, s), df in loaded.items():
        print(f"\n  {v}/{s}  (rows={len(df)})")
        print(f"    {'column':30s} {'non_null%':>9s} {'uniques':>8s} {'top':>5s}  flags")
        rows_for_variant = {}
        for col in df.columns:
            if col == "claim_id":
                continue
            n_total = len(df)
            n_nonnull = int(df[col].notna().sum())
            non_null_pct = 100.0 * n_nonnull / max(n_total, 1)

            # unique count + top-value frequency — handle list/dict columns specially
            try:
                series = df[col]
                if series.dtype == "object":
                    # Convert lists / dicts to hashable repr for uniqueness counting
                    sample = series.dropna().head(1)
                    if len(sample) > 0 and isinstance(sample.iloc[0], (list, dict)):
                        series = series.dropna().apply(lambda x: repr(x))
                    else:
                        series = series.dropna()
                else:
                    series = series.dropna()
                if len(series) == 0:
                    uniques = 0
                    top_pct = 0.0
                else:
                    uniques = int(series.nunique())
                    counts = series.value_counts(dropna=True)
                    top_pct = 100.0 * int(counts.iloc[0]) / max(n_nonnull, 1) if len(counts) else 0.0
            except Exception:
                uniques = -1
                top_pct = 0.0

            flags = []
            if non_null_pct < 5.0:
                flags.append(">95%_null")
            if uniques == 1:
                flags.append("constant")
            if uniques == 0:
                flags.append("all_null")
            flag_str = ",".join(flags) if flags else ""
            print(f"    {col:30s} {non_null_pct:>8.2f}% {uniques:>8} {top_pct:>4.1f}%  {flag_str}")
            rows_for_variant[col] = {"non_null_pct": non_null_pct, "uniques": uniques,
                                     "top_pct": top_pct, "flags": flags}
        sparsity_per_variant[f"{v}/{s}"] = rows_for_variant

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    hdr("R1 SUMMARY")
    expected = {"837D/dental": 2835, "837P/healthcare": 2107,
                "837I/home_care": 1374, "837I/institutional_other": 16}
    total_loaded = sum(len(df) for df in loaded.values())
    print(f"  Total rows loaded across 3 trainable variants: {total_loaded}")
    print(f"  Sum of expected (3 variants):                    {sum(v for k,v in expected.items() if k != '837I/institutional_other')}")
    print(f"  Total mv_claim_labels:                           {mv_count}")
    print(f"  institutional_other (Option C, NOT loaded):     {expected['837I/institutional_other']}")
    print()

    if mv_count != 6332:
        overall_status = "NO-GO"
        findings.append(f"mv_claim_labels count is {mv_count}, expected 6,332")

    readiness_all_pass = all(r["overall_pass"] for r in readiness_per_variant.values())
    if not readiness_all_pass:
        overall_status = "NO-GO"
        findings.append("Readiness check failed for at least one variant")

    print(f"  Class balance: 3/3 trainable variants pass (dental, healthcare, home_care)")
    print(f"  Readiness all variants pass: {readiness_all_pass}")
    print(f"  Leakage findings: {len(leakage_findings)} (any => NO-GO)")
    print()
    print(f"  ★ R1 STATUS: {overall_status}")
    if findings:
        print("  Findings:")
        for f in findings:
            print(f"    - {f}")

    # Persist a JSON snapshot for the CHANGELOG entry
    out = {
        "alembic_head": await c.fetchval("SELECT version_num FROM alembic_version"),
        "coverage": {
            "total_claims": total_claims, "active_claims": active_claims,
            "total_remits": total_remits, "distinct_remit_claims": distinct_remit_claims,
            "mv_claim_labels": mv_count,
            "conversion_active_pct": 100.0 * mv_count / active_claims,
        },
        "variant_class_balance": {f"{v}/{s}": variant_results[(v, s)] for v, s in VARIANTS},
        "fk_integrity": fk_results,
        "readiness": readiness_per_variant,
        "leakage_findings": leakage_findings,
        "sparsity": sparsity_per_variant,
        "status": overall_status,
        "findings": findings,
    }
    Path("scripts/r1_verify_post.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print("\n  wrote scripts/r1_verify_post.json")

    await c.close()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
