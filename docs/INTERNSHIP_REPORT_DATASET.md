# Internship Report — Preparation Dataset

A working knowledge base built from `docs/PROJECT_KNOWLEDGE_DUMP.md` for use while preparing the Summer Internship Report, Internship Evaluation, Project Viva, Technical Presentation, Project Synopsis, Resume Project Section, and any research / publication-style write-up.

> **This is NOT the final report.** It is dataset + drafting material. Numbers and CRs are sourced from the repository state on 2026-06-24 and `CHANGELOG.md` (9,134 lines, CR-001 → CR-126C).

> **Read-only:** no source code, artifacts, migrations, or models have been modified to produce this document.

---

## Table of contents

1. Personal contribution analysis
2. Business value
3. System walkthrough (Patient → Analytics)
4. Database explanation
5. Machine learning explanation
6. Feature engineering explanation
7. Interview & viva questions (250 Q&A)
8. Presentation preparation (2 / 5 / 10 / 20 minutes)
9. Achievements (quantified)
10. Report assets (what to capture)
11. Gaps (what is still missing)

---

# SECTION 1 — Personal contribution analysis

> **Honesty note:** The Git CHANGELOG records system changes (CRs), not authorship. The active branch `v1/v1_featureengineering` and recent commits (*"Threshold and calibration tuning for 837I and 837D"*, *"Added hyperparameter tuning for 837I and implemented feature engineering designed"*) place the intern squarely on the **feature-engineering + ML tuning + calibration** track. The table below lists the *most likely intern-led CRs* given that branch context — confirm with mentor before using percentages in the final report.

## 1.1 Contribution framework — modules

| Module | Built / enhanced | Most likely intern role |
| --- | --- | --- |
| `src/rcm/features/registry.py` | Built / extended | Designed FeatureSpec list, per-variant column tuples, **M1 validator** (strict column-order check). |
| `src/rcm/features/builder.py` | Built / extended | Orchestrated `fit_transform` and `transform` flows; reference-data graceful degradation. |
| `src/rcm/features/encoders.py` | Built | `LeakageSafeTargetEncoder` (5-fold OOF train, deterministic predict) + `RarityState`. |
| `src/rcm/features/dataset.py` | Built / extended | `load_training_corpus`; CR-071 denial propagation. |
| `src/rcm/features/categories/*.py` | Built (14 modules) | Coverage / Authorization / Clinical / Coding / Timely / Documentation / History / Provider / Joint / Base / Encoded / Rarity / Availability / Lifecycle. |
| `src/rcm/features/variants/*.py` | Built (7 + global) | Healthcare, therapy, transport, home_care, dental, specialty, institutional_other, global_fallback. |
| `src/rcm/ml/trainer.py` | Built / tuned | Stratified 70/15/15, XGBoost fit, isotonic calibration, precision-floor threshold. |
| `src/rcm/ml/predictor.py` | Built | Per-variant predictors + `booster.feature_names` hard-check. |
| `src/rcm/ml/artifacts.py` | Built | `ModelArtifactBundle` save/load contract. |
| `src/rcm/ml/reason_renderer.py` | Built / refined | SHAP → 11 buckets → variant-aware sentence + actionability-tier sort. |
| `src/rcm/ml/denial_buckets.py` | Built | CARC/RARC → bucket mapping with per-code overrides. |
| `src/rcm/ml/shadow.py` | Built | Paired-prediction logging. |
| `src/rcm/ml/simple_pipeline.py` | Built (fallback) | Fixed-vocabulary fallback model. |
| `scripts/cr079_tune.py` + `cr079_promote.py` | Built | Optuna search harness + atomic bundle promotion. |
| `src/migrations/versions/0010` … `0018` | Enhanced | Materialised views, denial-label MVs, recency MVs (mig 0017/0018). |
| Frontend (`UploadPage`, `ClaimsPage`, `ClaimDetailPage`) | Enhanced | DenialRiskCard, HighRiskList, RecommendedFixesPanel, TrainModelCard, TrainingHistoryCard. |

## 1.2 Estimated contribution percentage (template — confirm with mentor)

| Phase | Personal involvement |
| --- | --- |
| Phase 0 — Scaffolding | Low (≤ 10 %). |
| Phase 1 — DB layer | Medium (review + minor enhancements). |
| Phase 2 — Parsing | Low–medium (consumed, may have added handlers in CR-049). |
| Phase 3 — Feature engineering | **High** (primary contributor; branch is `v1/v1_featureengineering`). |
| Phase 4 — ML tuning + calibration | **High** (CR-075/079/084/091/107/112/114/120). |
| Phase 5 — Explanation layer | **High** (CR-078/078A/092/093). |
| Phase 6 — Recency + lifecycle | **High** (CR-117/118/120/120A/126). |
| Frontend | Medium (cards + reason rendering integration). |

A defensible total contribution band for viva is **35–55 %** of the post-CR-067 work, with the strongest claims on feature engineering, model calibration, threshold tuning, lifecycle features, recency features, and explanation rendering.

## 1.3 CR-level breakdown (problem / root cause / solution / complexity / impact)

Each CR below follows a five-line shape so it can be quoted directly in the report.

### CR-067 — FeatureBuilder production cutover
- **Problem:** simple_pipeline was scoring production claims; FeatureBuilder existed but was used only in shadow.
- **Root cause:** v2 was built incrementally — the FB code path matured separately from the primary inference path.
- **Solution:** Inverted prediction routing so FB became primary; simple_pipeline became shadow + fallback.
- **Technical complexity:** Medium — required artifact bundle contract, dual logging, predictor cache.
- **Business impact:** Unlocked per-variant accuracy, leakage-safe features, and richer explanation language.

### CR-075 — Evaluation integrity + threshold calibration (70/15/15 split)
- **Problem:** Existing trainer used a single train/test split; calibration + threshold derivation shared data.
- **Root cause:** No held-out slice → no honest reporting; calibrator was fit on the same rows used to score it.
- **Solution:** Stratified 70/15/15 split; isotonic calibrator on VAL; precision-floor threshold on VAL; HELD-OUT touched only at the end.
- **Technical complexity:** Medium — required reproducible split, per-surface metric reporting, calibrator persistence.
- **Business impact:** Reported metrics now production-realistic; HELD-OUT becomes the honest figure for stakeholders.

### CR-076 + CR-077 — Stabilization, integrity + invariant audit
- **Problem:** Silent feature-order drift in XGBoost; non-monotonic calibrators silently shipping; ad-hoc seed handling.
- **Root cause:** booster.feature_names not asserted at predict; calibrator monotonicity not checked.
- **Solution:** Hard-fail FeatureSchemaError on column-name/order drift; 20-point monotonicity test; fixed random seeds.
- **Technical complexity:** Low-medium — guards added at predict-entry and calibrator-fit.
- **Business impact:** Removes a class of silent ML bugs; predictions become reproducible.

### CR-078 / CR-078A — Claim-focused denial-reason renderer
- **Problem:** Renderer surfaced raw FB feature names (`paperwork_missing_when_required`) to operators.
- **Root cause:** No mapping from feature → business bucket → care-setting-specific sentence.
- **Solution:** 11 canonical buckets; default sentences; per-(bucket, subtype) overrides for healthcare/home_care/dental/etc.
- **Technical complexity:** Medium — context-aware mapping with deduplication.
- **Business impact:** Operators read remediation guidance in the language of their workflow.

### CR-079 — Hyperparameter tuning (Optuna Balanced)
- **Problem:** Stock XGBoost hyperparameters; no per-variant tuning.
- **Root cause:** Phase 3 shipped a single set of defaults across all variants.
- **Solution:** Optuna TPE + MedianPruner, 60 trials × 3 variants, composite PR-AUC objective, per-variant promotion gates.
- **Technical complexity:** Medium-high — search-space design, leakage-safe CV inside trials, atomic promotion script.
- **Business impact:** 837I F1 +6.32 pp, PR-AUC +4.06 pp; promotion gates rejected 837P / 837D candidates.

### CR-080 — Consolidated training history (UI)
- **Problem:** Three rows per `/train` (one per variant) in the history card.
- **Root cause:** API returned rows from `model_training_metrics` un-grouped.
- **Solution:** 60-second window grouping in the API layer; deterministic synthetic `training_run_id`.
- **Technical complexity:** Low — pure presentation-layer change.
- **Business impact:** Operators see one training event per click, not three.

### CR-081 — Promotion safety + runtime consistency
- **Problem:** Bundle promoted on disk; uvicorn still served the cached predictor.
- **Root cause:** `_FB_PREDICTOR_CACHE` initialised on startup, no invalidation API.
- **Solution:** `POST /api/predictions/reload-bundles` + `scripts/run_dev.{sh,ps1}` enforcing `--reload`.
- **Technical complexity:** Low — cache-invalidation endpoint + dev-launcher hardening.
- **Business impact:** Eliminates a class of "stale model" production bugs.

### CR-083 — Refresh `mv_claim_labels` before every `/train`
- **Problem:** Bulk upload of 14,250 claims; next `/train` produced identical `total_claims_used`.
- **Root cause:** Trainer silently operated on stale MV.
- **Solution:** Refresh MV as first statement in `/train`; abort 503 on failure.
- **Technical complexity:** Low.
- **Business impact:** Training corpus matches database state; ends silent metric stagnation.

### CR-084A / CR-084B — Tuning re-validation + 837I promotion
- **Problem:** CR-083 grew dental + home_care 3–4×; CR-079 conclusions needed re-checking.
- **Root cause:** Tuning is corpus-dependent.
- **Solution:** Re-ran 60-trial sweep; manual atomic promotion of 837I (gate margin was 0.0033 vs 0.008 required).
- **Technical complexity:** Medium — required disciplined manual override.
- **Business impact:** Confirmed 837P / 837D decisions; 837I retained tuned gains on larger corpus.

### CR-085 — Dental performance investigation
- **Problem:** Dental F1 stuck ~0.66 despite 4× corpus growth + tuning.
- **Root cause:** 68/116 features constant on dental corpus; 67 % of claims lack diagnosis code; 7 ref tables empty.
- **Solution:** Audit-only investigation. Ranked remediations: populate ref data, source diverse EDI, add dental MVs.
- **Technical complexity:** Medium — feature coverage analysis + SHAP audit + label quality check.
- **Business impact:** Attributed the ceiling to information, not model capacity; saved the team from chasing more tuning trials.

### CR-086 / CR-087 / CR-088 — Reference data ingestion + FE activation
- **Problem:** Reference tables empty on local docker; FE could not use code metadata.
- **Root cause:** Loaders existed for code_masters only; HCPCS + ICD-10-CM not loaded; FE never read ref tables.
- **Solution:** Replicate 1,506 CARC/RARC rows; load 8,724 HCPCS + 74,719 ICD-10-CM codes; implement `RefDataLookup.from_session()`; extend Category K encoder with `cpt_category_encoded` + `primary_dx_chapter_encoded`.
- **Technical complexity:** Medium — async ref loader, lazy caching, graceful degradation when tables empty.
- **Business impact:** Healthcare F1 +3.4 pp; reference data scaffolded for future RAG.

### CR-090 — Auto-refresh `mv_claim_labels` after upload (debounced)
- **Problem:** Operator had to manually retrain to see new uploads.
- **Root cause:** No background MV refresh after writes.
- **Solution:** `schedule_mv_refresh()`; 5-s quiescence debounce; bulk uploads coalesce.
- **Technical complexity:** Medium — non-blocking scheduling + concurrent refresh safety.
- **Business impact:** 200-file bulk upload triggers one refresh, ~5 s after the last write.

### CR-091 — D + I optimisation pass
- **Problem:** After D+I corpus grew, was there more juice?
- **Root cause:** Trainer's threshold selector underestimated optimum on 837I.
- **Solution:** 6-step optimisation; manual 837I threshold edit (0.15 → 0.23).
- **Technical complexity:** Medium — required held-out grid search and operator-controlled threshold edit.
- **Business impact:** 837I F1 +5.22 pp without retraining.

### CR-092 + CR-092A — Explanation-layer correctness
- **Problem:** History denials attributed to billing; raw FB names leaking; CARC/RARC mapping fragmented.
- **Root cause:** Three separate mapping sources; renderer not variant-aware; simple_pipeline reasons bypassed renderer.
- **Solution:** `_VARIANT_BUCKET_OVERRIDES`; canonical `denial_buckets.py`; route simple_pipeline through renderer; cleanup of investigation artifacts.
- **Technical complexity:** Medium — required reconciling three mapping spaces and adding context-aware overrides.
- **Business impact:** History-bucket alignment 0/7 → 4/14; raw-feature leakage 6 → 0; prediction behaviour unchanged.

### CR-093 — Actionability-tier sorting
- **Problem:** First-shown reason was largest-SHAP, not most actionable; 17.6 % of HIGH claims surfaced informational reasons first.
- **Root cause:** Sort key was `-impact`.
- **Solution:** Per-bucket tier (0 directly actionable / 1 partially / 2 informational); sort key `(tier, -impact)`.
- **Technical complexity:** Low — pure ordering change.
- **Business impact:** Tier-A-first claims 34.4 % → 94.4 %; Tier-C-first 17.6 % → 0 %.

### CR-104 — Tier-A dead-feature retirement (9 features)
- **Problem:** Universal columns 108; 9 features had gain = 0 on every variant.
- **Root cause:** Constant-valued features (e.g., placeholders) survived from Phase 3.
- **Solution:** Dropped from registry and category modules; retrained all 3 variants.
- **Technical complexity:** Low-medium — careful registry edit + retrain.
- **Business impact:** Columns 108 → 101; slightly faster DMatrix construction; expected ΔROC ≈ +0.0000.

### CR-107 — Leakage-safe training for 8 MV-backed denial-rate features
- **Problem:** Held-out ROC inflated by 0.11 – 0.36 vs realistic random-sample ROC.
- **Root cause:** 8 MV-backed denial-rate features had no temporal filter; 99.1 % of train rows shared cohorts with future rows.
- **Solution:** `compute_leakage_safe_denial_rates`; per row override using `merge_asof(allow_exact_matches=False)` on `service_from_date`.
- **Technical complexity:** **High** — required redesigning the training data path while preserving the predict path.
- **Business impact:** Honest held-out metrics for the first time; 837P 0.9974 → 0.9970, 837D 0.7811 → 0.7841, 837I 0.8952 → 0.8991.

### CR-112 — 837I isotonic recalibration
- **Problem:** Calibrator predicted 98.6 %; actual 51.6 % after CR-109 ingested 12.5 k new home_care claims.
- **Root cause:** Calibrator fit on pre-CR-109 distribution; data drift.
- **Solution:** Refit on 70 % post-CR-109 + 30 % pre slice; re-derived threshold.
- **Technical complexity:** Medium — calibration math + data-mix design.
- **Business impact:** Brier −26 %, ECE −91 %; HIGH-bucket precision 0.48 → 0.74.

### CR-114 — 837I threshold correction (0.75 → 0.50)
- **Problem:** After CR-112 raised threshold to 0.75, HIGH bucket was empty in production.
- **Root cause:** Calibrator output range peaks near 0.75; no production score crossed it.
- **Solution:** Threshold 0.75 → 0.50 (midpoint of output range).
- **Technical complexity:** Low.
- **Business impact:** Recall 100 % on audit cohort; FPR −46 pp; precision +12 pp.

### CR-115 — Replacement-claim prediction audit
- **Problem:** Operator concern that freq=7 claims were systematically flagged HIGH even after corrections.
- **Root cause:** Booster had no signal to differentiate corrected vs uncorrected replacements; `is_replacement_claim` dominated.
- **Solution:** Audit-only. Identified missing lifecycle features.
- **Technical complexity:** Medium — audit, SHAP traversal, paired prediction comparison.
- **Business impact:** Directed CR-116 → CR-120 design.

### CR-116 / CR-117 / CR-118 / CR-119 — Lifecycle features (design + compute)
- **Problem:** FE space had no information on original→replacement diff.
- **Root cause:** Pre-CR-117 features were claim-local; no relationship-aware joins.
- **Solution:** 11 lifecycle features computed via `load_original_snapshots`; AIRs locked in the integration strategy.
- **Technical complexity:** **High** — query-time CTE for original lookup; leakage-safe linkage; M1 invariant preservation while features stayed dormant.
- **Business impact:** Enabled CR-120 promotion.

### CR-120A / CR-120 — Lifecycle promotion (837I only)
- **Problem:** Should both freq=1 and freq=7 claims be in the training corpus?
- **Root cause:** Booster could short-cut on `is_replacement_claim → denied` when freq=7 was added if cohorts ≈ 99 % denied.
- **Solution:** Two candidate bundles per variant (A: freq=1 only; B: freq=1+freq=7); evaluation on fixed freq=7 audit + freq=1 holdout; variant-specific promotion of 837I/B.
- **Technical complexity:** **High** — multi-candidate training pipeline + held-out evaluation + atomic per-variant promotion.
- **Business impact:** 837I HIGH-bucket FPR on replacements 0.989 → 0.527; precision +12 pp; F1 +12 pp on audit cohort.

### CR-121 / CR-121B — Post-promotion verification
- **Problem:** Lifecycle promotion changes production behaviour; need to confirm CR-120A measurements reproduce live.
- **Solution:** Audit-only re-runs; 7-phase verification (score dist, lifecycle activation, replacement/original impact, bucket health, calibration).
- **Technical complexity:** Medium — disciplined re-run on production state.
- **Business impact:** Identified structural MEDIUM-bucket emptiness as a separate cross-variant issue.

### CR-122 / CR-122B — Retire `frequency_code_encoded`
- **Problem:** `frequency_code_encoded` had gain = 0 on every variant but consumed a column slot.
- **Root cause:** Dead feature from Phase 3.
- **Solution:** Retired from registry; retrained all 3 variants.
- **Technical complexity:** Low.
- **Business impact:** Columns 101 → 100; 837D threshold 0.01 → 0.34 (MEDIUM bucket reachable for the first time).

### CR-124 — Payer denial-rate feature audit
- **Problem:** Operator asked for `payer_denial_rate_30d`, `payer_denial_rate_90d`.
- **Root cause:** All 9 production denial-rate features are lifetime (no date window).
- **Solution:** Audit-only. Identified that 30 D is too thin (178 claims); 90 D is robust; suspicious clusters surfaced.
- **Technical complexity:** Medium — leaderboard math + stability assessment.
- **Business impact:** Informed CR-126 scope and CR-126B implementation plan.

### CR-126 — Recency-window shootout (audit; no promotion)
- **Problem:** Recency-window candidates needed empirical comparison before adoption.
- **Solution:** Trained 1 experimental booster per variant carrying 5 recency candidates; isolated artefacts under `artifacts/experiments/cr126/`.
- **Technical complexity:** **High** — multi-candidate training, gain/SHAP comparison, leakage-safe smoothing.
- **Business impact:** recent_2k wins on 837I (12.28 % gain); 90d wins on 837P (0.88 %); on 837I freq=7 cohort FPR collapsed 0.527 → 0.052 and F1 0.919 → 0.992.

## 1.4 Contribution timeline

```
2026-06-15  CR-067  FeatureBuilder cutover (primary inference path)
2026-06-16  CR-075  Evaluation integrity + threshold calibration
2026-06-16  CR-076  Stabilization + invariant audit
2026-06-16  CR-078  Claim-focused reason renderer
2026-06-16  CR-079  Optuna Balanced tuning  ◀──── major personal milestone
2026-06-16  CR-080  Consolidated training history (UI)
2026-06-16  CR-081  Promotion safety + reload-bundles
2026-06-17  CR-083  mv_claim_labels refresh before /train
2026-06-17  CR-084  Tuning revalidation + 837I promotion
2026-06-17  CR-085  Dental investigation (audit)
2026-06-17  CR-086  Replicate code_masters to local
2026-06-17  CR-087  Load HCPCS + ICD-10-CM
2026-06-17  CR-088  FeatureBuilder ref-data activation  ◀──── major personal milestone
2026-06-17  CR-090  Auto-refresh after upload (debounced)
2026-06-17  CR-091  D + I optimisation pass (manual 837I threshold)
2026-06-18  CR-092  Explanation-layer correctness
2026-06-18  CR-093  Actionability-tier sorting
2026-06-19  CR-104  Tier-A dead-feature retirement
2026-06-19  CR-107  Leakage-safe training (8 denial-rate features)  ◀──── major personal milestone
2026-06-19  CR-112  837I isotonic recalibration
2026-06-22  CR-114  837I threshold correction (0.75 → 0.50)
2026-06-22  CR-115  Replacement-claim audit
2026-06-22  CR-116  AIR: lifecycle-aware features
2026-06-22  CR-117  Lifecycle Stage 1 (6 features)
2026-06-22  CR-118  Lifecycle Stage 2 (5 correction-deltas)
2026-06-22  CR-119  AIR: integration strategy
2026-06-22  CR-120A Training-strategy validation
2026-06-22  CR-120  Variant-specific lifecycle promotion (837I only)  ◀──── major personal milestone
2026-06-22  CR-121  Post-promotion verification
2026-06-22  CR-122  AIR: frequency-code consistency
2026-06-22  CR-122B Retire frequency_code_encoded
2026-06-24  CR-124  Payer denial-rate feature audit
2026-06-24  CR-126  Recency-window shootout  ◀──── major personal milestone
2026-06-24  CR-126C CPT/DX recency audit
```

Five star milestones (CR-079, CR-088, CR-107, CR-120, CR-126) are the strongest report-worthy outputs.

---

# SECTION 2 — Business value

## 2.1 Why denial management matters

Denial management is the discipline of preventing, working, and appealing insurance claim denials. In US healthcare, a claim is a structured electronic document (EDI 837) submitted by a provider to a payer (insurance company / Medicare / Medicaid). When the payer refuses to pay, the response (EDI 835) carries a Claim Adjustment Reason Code (CARC) explaining why. Every denied claim represents work the provider has already performed but for which they may not be paid unless the denial is corrected and resubmitted.

For a non-technical audience: imagine sending an invoice to a customer, and the customer returning it with a one-line reason code. You can either (a) rewrite and resend, (b) write off the loss, or (c) negotiate. RCM denial management is the system that does all three across millions of invoices per month for a hospital, a clinic group, or a billing service. Every percentage point of denials that you prevent or resolve goes straight to the bottom line.

## 2.2 Financial impact of denials

- **Industry benchmark:** denial rates run 5 – 25 % depending on specialty. The corpus in this project shows 4.76 % for outpatient professional (837P healthcare), 20.96 % for home care (837I home_care), and 1.19 % for dental (837D dental). The home-care figure mirrors typical industry experience for Medicare home health, where coverage rules are dense and documentation requirements are strict.
- **Cost per denial:** industry studies estimate the average cost to *rework* a single denied claim at roughly $25–$118 (administrative + clinical + payer-follow-up labour). The cost per *prevented* denial is essentially zero because the system flags it before the claim is sent.
- **Revenue at risk:** even at a conservative $200 average claim amount and a 10 % denial rate, a practice submitting 50,000 claims per month is exposed to ~$1 M of revenue review per month, of which roughly a third is permanently written off when denials are never reworked.

The combination of pre-submission flagging + automated recommendations + outcome tracking lets a small RCM team protect substantially more revenue per analyst-hour than manual queue work.

## 2.3 Revenue leakage

Revenue leakage is money the provider has *earned* (services rendered) but cannot *collect* because of process defects. Common leakage paths the system addresses:

- **Avoidable denials** — claims denied for parser-detectable reasons (missing modifiers, invalid POS codes, missing dates). Caught before submission by the 4-tier validator chain.
- **Recurring denials** — same payer + same CPT + same reason pattern. Captured in the materialised views (`mv_payer_cpt_denial_rate`, `mv_provider_cpt_denial_rate`) and surfaced as features so the model learns the pattern.
- **Missed timely-filing windows** — payer policy says 90 / 180 / 365 days; the `service_to_submission_days` + `timely_filing_proximity_ratio` features compare the claim against the payer's deadline.
- **Replacement-claim churn** — a denied claim is resubmitted with a freq=7 frequency code; if the corrections are insufficient the cycle repeats. Lifecycle features (CR-117/118) compare original vs replacement to expose whether the right changes were made.

## 2.4 Operational cost reduction

- **Triage time per claim.** With actionability-tier sorting (CR-093), the first reason an operator reads is the one most likely to be fixable. Tier-A-first claims went from 34.4 % to 94.4 % of HIGH-risk surfaces — a near-3× reduction in cognitive search.
- **Avoided manual MV refresh.** CR-090 added debounced background refresh; a 200-file bulk upload triggers exactly one refresh ~5 s later, eliminating an operator step that previously had to be remembered.
- **Avoided manual restart on promotion.** CR-081 closed the cache invalidation gap; operators no longer have to ask DevOps to bounce uvicorn after a model promotion.
- **Avoided wasted retrain cycles.** CR-083 catches stale-MV training (where the trainer was silently using a corpus that hadn't included the latest upload). Each saved retrain is a 30–90-second engineering chore.

## 2.5 Rework reduction

The recommendation engine surfaces three kinds of guidance per claim:

1. **CARC-driven** — exact reason from the payer with the hand-curated fix sentence. Operators stop guessing what "CARC 16" means in the context of *this* claim.
2. **Parser-driven** — segment-level defects flagged before transmission. The fix is "open the EDI Inspector and correct the segment" — a deterministic action.
3. **ML-driven** — top-5 SHAP-derived reasons translated through 11 business buckets with care-setting-specific wording (e.g., "Treatment documentation may require review" on dental, "The billed home-health services may increase denial risk" on home_care).

For a non-technical evaluator: the system gives the analyst a *prioritised, ranked, plain-language list* of what to fix on each claim, *before* it goes out the door, *and* it learns from the actual payer response when the 835 comes back.

## 2.6 Strategic value (for an academic panel)

- **Auditable AI.** Every prediction logs its `model_version`, `feature_engineering_version`, `calibrator_version`, `decision_threshold`, `top_risk_factors`, and `unseen_indicators`. This means a clinical or compliance auditor can ask, six months from now, *"why did the model flag this claim?"* and get a deterministic answer.
- **No black-box outputs.** SHAP-based explanations + canonical buckets + actionability tier mean the system never tells a user *"the model said no"* — it always says *"the model said HIGH risk because [bucket]"*.
- **AIR discipline.** The Architecture Impact Review template (8 sections + red-flag checklist) is itself a process contribution — every non-trivial change is scoped, sized, and reviewed before code is written. The CR-051 incident (`audit_log` inflating to 7 M rows / 8 GB in days from a single inadvertent UPDATE) is the canonical example of why this discipline was adopted.

---

# SECTION 3 — System walkthrough

A complete narrative from a patient walking into a clinic to the analytics dashboard at the end of the month. Each step is one paragraph + a code/data pointer when relevant.

## 3.1 Patient

A patient arrives for an outpatient encounter. They present an insurance card carrying a member ID, group / policy number, and the payer name. The front desk verifies eligibility (either real-time via 270/271 or by phone). For this project, the patient's identifying data lands in the `patients` table — `member_id` (unique), demographic fields (name, DOB, gender) — *if* the 837 mentions them as a subscriber (`NM1*IL` self-claim) or a separate patient (`NM1*QC` non-self).

## 3.2 Provider

The provider — physician, therapist, dental practice, home-care agency — performs the service. The encounter generates a chart with diagnoses (ICD-10-CM codes) and procedures (CPT for professional, HCPCS for items/services, CDT for dental, HIPPS for home-health groupings). Providers are recorded in the `providers` table with their NPI (national provider identifier), taxonomy code (specialty), state, organisation. A single claim references up to 4 provider roles: billing, rendering, referring, supervising.

## 3.3 Claim generation

The provider's practice-management system (EHR / billing system) translates the encounter into an X12 837 claim. The claim is wrapped in:

```
ISA*…IEA       ← interchange envelope
  GS*…GE       ← functional-group envelope (GS08 carries the IG identifier)
    ST*837*…SE ← transaction set
      HL*…     ← hierarchical loops (billing → subscriber → patient → claim)
      CLM*…    ← one CLM per claim
        SV1/SV2/SV3*… ← service lines (one per procedure)
        HI*…   ← diagnoses
        DTP*…  ← dates
        REF*…  ← authorisations, referrals, prior-payer claim numbers
```

GS08 determines variant: `X222A1`=837P, `X223A2`=837I, `X224A2`=837D, `X221A1`=835.

## 3.4 837 upload

The operator drops the file onto the blue UploadCard in the React frontend. The file is POSTed to `POST /api/edi/upload` as multipart form-data. The backend computes `content_hash`, runs encoding detection (`utf-8-sig → utf-8 → cp1252 → latin-1` with ISA-presence sanity), and returns an `EdiUploadResponse` carrying `parse_status`, entity counts, the first 50 validation errors, and a `pair_status` (paired / replacement_no_original / remit_no_837).

## 3.5 Parsing

`parse_and_save(session, raw_bytes, file_name)` runs the full pipeline in one async transaction:

1. `detect_delimiters` — ISA hardening (non-alphanumeric, non-whitespace; `ISA[3] == ISA[6]`).
2. `decode_edi` — encoding fallback with ISA-presence check.
3. `tokenize` → list of segments.
4. `detect_variant(GS08)` → `(file_type, service_variant)`.
5. For each segment: `dispatch_segment(name, elements, ctx)` invokes one of ~30 handlers (NM1, HL, SBR, CLM, SV1/2/3, HI, DTP, REF, CR1, CR3, CRC, TOO, DN1, DN2, PWK, AMT, NTE, CLP, CAS, LQ, SVC, MIA, MOA, N1, N3, N4, PRV, PAT, LX, DMG, PER).
6. Handlers mutate `ParseContext`; broad exceptions become `parse_error` events.
7. `finalize_subtypes(ctx)` derives the claim subtype from CPT codes, revenue codes, modifiers (e.g., A0 → transport, GP → therapy, D-prefix → dental).

## 3.6 Validation

`validators.run_all(ctx, session)` runs four tiers in order:

| Tier | Owner | Example |
| --- | --- | --- |
| 1 — Structural | `tier1_structural.py` | ST/SE counter match. Multi-ISA warning. |
| 2 — IG | `tier2_ig.py` | 837P claim without DTP*472 → drop (Lesson C3). 835 without DTM*050/*405 → WARN. |
| 3 — Payer policy | `tier3_payer.py` | Reads `payer_policies` for prior_auth / referral / timely_filing per (payer × CPT × variant). |
| 4 — Business | `tier4_business.py` | Date ordering. Duplicate `claim_number`. Line-sum vs `CLM02` tolerance. |

ERROR-severity entries promote `claim.dropped=True`; the claim is not persisted but its `raw_segments` are (for audit). WARNING / INFO entries are recorded; the row persists.

## 3.7 Feature engineering

When a claim needs to be scored, `FeatureBuilder(service_variant, claim_subtype).transform(session, df)` runs:

1. Load `RefDataLookup` (procedure_codes, diagnosis_codes, ncci_edits, cms_lcd_coverage, payer_policies).
2. Load snapshots: patient history (`mv_patient_claim_history`), provider profile (`mv_provider_*`), joint encoders (6 MVs), lifecycle (if `include_lifecycle=True`).
3. Enrich the DataFrame with derived categoricals (`cpt_category`, `dx_chapter`).
4. Apply the fitted `LeakageSafeTargetEncoder` to 7 categorical columns.
5. Assemble 14 categories (J, A, B, C, D, E, F, G, H, I, K, L, Z, M) + variant block + lifecycle.
6. Substitute leakage-safe denial rates (CR-107) and recency rates (CR-126B) at train; reuse global MV rates at predict.
7. M1 strict column-order check (`validate_feature_frame`).

The result is a 100–122-column numeric matrix per claim.

## 3.8 ML prediction

`POST /api/predictions/predict-file/{edi_file_id}` routes each claim to its (variant, subtype) FeatureBuilder predictor:

```
booster.predict(DMatrix(X))          → raw scores
calibrator.transform(raw_scores)     → calibrated probabilities
predicted_label = (calibrated >= decision_threshold)
risk_level = HIGH (>= threshold) / MEDIUM (>= 0.05) / LOW
booster.predict(DMatrix(X), pred_contribs=True)[:, :-1]  → SHAP per feature
```

Each prediction is written to `prediction_log` with `model_version`, FE version, calibrator version, threshold, top risk factors, unseen indicators, pipeline name, prediction group id.

## 3.9 Risk scoring

The frontend's `DenialRiskCard` renders the score (e.g., "82 %"), the level (HIGH / MEDIUM / LOW), and the top-5 reasons. Each reason is *not* the raw feature name (`paperwork_missing_when_required`) — the renderer maps the feature to one of 11 buckets and applies a care-setting-specific sentence (e.g., on healthcare: "Clinical documentation may require review"; on dental: "Treatment documentation may require review").

## 3.10 Recommendations

`POST /api/recommendations/by-file/{edi_file_id}` combines three signal sources per claim:

- **CARC** (from `remittance_claims` ⨝ `adjustments`): code → hand-curated `(reason, fix)`.
- **Parser** (from `parse_events` with `event_type IN ('validator_error', 'claim_dropped')`): segment + field + message → generic "open EDI Inspector" fix.
- **ML** (from the predictor's `top_risk_factors`): SHAP → bucket → variant-aware sentence with actionability-tier order.

The composite list is returned with a `status_badge` per claim: `Resolved` / `Denied` / `High Risk`.

## 3.11 Claim correction

The operator opens the high-risk claim, reviews the top reasons (e.g., "Authorization requirements may not be fully satisfied" + "Supporting documentation may be insufficient"), and corrects the 837 in the upstream practice-management system. The corrected claim is exported as a fresh 837 file.

## 3.12 Replacement claim (freq=7)

The corrected 837 carries `CLM05-3 = 7` (replacement) and references the original claim's payer control number via `REF*F8`. When parsed, `derive_subtype` and the lifecycle subsystem (CR-117/118) compute the diff between the original (freq=1) and the replacement (freq=7): was an auth added? referral? modifier? did the diagnosis change? did the charge change? Eleven lifecycle features capture the answer.

## 3.13 835 remittance

The payer transmits an 835 file. The system parses CLP (claim-level), CAS (CARC triplets), SVC (service-line), LQ (RARC remarks), MIA / MOA (institutional adjudication). Each remit row links to its claim by `claim_number`. The `mv_claim_labels` MV is refreshed (debounced); the `prediction_log` row for the claim is back-filled with `actual_denied`, `actual_status`, `resolved_at`, `resolved_by_remittance_id`.

## 3.14 Analytics

- **Dataset stats** (`GET /api/predictions/dataset-stats`): total claims, denied, paid, denial rate.
- **Training history** (`GET /api/ml/training-history`): per-variant runs grouped by 60-second window with held-out F1 / precision / recall / ROC-AUC / PR-AUC / Brier.
- **Latest training** (`GET /api/ml/latest-training`): most-recent training run.
- **Pending pairs** (`GET /api/edi/pending-pairs`): originals awaiting replacement / replacements awaiting original / remit-no-837.

A future analytics surface (planned, not built) would visualise denial-rate drift over time using `mv_drift_baselines`, top denial-bucket distribution per payer, and the accuracy-over-time curve from `prediction_log` matched against later 835 outcomes.

---

# SECTION 4 — Database explanation

## 4.1 Simplified ER explanation

```
                              ┌──────────────┐
                              │   tenants    │
                              └──────┬───────┘
                                     │
                              ┌──────▼───────┐
                              │    users     │
                              └──────────────┘
                                     │
              ┌──────────────┐       │       ┌──────────────────┐
              │   edi_files  │◀──────┴──────▶│ pending_claim_   │
              │  (soft-del)  │               │ pairs            │
              └──────┬───────┘               └──────────────────┘
                     │
              ┌──────▼───────┐         ┌─────────────────────────┐
              │ raw_segments │         │ parse_events            │
              │ (partitioned)│         │ (partitioned)           │
              └──────┬───────┘         └─────────────────────────┘
                     │
                     │            ┌─────────────────┐
              ┌──────▼─────┐      │    payers       │
              │   claims   │─────▶│ (master + GIN   │
              │ (soft-del) │      │  aliases)       │
              └─┬──┬──┬────┘      └─────────────────┘
                │  │  │
                │  │  └─────▶ patients (soft-del)
                │  │
                │  └────────▶ providers
                │
       ┌────────┼─────────┬───────────────┬───────────────┬───────────────┐
       │        │         │               │               │               │
   claim_lines  diagnoses  claim_         claim_amounts   claim_          remittance_
   (1..N)      (1..N)     certifications                  attachments     claims (1..M)
                                                                          │
                                                                          ├─ adjustments (CAS)
                                                                          └─ remark_codes (RARC)
                          home_care_episodes
                          transport_certifications

                          claim_lifecycles    appeals
                          (original ↔ child)
```

Supporting graphs:

- **Reference** — `code_masters` (CARC/RARC), `procedure_codes`, `diagnosis_codes`, `payer_policies`, `cms_knowledge`, `ncci_edits`, `cms_lcd_coverage`. Used by FE + recommendation engine.
- **ML pipeline** — `prediction_log` (partitioned), `model_training_metrics`, `feature_snapshots`, `model_artifacts`.
- **RAG** — `knowledge_documents`, `knowledge_chunks` (vector), `claim_embeddings` (vector), `correction_examples`, `rag_generations`.
- **Materialised views (14)** — `mv_claim_labels` + 13 dependents.

## 4.2 Per-table reference

### `edi_files`
- **Purpose:** every uploaded EDI file. Soft-deleted.
- **Key columns:** `id`, `file_name`, `file_type` (edi_837 / edi_835 / edi_277 / edi_999), `content_hash` (unique), `raw_text`, `parse_status`, `parse_summary` JSONB, `uploaded_by_user_id`, `deleted_at`.
- **Relationships:** has many `raw_segments`, `parse_events`, `claims`, `remittance_claims`.

### `raw_segments`
- **Purpose:** every parsed segment. Partitioned monthly by `created_at`.
- **Key columns:** composite PK `(id, created_at)`, `edi_file_id`, `segment_position`, `segment_name`, `elements` (JSONB), `handler_status` (handled / skipped_unhandled / parse_error / validator_dropped).

### `parse_events`
- **Purpose:** parse telemetry (segment_handled, segment_skipped, validator_warning, validator_error, claim_dropped, parse_error). Partitioned monthly.
- **Key columns:** `event_type`, `segment_name`, `field`, `details` JSONB with GIN index.

### `claims`
- **Purpose:** core claim row.
- **Key columns:** `service_variant` (837P/I/D), `claim_subtype`, `claim_number`, `payer_id`, `patient_id`, `billing_provider_id`, `rendering_provider_npi`, `referring_provider_npi`, `total_charge_amount`, **NULLABLE** `service_from_date` (Lesson C3), `frequency_code`, `variant_data` JSONB, `deleted_at`.
- **Relationships:** has many `claim_lines`, `diagnoses`, `claim_certifications`, `claim_amounts`, `claim_attachments`, `home_care_episodes`, `transport_certifications`. Has many `remittance_claims` (via `claim_number`).

### `claim_lines`
- **Purpose:** service lines within a claim.
- **Key columns:** `claim_id`, `line_number` (unique per claim), `procedure_code`, `modifier1/2/3/4`, `billed_amount`, `units`, `place_of_service`, `service_date`, `diagnosis_pointers` ARRAY, `revenue_code` (837I), `hipps_code`, `tooth_number` / `tooth_surfaces` (837D), `ndc_drug_code`.

### `diagnoses`
- **Purpose:** ICD-10 diagnoses per claim.
- **Key columns:** `claim_id`, `sequence_number`, `diagnosis_code`, `diagnosis_type` (ABK/ABF/BJ/…), `present_on_admission`.

### `remittance_claims`
- **Purpose:** 835 CLP-level remit.
- **Key columns:** `claim_id`, `claim_status_code` (`CLP02` — 1/2/3=processed, 4=denied, 19/20=forwarded, 22=reversal), `billed_amount`, `paid_amount`, `patient_responsibility_amount`, **NULLABLE** `remittance_date` (Lesson P1), `payer_claim_control_number`.

### `adjustments`
- **Purpose:** CAS triplets per remit.
- **Key columns:** `remittance_claim_id`, `adjustment_group_code` (CO/PR/OA/PI/CR), `adjustment_reason_code` (CARC), `adjustment_amount`, `quantity`.

### `remark_codes`
- **Purpose:** RARC remarks per remit.
- **Key columns:** `remittance_claim_id`, `remark_code`.

### `code_masters`
- **Purpose:** WPC CARC/RARC/POS/claim-status lookups + enriched denial guidance.
- **Key columns:** `code_type`, `code` (unique pair), `short_description`, `denial_reason_plain`, `patient_friendly_reason`, `recommended_action`, `category`, `action_category`, `severity`, `is_billable_denial`, `is_patient_responsibility`, `requires_remark_code`.

### `procedure_codes`
- **Purpose:** CPT / HCPCS / CDT / HIPPS / NDC catalogue.
- **Key columns:** `code`, `code_system`, `category`, `effective_date`, `deprecated_date`, `code_metadata` JSONB (annual_limit, requires_pwk, requires_modifier, valid_pos_codes, age_min/max, gender_restriction, global_period_days).

### `diagnosis_codes`
- **Purpose:** ICD-10-CM / PCS / ICD-9-CM catalogue.
- **Key columns:** `code`, `code_system`, `description`, `chapter`, `category`.

### `payer_policies`
- **Purpose:** payer-specific rules.
- **Key columns:** `payer_id`, `policy_type` (coverage / prior_auth / frequency_limit / modifier_required / age_limit / appeal_process / timely_filing / referral_required), `applies_to_codes` ARRAY, `structured_rule` JSONB, `service_variant`, `claim_subtype`.

### `prediction_log`
- **Purpose:** every prediction. Partitioned monthly by `prediction_time`.
- **Key columns:** composite PK `(id, prediction_time)`, `claim_id`, `prediction_id` (UUID), `predicted_risk`, `predicted_label`, `risk_level`, `service_variant`, `claim_subtype`, `model_version`, `feature_engineering_version`, `calibrator_version`, `decision_threshold`, `actual_denied`, `actual_status`, `resolved_at`, `resolved_by_remittance_id`, `top_risk_factors` JSONB, `unseen_indicators` JSONB, `pipeline_name`, `prediction_type`, `prediction_group_id`.

### `model_training_metrics`
- **Purpose:** one row per training run.
- **Key columns:** `training_id`, `service_variant`, `claim_subtype`, `training_timestamp`, `total_claims_used`, `training_samples`, `validation_samples`, `metrics` JSONB (OOF/VAL/HELD-OUT), `hyperparameters` JSONB, `feature_importance` JSONB, `calibration_method`, `decision_threshold`, `model_version`, `feature_engineering_version`, `artifact_paths` JSONB.

### `feature_snapshots`
- **Purpose:** persisted features per training row (for drift + re-attribution).
- **Key columns:** `training_id`, `claim_id`, `feature_vector` JSONB, `denied`, `fold_assignment`.

### `claim_lifecycles`
- **Purpose:** original → parent → child chain.
- **Key columns:** `original_claim_id`, `parent_claim_id`, `child_claim_id`, `relationship_type` (replacement / resubmission / void / correction / appeal), `iteration_number`, `days_to_resolution`, `diff_summary` JSONB.

### `appeals`
- **Purpose:** appeal lifecycle.
- **Key columns:** `claim_id`, `appeal_level` (internal / external / ALJ / DAB), `status` (draft / submitted / accepted / denied / withdrawn), `outcome`, `generated_letter`, `human_edits`, `final_letter`.

### `pending_claim_pairs`
- **Purpose:** silent tracking of original ↔ replacement linkage to avoid false warnings.

### RAG tables (5)
- `knowledge_documents`, `knowledge_chunks` (vector + HNSW), `claim_embeddings` (vector), `correction_examples`, `rag_generations`.

### Materialised views (14)
- `mv_claim_labels` keystone, 6 joint denial-rate MVs, 3 provider profile MVs, `mv_lifecycle_outcomes`, `mv_patient_claim_history`, `mv_drift_baselines`, `mv_payer_denial_rates_recent_2k`, `mv_payer_denial_rates_90d`. All carry unique indexes so they can be refreshed `CONCURRENTLY`.

---

# SECTION 5 — Machine learning explanation (beginner-friendly)

## 5.1 Why XGBoost was chosen

Three reasons in plain language:

1. **It works very well on tabular data.** Most healthcare-claim features are columns of numbers and categories (charge, units, place of service, payer, CPT code). Tree-based ensembles like XGBoost consistently win on this kind of data — empirically on this corpus we see ROC-AUC 0.95 – 0.998.
2. **It explains itself.** XGBoost exposes per-feature contributions (SHAP values) directly from the booster. There is no need for a separate model to explain why a prediction was made. The system uses these contributions to surface plain-language reasons to the operator.
3. **It handles imbalance.** The dental corpus has only 1.19 % denials. XGBoost lets us scale the positive class with `scale_pos_weight = neg / pos`, so even rare denials are learned without us having to over-sample or under-sample.

## 5.2 Why not Random Forest

Random Forest is also a tree-based ensemble and is reasonable for tabular data. It was not chosen because:

- **Gradient boosting > bagging on this corpus.** Random Forest is bagging (independent trees averaged); XGBoost is boosting (each tree fixes errors of the previous). Boosting tends to win when the signal is small (low denial rate, noisy labels), which describes denials.
- **Calibration is worse out of the box.** Random Forest probabilities tend to cluster around 0.5 (over-confident in the middle); they need more post-processing. XGBoost paired with isotonic regression gives well-calibrated outputs (Brier 0.002 – 0.018 in this project).
- **Less industry tooling.** SHAP, XGBoost's `pred_contribs=True`, and Optuna integrations are mature and standard.

## 5.3 Why not Logistic Regression

Logistic regression is simple, fast, and interpretable. It was not chosen because:

- **Linear in features.** Logistic regression learns a single weight per feature. Denial risk is highly non-linear and interacts heavily — e.g., "missing modifier" raises risk *only when* the procedure code requires one. Capturing those interactions in a linear model means hand-crafting hundreds of polynomial / interaction terms, which is fragile.
- **Calibration on the linear scale is fine, but discrimination suffers.** On this corpus, an XGBoost model gets PR-AUC 0.99 on 837P; a logistic baseline would not get close.
- **No native SHAP.** Coefficient signs are interpretable for linear models, but the ranking of *which feature mattered most for this specific claim* is harder than with tree-SHAP.

## 5.4 Why not Deep Learning

Deep neural networks were not chosen because:

- **Tabular data is XGBoost's home turf.** The literature (Shwartz-Ziv & Armon, 2022; Borisov et al., 2024) is consistent: on small/medium tabular tasks, gradient-boosted trees match or beat neural nets while being faster to train and easier to deploy.
- **Less data than DL needs.** The training corpora are 8 k – 43 k rows. DL becomes interesting at the 1 M+ row scale with abundant labels.
- **Interpretability cost is high.** A deep net would need a surrogate explanation model (LIME/SHAP wrappers) and would still be harder for a healthcare auditor to defend.
- **Operational simplicity.** XGBoost is a single `model.json` file + a calibrator + an encoder. A DL model would require a GPU runtime, a more complex bundle, and version-control of weights at scale.

## 5.5 Calibration in plain language

A raw score from XGBoost might say *"0.7"*, but in reality, when the model says 0.7 the true denial rate may be 0.3. **Calibration** is the post-processing step that aligns the model's scores with the true rates.

The system uses **isotonic regression**: a non-parametric, monotonic mapping fit on the *validation* slice. "Monotonic" means: if claim A's raw score is higher than claim B's, A's calibrated score is also higher than B's — the ranking is preserved.

Why isotonic vs sigmoid (Platt scaling)? Sigmoid assumes the miscalibration is S-shaped (one bend). Isotonic can fit any monotonic shape, including bi-modal label distributions like the home_care corpus where one payer denies 80 % and another 5 %.

## 5.6 Thresholding in plain language

The calibrated score is a probability between 0 and 1. We need a threshold to decide HIGH / not HIGH. We chose a **precision-floor** approach:

> Find the threshold that maximises **recall** (catch as many denials as possible) subject to **precision ≥ 0.85** (when we flag a claim as HIGH, we want to be right 85 % of the time).

If 0.85 is unreachable on a corpus (dental, 1.19 % prevalence), we fall back to the threshold that maximises precision. Production thresholds are 0.05 for healthcare, 0.50 for home_care (post-CR-114), and 0.34 for dental (post-CR-122B).

This is the right business choice because over-flagging is expensive (operator time) and under-flagging is missed revenue. Precision-floor enforces a trust floor on what we tell operators.

## 5.7 SHAP in plain language

SHAP (SHapley Additive exPlanations) is a way to split a prediction into per-feature contributions. For one claim:

```
predicted_score = base_rate + contribution(feature_1) + contribution(feature_2) + …
```

Each contribution is a number — positive means the feature pushed the score up (toward denial), negative means it pushed down (toward paid). The model surfaces the top-15 features by absolute contribution, filters to positive-only (so only "increases risk" reasons appear), maps each to one of 11 buckets, deduplicates within bucket, sorts by actionability tier (then by impact), and returns the top-5.

For a viva: SHAP is the *post-hoc* interpretability method; XGBoost exposes it natively, so we don't need a separate model to explain the main one.

## 5.8 Feature importance in plain language

Two flavours:

1. **Global feature importance** — average absolute contribution across the whole training set. Useful for understanding what the model has learned overall.
2. **Per-claim SHAP** — what mattered for *this specific claim*. This is what the operator sees.

For this project, global importance after CR-088 shows `payer_overall_denial_rate`, `payer_cpt_denial_rate`, `cpt_dx_denial_rate`, and `is_replacement_claim` at the top across variants. After CR-126 on 837I, `payer_overall_denial_rate_recent_2k_smoothed` jumps to the top with 12.28 % gain.

---

# SECTION 6 — Feature engineering explanation (with examples)

Each category includes (a) what it measures, (b) why it helps prediction, (c) a real-world example.

## Category A — Coverage / Eligibility
- **What it measures.** Patient age, age band, coordination-of-benefits position, and per-payer denial rate (lifetime + recent 2,000 + last 90 days, Bayesian-smoothed).
- **Why it helps.** Some payers deny far more than others. Age and COB position drive policy applicability.
- **Example.** *Aetna denies 18 % of claims overall but 31 % over the last 90 days for outpatient professional.* The model learns this drift and weights claims to Aetna higher.

## Category B — Authorization / Referral
- **What it measures.** Presence of prior auth, referral, validity of the auth number format, whether the payer requires either for this CPT.
- **Why it helps.** Missing authorisation is the single most common denial reason (CARC 197/198).
- **Example.** *CPT 70553 (MRI brain) requires prior auth under Cigna's policy. The claim has no REF*G1 segment. The feature `auth_missing_when_required` fires.*

## Category C — Clinical / Medical Necessity
- **What it measures.** CPT–diagnosis pairing strength, ICD chapter, complexity of E&M, whether dx supports the procedure.
- **Why it helps.** Payers deny claims where the diagnosis does not justify the procedure (medical-necessity denials, CARC 50).
- **Example.** *Procedure: knee MRI (73721). Diagnosis: headache (R51.9). The `cpt_dx_alignment_score` is low; the model raises risk.*

## Category D — Coding Integrity
- **What it measures.** Modifier presence + count, NCCI unbundling, annual-limit checks, replacement-claim flag.
- **Why it helps.** Unbundled service lines and missing/incorrect modifiers are operationally common defects.
- **Example.** *NCCI says CPT 99213 and 96372 cannot be billed together without modifier 25 on the E&M. The claim has neither modifier. `is_likely_unbundled` fires.*

## Category E — Timely Filing
- **What it measures.** Days from service to submission, payer's filing window, ratio of the two, near-miss flag.
- **Why it helps.** Past-deadline denials (CARC 29) are unrecoverable except through formal appeal.
- **Example.** *Service date 2024-01-15, submission 2025-01-10, Medicare filing limit 365 days. Submitted on day 360. `is_near_timely_filing` fires.*

## Category F — Documentation
- **What it measures.** PWK attachment presence, payer requirement for PWK, certification, notes.
- **Why it helps.** Missing attachments are a major source of "lacks information" denials (CARC 16, RARC M76).
- **Example.** *Procedure 97140 (manual therapy) requires plan-of-care for the payer. No PWK*CT segment. The model raises risk.*

## Category G — Patient History
- **What it measures.** Claims in last 30/90/365 days, prior denials with this payer, prior denials with payer + CPT, days since last claim, new-patient flag.
- **Why it helps.** Patient-specific patterns (frequent ER visits, repeat denials with one payer) signal risk.
- **Example.** *This patient has had 4 claims denied by Anthem in the last 90 days. `prior_denials_with_payer` = 4.*

## Category H — Provider Profile
- **What it measures.** Provider's overall denial rate, denial rate with this payer, denial rate with this CPT, volume band, specialty.
- **Why it helps.** Some providers' billing styles produce more denials. The model captures this profile.
- **Example.** *Dr X has a 22 % overall denial rate; the network average is 8 %. `provider_overall_denial_rate` is high.*

## Category I — Joint Encoders
- **What it measures.** Composite denial rates: (payer × CPT), (payer × dx), (payer × POS), (CPT × dx), (provider × payer), (provider × CPT).
- **Why it helps.** Some combinations are systematically denied (e.g., CPT 99214 + Anthem + POS 11 has a known issue).
- **Example.** *The (Anthem, CPT 99214, POS 11) bucket has a 47 % denial rate in the corpus. `payer_cpt_denial_rate` is high.*

## Category J — Base Claim
- **What it measures.** Total charge, billed, ratio, line count, dx count, units, service month / day of week, weekend flag, service duration.
- **Why it helps.** Unusual claim shapes (very high charge, weekend service) correlate with denials.
- **Example.** *A weekend (Sunday) service date for a routine office visit is unusual; `weekend_service` = 1.*

## Category K — Encoded Categoricals (leakage-safe target encoding)
- **What it measures.** Numeric encoding of payer name, primary CPT, primary dx, place of service, facility type, CPT category, dx chapter.
- **Why it helps.** Trees can't natively use string categories; target encoding turns "AETNA" into the denial-rate mean for that payer (computed leakage-safely via 5-fold OOF on train).
- **Example.** *"AETNA" → 0.12 (12 % denial rate during 4-fold training); UnitedHealthcare → 0.08.*

## Category L — Rarity / Unseen / Missing
- **What it measures.** Whether the payer / CPT / dx is rare in training, whether it is unseen entirely, count of missing fields.
- **Why it helps.** Predictions on rare/unseen entities are less reliable; the model learns to be cautious.
- **Example.** *This claim has a CPT that wasn't seen in training. `unseen_cpt` = 1.*

## Category Z — Reference Data Availability
- **What it measures.** Five availability flags + their mean completeness.
- **Why it helps.** When reference data is missing (empty procedure_codes table), policy-driven features can't fire; the model learns to discount that case.
- **Example.** *On a fresh local DB without procedure_codes loaded, `avail_procedure_codes_metadata` = 0 and `reference_data_completeness` = 0.25.*

## Category M — Variant-specific blocks
- **Healthcare (4):** E&M level, telehealth, preventive visit, consultation.
- **Therapy (9):** discipline modifier, KX presence, therapy cap proximity, plan-of-care, KH (maintenance), evaluation vs treatment, sessions YTD.
- **Transport (8):** ambulance cert, miles, patient weight, reason code, round trip, emergent, origin/dest, level-of-service.
- **Home care (11):** HIPPS, episode length, skilled revenue codes, visit count, homebound cert, OASIS within 5 days, face-to-face encounter, physician cert, LUPA, discipline mix, recertification.
- **Institutional other (5):** room/board revenue, hospice revenue, DRG, admission date, statement period.
- **Dental (8):** tooth number, surface count, CDT category, predetermination, orthodontia, preventive, tooth-service age, radiograph within year.
- **Specialty (10):** NDC, J-code count, high-cost drug, CR3 DME cert, rental/purchase, H-code (behavioural health), initial assessment, group therapy, CLIA number (lab).

## Lifecycle features (11 — 837I only in production)
- **What they measure.** Diff between freq=1 original and freq=7 replacement: had_prior_denial, prior_denial_bucket, days_since_original_denial, auth_added, referral_added, modifier_added, diagnosis_changed, procedure_changed, lines_changed, charge_changed, correction_action_count.
- **Why it helps.** Without these, the model can't tell a *corrected* replacement from an *uncorrected* one — both look like freq=7. The features capture whether the operator's correction actually changed something the payer cares about.
- **Example.** *A denied freq=1 claim for CPT 99214 with no modifier 25 is resubmitted as freq=7 with modifier 25 added. `modifier_added_in_replacement` = 1, `correction_action_count` = 1. The model lowers risk vs an uncorrected resubmit.*

## Recency features (CR-126B)
- **What they measure.** `payer_overall_denial_rate_recent_2k_smoothed` (last 2,000 claims per payer) and `payer_overall_denial_rate_90d_smoothed` (last 90 days). Both Bayesian-smoothed: `(denied + α·prior) / (volume + α)`, α=25, prior=0.2772.
- **Why it helps.** Payer behaviour drifts over time (policy changes, claim-processing system updates). Recency features capture that drift faster than lifetime rates.
- **Example.** *A payer that historically denied 5 % suddenly starts denying 30 % after a policy change. The recent_2k feature reflects the new rate within ~2,000 claims; lifetime rate would not budge for months.*

---

# SECTION 7 — Interview & viva questions

A consolidated bank of 250 questions with detailed answers. Use for self-prep before viva / interviews / panel evaluation.

## 7.1 Technical questions (100)

### Parsing & EDI
1. **What is X12 EDI 837?** ASC X12 transaction set 837 is the U.S. healthcare claim. Variants P (professional, IG 005010X222A1), I (institutional, X223A2), D (dental, X224A2).

2. **What is X12 EDI 835?** Transaction set 835 is the electronic remittance advice — the payer's adjudication response. IG 005010X221A1. Carries CLP (claim status), CAS (CARC adjustments), SVC (line payment), LQ (RARC).

3. **What is the ISA segment?** The interchange envelope header. Contains sender / receiver IDs, control number, and **delimiter declarations** in fixed positions (element separator at byte 3, segment terminator at byte 105, etc.).

4. **What ISA hardening rules does the system enforce?** Non-alphanumeric and non-whitespace delimiters; `ISA[3]` (element separator) must equal `ISA[6]` (control number qualifier separator); missing/truncated ISA raises `EnvelopeError`.

5. **What encoding fallback chain is used?** `utf-8-sig → utf-8 → cp1252 → latin-1` with an ISA-presence sanity check in the first 1 KB. `latin-1` is never first because it never raises and would accept garbage (Lesson P2).

6. **Why is `claims.service_from_date` NULLABLE?** Lesson C3 — v1 substituted `date.today()` when the date was missing, polluting the corpus with wrong dates. v2 lets the validator drop the claim instead.

7. **What does `safe_date` do?** Empty input → returns None silently. Non-empty unparseable input → WARN + None. Never substitutes `date.today()`.

8. **Why does the CAS triplet parser have stride-2 and stride-3 modes?** The X12 spec is stride-3 (group + reason + amount + quantity-optional, repeating). Some senders emit a compact stride-2 form. The parser auto-detects and warns on the compact form.

9. **What does `derive_subtype` do?** Maps a claim's revenue codes / CPTs / modifiers to a subtype: revenue 551–589 → home_care, CPT starting A0 → transport, modifiers in {GP, GO, GN, KH, KX} → therapy, etc.

10. **How are duplicate uploads detected?** `EdiFile.content_hash` is unique with a partial index `WHERE deleted_at IS NULL`. `parse_and_save` does insert-then-catch: if `IntegrityError` is raised, it becomes `DuplicateFileError` (HTTP 409 territory).

11. **What are the four validation tiers?** Tier 1 structural (ST/SE counters, ISA/IEA, multi-ISA warnings); Tier 2 IG (claim must have DTP*472 etc.); Tier 3 payer policy (reads `payer_policies`); Tier 4 business (date ordering, duplicate claim_number, line-sum vs CLM02).

12. **What is the `parse_summary`?** A JSONB column on `edi_files` capturing claims_saved, claims_dropped, drop_reasons_by_field, error_count, warning_count for the parsed file.

13. **What is the `HANDLER_REGISTRY`?** A dict keyed by `(variant, segment_name)` with `'*'` fallback. Handlers register themselves via side-effect imports. `dispatch_segment` looks up and invokes.

14. **Why does the dispatcher catch broad `Exception` rather than specific types?** v1 crashed when unexpected exception types bubbled up. v2 converts any handler failure into a `parse_error` event so the rest of the file continues parsing (Lesson from CR-010).

15. **How are NM1 entity codes routed?** Each entity code (`IL` subscriber, `QC` patient, `82` rendering provider, `85` billing provider, `DN` referring) updates a different field on the active claim or creates a new entity row.

16. **What is the subscriber-as-patient bug fix (CR-014)?** When `SBR02='18'` (self), the subscriber IS the patient. Without the fix, the claim's `patient_member_id` remained None. The fix copies the subscriber's member_id and creates a patient row.

17. **What is the late-rendering-NPI bug fix (CR-015)?** A `NM1*82` after CLM (line-level) was not propagating to the claim's `rendering_provider_npi` field. Fixed in `handle_nm1` by also updating the open claim's NPI field if currently empty.

18. **How are home-care episodes initialised?** When 837I parses a `CRC*75` (homebound condition) and the claim has a `service_from_date`, a `HomeCareEpisode` row is auto-created.

19. **How does the system handle multi-ISA files?** `count_isa_blocks` lets the caller detect them; Tier 1 emits a warning. v1 silently parsed only the first ISA block.

20. **What is the reparse workflow?** `reparse(session, edi_file_id)` soft-deletes the original `EdiFile` row and creates a fresh row from the stored `raw_text`, re-running the entire pipeline.

### Database
21. **Why is `prediction_log` partitioned?** Volume. ~10k–100k predictions/month. Monthly partitions enable partition pruning at query time and graceful archival of old partitions.

22. **Why composite PKs on partitioned tables?** PostgreSQL requires the partition column in every unique constraint. So `prediction_log` PK is `(id, prediction_time)`, not just `id`.

23. **What is `mv_claim_labels` and why is it the keystone?** A pre-computed per-claim denial label via LEFT JOIN to `remittance_claims`. Filters soft-deleted + missing `service_from_date` + freq IS NULL OR '1'. 10 dependent MVs build on top.

24. **Why does the system use materialised views instead of views?** Speed. The label query was a multi-CTE join; the MV reduces it to a sub-2-second read. Concurrent refresh (`REFRESH MATERIALIZED VIEW CONCURRENTLY`) keeps the system responsive during refresh.

25. **How are MV unique indexes used?** Every MV has a unique index that enables `REFRESH CONCURRENTLY` (PG requires it).

26. **What is `pgvector`?** A PostgreSQL extension adding a `vector` column type with HNSW / IVF-Flat indexing. Used for `claim_embeddings`, `knowledge_chunks`, `correction_examples`.

27. **What is HNSW?** Hierarchical Navigable Small World — a graph-based ANN index that supports cosine / L2 / inner-product similarity. Production index has `m=16, ef_construction=64` on `vector_cosine_ops`.

28. **Why is audit_log dropped (CR-051)?** A single `_propagate_remit_status_to_claims()` UPDATE inflated audit_log to 7 M rows / 8 GB within days. No active downstream consumer existed.

29. **What is the AIR contract?** Architecture Impact Review — an 8-section document (functional / DB / query / storage / scalability / cross-cutting / rollback / op cost) + red-flag checklist required before any non-trivial change.

30. **What are the partial-index uses in this project?** `edi_files.deleted_at IS NULL`, `claims.service_from_date IS NOT NULL`, `parse_events.event_type IN ('segment_skipped', 'parse_error')`, `request_log.status_code >= 400`, etc.

31. **What is `pool_pre_ping`?** A SQLAlchemy engine option that issues `SELECT 1` before lending a pooled connection, surviving idle-connection drops on the remote PG cluster.

32. **What is `pool_recycle=1800`?** Connections older than 30 minutes are proactively replaced — defensive against remote cluster idle timeouts.

33. **Why are there 4 modifier slots in `claim_lines`?** v1 had 1; v2 preserves all 4 to support full modifier combinations (e.g., `25, 59, GP, KX` together).

34. **How does the system handle PG version differences?** Migrations 0006–0010 require pgvector. Local docker (pg16) ships with pgvector; remote (pg18.3) has 0.8.2 (with a known operator-crash bug — CR-009).

35. **Why is FE-level migration 0017 a denial-propagation migration?** Some freq=7 replacements have remittance denials but their freq=1 originals do not. The migration propagates `denied=1` from replacement to original to enrich training labels.

36. **What is migration 0018?** Adds `mv_payer_denial_rates_recent_2k` (last 2,000 claims per payer) and `mv_payer_denial_rates_90d` (last 90 days) for CR-126B recency features.

37. **What ENUMs does the schema use?** 18+ Postgres ENUMs covering file_type, parse_status, handler_status, claim_status, lifecycle_relationship, appeal_level, code_system, dx_code_system, policy_type, cms_doc_type, certification_type, correction_outcome, generation_type, user_feedback, job_status, tenant_isolation, user_role, artifact_type, parse_event_type.

38. **How are JSONB columns indexed?** GIN indexes on `claims.variant_data`, `claim_lines.line_data`, `parse_events.details`, `payer_policies.structured_rule`, RAG metadata columns.

39. **What is the soft-delete pattern?** A `deleted_at` timestamp on `edi_files`, `claims`, `patients`, `users`. Query MVs and indexes filter `deleted_at IS NULL`.

40. **What is the deferred FK pattern?** `edi_files.uploaded_by_user_id` and `appeals.created_by_user_id` are wired in migration 0004 *after* `users` is created. Required because they live in different domains.

### ML / Training
41. **What is the 70 / 15 / 15 split for?** Train (encoder fit + booster fit), Validation (isotonic calibrator fit + threshold selection), Held-out (final reported metrics, never touched during training). Introduced in CR-075.

42. **Why is calibration fit on VAL rather than OOF?** Because VAL comes from the same final booster used at predict-time. OOF comes from CV (auxiliary) — its distribution differs from production.

43. **What is `scale_pos_weight`?** XGBoost's class-weight parameter for imbalanced classification. Set to `n_neg / max(1, n_pos)` so the rare positive class is weighted equally.

44. **What is `eval_metric=logloss`?** Cross-entropy loss — the standard binary classification metric. The booster minimises this during training.

45. **What is `tree_method=hist`?** XGBoost's histogram-based split-finding algorithm. Faster than `exact` on tabular data, with negligible accuracy loss.

46. **What is `pred_contribs=True`?** XGBoost's flag that returns SHAP values per feature (plus a bias term) instead of probability. Used by reason_renderer.

47. **What is M1 strict column-order check?** A `FeatureSchemaError` raised if `list(df.columns) != FEATURE_COLUMNS_<variant>` at predict or train. XGBoost silently mis-attributes SHAP otherwise.

48. **What is leakage-safe target encoding?** sklearn `TargetEncoder(cv=5, target_type='binary', smooth='auto', random_state=42)`. Each train row's encoded value comes from a fold-out fit; predict-time uses the fully-fit encoder.

49. **What is `RarityState`?** A persisted snapshot of training vocabulary + per-value volume. Used to compute `unseen_*` and `is_rare_*` features at predict time.

50. **What is the precision-floor threshold algorithm?** Search t in {0.01, …, 0.98}; return max-recall t with precision ≥ 0.85; fallback to max-precision t if 0.85 unreachable. PRECISION_FLOOR constant lives in `features/constants.py`.

51. **What is `IsotonicRegression(out_of_bounds='clip', y_min=0.001, y_max=0.999)`?** Non-parametric monotonic regression. `clip` extrapolation prevents predictions outside [0.001, 0.999].

52. **What is the 20-point monotonicity check?** After fitting, transform `linspace(0, 1, 20)` and assert all diffs ≥ -1e-9. If non-monotonic, fall back to raw scores.

53. **What is Optuna's TPESampler?** Tree-structured Parzen Estimator — a Bayesian optimisation algorithm that models good vs bad hyperparameter regions and samples toward the good region.

54. **What is MedianPruner?** Optuna's pruning strategy that kills trials whose intermediate score is below the median of completed trials at that step. Saves compute.

55. **What objective does CR-079 tuning optimise?** `0.7 * (5-fold CV PR-AUC on TRAIN) + 0.3 * (single-fit PR-AUC on VAL)`. Balances stability and validation fit.

56. **What hyperparameters does the search space include?** n_estimators, max_depth, learning_rate, min_child_weight, gamma, subsample, colsample_bytree, colsample_bylevel, reg_alpha, reg_lambda, scale_pos_weight, max_delta_step.

57. **Why does dental need a fallback threshold?** Prevalence is 1.19 %. Even the perfectly-calibrated model rarely emits a score that meets precision ≥ 0.85 at a high recall threshold. Fallback to max-precision threshold (currently 0.34).

58. **Why is the home_care threshold 0.50 (not 0.75)?** After CR-112 raised it to 0.75, the HIGH bucket was empty in production because the calibrator output range peaked near 0.75. CR-114 moved it to 0.50.

59. **What does `feature_engineering_version` track?** Version of the FE schema (`v1.0.0`). Bumped when columns are added/removed/reordered.

60. **What does `model_version` look like?** `v1.fb.YYYYMMDDTHHMMSS.{variant}_{subtype}` — e.g., `v1.fb.20260624T044257.837P_healthcare`.

61. **How are the 5 artefact files structured?** `model.json` (XGBoost booster), `calibrator.joblib` (IsotonicRegression), `encoder.joblib` (LeakageSafeTargetEncoder), `rarity_state.joblib` (RarityState), `feature_schema.json` (column list + decision_threshold + metrics).

62. **What is the shadow logger?** A parallel writer that runs the *other* pipeline (FeatureBuilder ↔ simple_pipeline) on the same input and writes its prediction to `prediction_log` with the same `prediction_group_id`. Used for parity audits.

63. **What is the `simple_pipeline`?** A fallback model with fixed one-hot vocabulary (top-20 payers, top-30 CPTs, top-30 dx) and 90-ish features. Single model across all variants. Used when no FB bundle exists for a variant (e.g., institutional_other).

64. **How is feature drift detected at predict time?** `booster.feature_names` is compared to `list(X.columns)`. Hard-fail with `FeatureSchemaError` on any drift.

65. **What is the `_FB_PREDICTOR_CACHE`?** A process-local dict mapping `(variant, subtype) → predictor`. Lazily loaded; invalidated by `POST /api/predictions/reload-bundles`.

66. **What is `compute_leakage_safe_denial_rates`?** A function that, for each train row, computes `denied_count / volume` over earlier-dated TRAIN rows only (`merge_asof(allow_exact_matches=False)` on `service_from_date`). Used to override 8 MV-backed denial-rate features at train.

67. **Why isn't the leakage-safe override used at predict?** No prior training rows exist for the query claim; predict reverts to the global MV value.

68. **What is the Bayesian smoothing formula?** `(denied + α·prior) / (volume + α)` with α=25, prior=0.2772. Stabilises rates on low-volume payers.

69. **What is `_VARIANT_BUCKET_OVERRIDES`?** A dict mapping `(feature, subtype) → bucket` to re-route specific features per care setting (e.g., `total_units → history` on home_care).

70. **What are the 11 canonical buckets?** authorization, documentation, procedure, diagnosis, timely_filing, coverage, billing, provider, history, similar, general. (Tiers 0/0/0/0/1/1/1/1/2/2/2.)

### Backend / API
71. **What FastAPI version is used?** `>=0.115.0,<0.117.0`.

72. **How are routes organised?** `src/rcm/routers/public/*.py` (operator-facing) and `src/rcm/routers/dev/*.py` (developer console). `src/rcm/main.py` mounts them under `/api/`.

73. **What is `POST /api/edi/upload`?** Multipart file upload; returns `EdiUploadResponse` with parse status, entity counts, validation errors, pair status.

74. **What does `/api/predictions/predict-file/{id}` return?** `risk_summary` (HIGH/MEDIUM/LOW counts) + `high_risk_claims[]` with per-claim risk score + top denial reasons.

75. **What does `/api/recommendations/by-file/{id}` combine?** CARC adjustments (from remit), parser validator errors, and ML risk factors per claim.

76. **What is the `status_badge`?** Per-claim badge: `Resolved` (paid, no recs) / `Denied` (remit with CARC) / `High Risk` (HIGH ML prediction, no remit yet).

77. **How is async DB used?** SQLAlchemy 2.0 `create_async_engine` + `AsyncSession`. `parse_and_save`, all router endpoints, and `FeatureBuilder.fit_transform/transform` are async.

78. **What is the CORS configuration?** Open to `http://localhost:5173` (Vite). Configurable via `settings.CORS_ORIGINS`.

79. **What is the auth posture?** No authentication in the current phase. `users` + `passlib` + `python-jose` scaffolded for a future auth layer.

80. **What does `POST /api/predictions/reload-bundles` do?** Clears `_FB_PREDICTOR_CACHE`; returns available bundle inventory. Called after promotion.

### DevOps / tooling
81. **What launches the backend?** `PYTHONPATH=src uvicorn rcm.main:app --reload --port 8000` or `scripts/run_dev.{sh,ps1}`.

82. **Why `--reload`?** CR-081 — source edits under `src/rcm/` require either `--reload` or an explicit restart. The dev launcher enforces it.

83. **What launches the frontend?** `cd frontend && npm run dev` → Vite on `127.0.0.1:5173`.

84. **What is `docker-compose.yml`?** Defines `pgvector/pgvector:pg16` and `redis:7-alpine` with healthchecks.

85. **How are migrations applied?** `alembic upgrade head`. `env.py` overrides the static `alembic.ini` URL with `settings.DATABASE_URL`.

86. **What does `verify_db_connection.py` do?** Reports DB version, size, tables, extensions, key PG settings, alembic head. Exit 0 only if `vector` extension is installed.

87. **How are unit tests run?** `pytest tests/unit`. SQLite in-memory; no DB required.

88. **How are integration tests run?** `pytest tests/integration` with `RCM_INTEGRATION_DSN` env var. Skipped otherwise.

89. **Where do benchmarks live?** `scripts/bench_*.py`, standalone, runnable against local docker PG.

90. **What's the linting tool?** `ruff` (line-length 110, target-py312, select=E/F/I/W/UP/B/SIM/C4/RUF).

### Misc
91. **What is the FE schema_version?** `v1.0.0`. Bumped when columns change.

92. **How many parsers / handlers in total?** ~30 segment handlers across NM1, N1, PER, DMG, HL, SBR, CLM, SV1, SV2, SV3, HI, DTP, DTM, REF, CR1, CR3, CRC, TOO, DN1, DN2, PWK, AMT, NTE, CLP, CAS, LQ, SVC, MIA, MOA, N3, N4, PRV, PAT, LX.

93. **How many materialised views?** 14 (incl. CR-126B recency MVs in migration 0018).

94. **How many PL/pgSQL functions?** 4: `derive_service_variant`, `is_denied`, `claim_lifecycle_resolved`, `find_similar_claims`.

95. **How many production model bundles?** 3: 837P_healthcare, 837I_home_care, 837D_dental.

96. **How many universal feature columns?** 100 (post-CR-122B retirement of `frequency_code_encoded`).

97. **How many lifecycle features?** 11 (6 Stage 1 + 5 Stage 2).

98. **How many integration tests?** 9 against remote PG (plus 67 unit tests at parsing layer, plus 178 unit tests in total).

99. **What test fixtures exist?** 8 EDI files covering 837P healthy + therapy + transport, 837I home_care, 837D dental, 835 full, 837P no_DTP472, edge_no_ISA.

100. **What is the v2 file count?** ~29 parsing-layer source files (~3,500 LOC) + features (~2,000 LOC) + ML (~1,500 LOC) + routers (~1,000 LOC) + migrations (18 files).

## 7.2 Business questions (50)

1. **What is RCM?** Revenue Cycle Management — the end-to-end financial process from patient scheduling to final settlement.

2. **What is a denial?** A payer's refusal to pay an adjudicated claim (CLP02='4').

3. **What is a rejection?** A clearinghouse / payer front-end refusal *before* adjudication; no 835.

4. **Why does this matter financially?** Cost-per-denial $25–$118; revenue at risk in millions per month for a mid-sized provider.

5. **What is a CARC?** Claim Adjustment Reason Code — WPC-standardised code on the 835's CAS segment.

6. **What is a RARC?** Remittance Advice Remark Code — supplemental code on the 835's LQ segment.

7. **What is timely filing?** The payer's deadline for receiving a claim (commonly 90/180/365 days).

8. **What is prior authorisation?** Payer's pre-approval of a service before the encounter occurs.

9. **What is referral?** PCP-to-specialist authorisation, payer-mediated.

10. **What is medical necessity?** The payer's policy that diagnoses must support procedures (LCD / NCD / payer policy).

11. **What is NCCI?** National Correct Coding Initiative — CMS's PTP (procedure-to-procedure) edits and MUE (medically unlikely edits).

12. **What is HIPPS?** Health Insurance Prospective Payment System — code system used in home health, hospice, SNF.

13. **What is DRG?** Diagnosis-Related Group — inpatient grouping for payment.

14. **What is a place of service (POS) code?** A code identifying where the service occurred (office=11, hospital outpatient=22, telehealth=02/10).

15. **What is HIPAA?** Health Insurance Portability and Accountability Act — the U.S. privacy + transaction-standard law mandating X12 EDI.

16. **What is PHI?** Protected Health Information. The system has structlog filters that redact NPI / member_id / DOB patterns from logs.

17. **What is a payer?** The insurance entity (commercial: Aetna, UHC, Cigna; government: Medicare, Medicaid).

18. **What is a clearinghouse?** A middleware service that aggregates and routes EDI between providers and payers.

19. **What is the difference between billed and paid amount?** Billed = provider's charge; paid = payer's reimbursement (often less due to contractual adjustments).

20. **What is a contractual adjustment?** The discount between billed and contracted rate (CARC group code CO).

21. **What is patient responsibility?** Deductible + copay + coinsurance (CARC group code PR).

22. **What is the difference between an appeal and a resubmission?** Appeal = formal challenge of the denial; resubmission = corrected claim (freq=7).

23. **What is a write-off?** Revenue the provider has accepted will not be paid.

24. **What is a denial rate?** Denied claims / total adjudicated claims. Industry typical 5–25 %.

25. **What is "first-pass yield"?** Percentage of claims paid on first submission. Improves with pre-submission scrubbing.

26. **What is the impact of denial rework?** Industry studies: $25–$118 per claim, weeks of delay, ~67 % of denied claims never reworked.

27. **What is the target user?** RCM denial analysts, billing operators, provider IT, RCM admins.

28. **Why surface reasons in business language?** Operators are not data scientists; raw feature names like `paperwork_missing_when_required` need to become "Supporting documentation may be insufficient."

29. **What is actionability tier?** Whether a reason is directly actionable (auth, doc, procedure, dx), partially actionable (timely filing, coverage, billing, provider), or informational (history, similar, general).

30. **Why prioritise actionability over SHAP magnitude?** Operators should see the most fixable item first; CR-093 moved tier-A-first claims from 34.4 % to 94.4 %.

31. **What is "shadow logging"?** Running the alternate ML pipeline in parallel for parity comparison without affecting the user.

32. **What is the value of `prediction_log` audit fields?** Every prediction is traceable to its model version, FE version, calibrator version, threshold — defensible six months later.

33. **What is a replacement claim?** A claim with freq=7 that supersedes a prior claim (often corrected after a denial).

34. **What is "denial bucket"?** A canonical category for denial reasons (11 in this system) that operators can mentally model.

35. **What is the role of reference data?** Payer policies, NCCI edits, LCD coverage drive policy-specific features and validate claims pre-submission.

36. **What is `mv_drift_baselines`?** A snapshot of per-(variant, subtype) volume + denial prevalence; future use is drift monitoring.

37. **What is "claim lifecycle"?** The chain from original (freq=1) → replacement (freq=7) → adjudication → appeal.

38. **What is "corpus diversity"?** Variety of payers / CPTs / dx / providers in the training data. Dental is corpus-diversity-bound (CR-085).

39. **What is "model promotion"?** Moving a tuned bundle from candidate directory to the production directory + reloading the predictor cache.

40. **What is the difference between FB and simple_pipeline?** FB = leakage-safe target-encoded features per variant; simple_pipeline = fixed-vocab one-hot fallback across all variants.

41. **Why not predict every claim instantly on upload?** Predictions are run via explicit POST `/predict-file`. This lets operators batch + control timing.

42. **What is "calibration drift"?** Calibrator output diverges from true rates as data evolves (CR-112: 98.6 % predicted vs 51.6 % actual on home_care after a new cohort).

43. **What is the role of `mv_claim_labels`?** Single source of truth for training labels. Refreshed before every `/train` (CR-083).

44. **Why does the system separate "Resolved" from "Denied"?** Resolved claims have remittance with paid_amount > 0; denied claims have CLP02='4'. The badge tells the operator at a glance.

45. **What is the value of explaining `unseen_indicators`?** When the payer / CPT / dx was not in training, the prediction is less reliable. Surfacing this tells the operator to weigh the prediction accordingly.

46. **What is the future RAG layer for?** Retrieving payer policy + LCD passages similar to the claim and drafting an appeal letter / remediation grounded in source documents.

47. **What is the long-term scaling concern?** Partition rollover (currently manual), audit re-introduction (deferred since CR-051), and cluster pgvector stability (CR-009).

48. **Why does the project have an AIR contract?** To prevent CR-050/CR-052-style incidents where a "functionally correct" change inflated `audit_log` to 8 GB.

49. **Why is the dental ceiling structural, not model-side?** CR-085: 68/116 features constant on dental corpus; 67 % of dental claims lack diagnosis code; 7 ref tables empty.

50. **What is the next business milestone?** Adoption of recency features (CR-126B), and a RAG-grounded recommendation surface.

## 7.3 Architecture questions (50)

1. **What is the deployment topology today?** Local docker PG on `:5433`, Redis `:6379`, uvicorn `:8000`, Vite `:5173`. No production cloud yet.

2. **What is the migration topology?** 18 alembic revisions chained linearly from 0001 to 0018; `alembic upgrade head` is the only command.

3. **Why is the database authoritative on docker:5433 (not native PG)?** CR-082 cleanup: native PG held two orphaned legacy DBs (8.66 GB). v2 reclaimed disk + simplified ops.

4. **What is the developer console for?** Local-only observability surface (`/api/dev/*`); PHI toggle OFF by default, write endpoints require `?confirm=true`.

5. **How does the system handle PHI logging?** structlog + stdlib bridge with regex redaction for NPI / member_id / DOB / SSN-like patterns.

6. **What are the 9 model domains?** Ingestion, Claims Core, Variant Extensions, Remittance, Lifecycle, Reference, ML Pipeline, RAG, Operations.

7. **How are partitioned tables managed?** Manual monthly partitions in alembic (no pg_partman). 4 future months pre-created at migration time.

8. **What is the partitioning strategy for `prediction_log`?** RANGE on `prediction_time`, monthly child partitions, composite PK `(id, prediction_time)`.

9. **What is the dispatcher-registry pattern in parsing?** `HANDLER_REGISTRY[(variant, segment_name)] → handler`. Handlers register via side-effect imports. Dispatcher invokes; broad exception catch.

10. **Why this rather than a switch / if-else chain?** Adding a new handler is one file + one decorator. Reduces churn in the central dispatcher.

11. **What is the per-variant routing strategy?** Variant detected from GS08 up front. Subtype derived AFTER segment loop (needs CPT / revenue codes / modifiers).

12. **How is the (variant, subtype) → predictor mapping done?** `_FB_PREDICTOR_CACHE[(variant, subtype)]` lazy-loads from `artifacts/featurebuilder/<variant>_<subtype>/`.

13. **What is the global fallback?** When the (variant, subtype) tuple is unknown, the registry routes to `_global` with 100 universal columns. Guarantees no crashes.

14. **How does the artifact bundle work?** Five files: `model.json`, `calibrator.joblib`, `encoder.joblib`, `rarity_state.joblib`, `feature_schema.json`. `ModelArtifactBundle.load(path)` returns a bundle; predictor wraps it.

15. **What is the bundle promotion contract?** Dry-run → preview rollback inventory → atomic file move → reload-bundles endpoint → confirm. Enforced by `scripts/cr079_promote.py`.

16. **What is the prediction logging contract?** Every prediction writes one row to `prediction_log` with model/FE/calibrator versions, threshold, top risk factors, unseen indicators, pipeline name, prediction group id.

17. **What is the shadow logging contract?** Production path runs; shadow logger spawns parallel alternate pipeline; both rows share `prediction_group_id`; failures isolated.

18. **What is the MV refresh contract?** `mv_claim_labels` first; cascading `REFRESH CONCURRENTLY` for dependents. CR-083 refreshes before every `/train`. CR-090 debounces background refresh after uploads.

19. **What is the encoder persistence contract?** Per-column state (encoder, vocabulary, global_mean, n_training_rows) saved via joblib. Single bundle file (`encoder.joblib`).

20. **What is the validator contract?** `run_all(ctx, session=None)` runs four tiers; ERROR-severity promotes `dropped=True`. Tier 3 gracefully skipped when no session.

21. **What is the FeatureBuilder contract?** Construct with `(variant, subtype, include_lifecycle)`. `fit_transform(session, df, y, safe_rates=…)` returns `FeatureArtifacts`. `transform(session, df)` returns DataFrame.

22. **What is M1?** Strict column-order check. Hard-fail on missing / extra / re-ordered columns at predict and train.

23. **How is M1 enforced across variants?** Each variant's `FEATURE_COLUMNS_<X>` tuple in `registry.py`. `validate_feature_frame(df, variant, subtype, …)` compares against expected.

24. **What is the leakage-safe override architecture?** Train-time: per row, denial rates from earlier-dated TRAIN rows. Predict-time: global MV value.

25. **What is the recency Bayesian smoothing architecture?** Raw volume + denied_count stored in MVs (0018); Python applies `(d + αp) / (v + α)` at FE time. Allows tuning constants without migration.

26. **What is the `pending_claim_pairs` registry for?** Silent tracking of original ↔ replacement linkage; avoids false "no replacement yet" warnings.

27. **Why are RAG tables in their own domain?** Separation of concerns; vector indexes are HNSW-specific; pgvector cluster crash bug (CR-009) is isolated.

28. **What is the cascade behaviour of FKs?** Every FK has explicit CASCADE or SET NULL. Zero FKs at NO ACTION default (verified in CR-008).

29. **What FKs cross partitioned boundaries?** None — partitioned children can't be FK targets without including the partition key. Logical id references only.

30. **How does the system survive remote PG idle-connection drops?** `pool_pre_ping=True` + `pool_recycle=1800`.

31. **What is the role of `arq` + Redis?** Scaffolded for background jobs (e.g., MV refresh worker). Not heavily wired yet.

32. **What is the role of the `users` + `tenants` tables today?** Scaffolded for a future auth + multi-tenancy layer. Not wired into request flow.

33. **How does the dev console relate to production?** It's local-only, observability-focused. Not for production users. PHI toggle OFF; write endpoints gated.

34. **What is the relationship between FeatureBuilder and the trainer?** Trainer uses `builder.fit_transform` on TRAIN; the same builder reused on VAL + HELD-OUT via `transform`. Persisted to bundle.

35. **What is the relationship between predictor and reason_renderer?** Predictor returns top-15 SHAP factors; renderer maps feature → bucket → variant-aware sentence → top-5 actionability-ordered list.

36. **What is the relationship between `denial_buckets.py` and `reason_renderer.py`?** denial_buckets maps CARC/RARC → bucket; reason_renderer maps feature → bucket. Both target the same 11 canonical buckets — single explanation surface.

37. **What is the deployment story for a new variant?** Add `_<NEW>_VARIANT` to registry; create category module if needed; train via `/train`; save bundle to `artifacts/featurebuilder/<variant>_<subtype>/`; reload.

38. **What is the rollback story for a bad promotion?** Bundle directories are versioned (timestamp in path). `scripts/cr079_promote.py` previews rollback inventory; manual git revert + file restore + reload.

39. **What is the artefact dependency tree?** `feature_schema.json` is the contract — references `model.json`, `calibrator.joblib`, `encoder.joblib`, `rarity_state.joblib`. `ModelArtifactBundle.load` reads all four together.

40. **How is partitioning verified end-to-end?** `EXPLAIN` on partition-pruning queries confirms only relevant child partitions are scanned (CR-008).

41. **How is the prediction cache invalidated?** `POST /api/predictions/reload-bundles` clears `_FB_PREDICTOR_CACHE` and returns the available-bundles inventory.

42. **How is FE version drift handled?** Bumping `FEATURE_ENGINEERING_VERSION` is a manual decision when columns change. Production bundles record the version they were trained with.

43. **How are 4-tier validators ordered?** Tier 1 always first (structural); Tier 2 IG; Tier 3 payer (may skip if no session); Tier 4 business.

44. **What is the relationship between `simple_pipeline` and the variant FBs?** simple_pipeline is the fallback when no FB bundle exists or for variants without a registered block (e.g., institutional_other). It's also the shadow predictor for CR-065 parity.

45. **What is the role of `mv_drift_baselines`?** Per-(variant, subtype) volume + prevalence snapshot at training time. Future use: drift dashboard.

46. **What is the role of `feature_snapshots`?** Persisted per-training-row feature vectors. Future use: re-attribution and drift analysis.

47. **What is the role of `model_artifacts`?** Inventory of file artefacts on disk per training run. Future use: garbage collection + rollback.

48. **What is the role of `correction_examples`?** Future RAG few-shot table: original + corrected claim pairs as training examples for the LLM.

49. **What is the role of `rag_generations`?** Future RAG output log: prompt, retrieved chunk ids, model name, generated text, user feedback, tokens, latency, cost.

50. **What is the role of `appeals.generated_letter` + `human_edits` + `final_letter`?** Future LLM-drafted appeal letters with operator edits; diff captured for use as correction examples.

## 7.4 ML questions (50)

1. **What is the prediction problem?** Binary classification: will this 837 claim be denied (`CLP02='4'` in the subsequent 835)?

2. **What are the production models?** Three: 837P/healthcare, 837I/home_care (with lifecycle), 837D/dental. Plus simple_pipeline fallback.

3. **Why per-variant models?** Variants have different feature distributions and denial drivers. One model would underperform on at least one.

4. **What are the feature counts per variant?** 837P healthcare: 99; 837I home_care: 71 (or higher post-CR-122B retrain — 122 with lifecycle); 837D dental: 74.

5. **What are the held-out metrics?** Healthcare ROC-AUC 0.9979 / PR-AUC 0.9896 / F1 0.9597; home_care 0.9962 / 0.9807 / 0.9056; dental 0.9528 / 0.3014 / 0.2222.

6. **Why is dental PR-AUC so low?** 1.19 % prevalence + corpus-diversity ceiling. Precision-floor of 0.85 unreachable.

7. **What is the calibration quality?** Brier 0.002–0.018 across variants. Isotonic monotonicity test passes.

8. **Why isotonic rather than sigmoid?** Non-parametric; handles bi-modal label distributions; better-calibrated on the home_care corpus.

9. **What is the leakage risk that CR-107 fixed?** 8 MV-backed denial-rate features had no temporal filter; 99.1 % of train rows shared cohorts with future rows.

10. **How was the leakage fix verified?** Held-out ROC moved from 0.997+ to 0.997+ (small) and PR-AUC stayed strong. Production / recent-sample ROC unchanged.

11. **What is overfitting?** Model fits training noise rather than signal; held-out metrics degrade. Mitigated by reg_alpha, reg_lambda, early stopping, subsample, colsample_bytree.

12. **What is underfitting?** Model is too simple to capture signal; both train and held-out are poor. Mitigated by deeper trees, more estimators.

13. **What is target encoding?** Replace a categorical with the target mean for that category. Powerful for high-cardinality features (payer name, CPT code).

14. **What is the leakage risk of target encoding?** Encoding using the row's own label leaks future information. Mitigated by 5-fold OOF: encode each row using folds that don't contain it.

15. **What is `smooth='auto'`?** sklearn's auto-determined smoothing factor. Small-category means are pulled toward the global mean to reduce variance.

16. **What is `cv=5`?** Five-fold cross-validation for OOF prediction.

17. **What is a held-out set?** A slice never seen during training, validation, or calibration. Used for final honest reporting.

18. **What is data drift?** Production data distribution diverges from training. Detected via `unseen_indicators`, `mv_drift_baselines`, and held-out re-evaluation.

19. **What is concept drift?** The relationship between features and label changes (e.g., payer suddenly denies more after a policy change). Mitigated by retraining + recency features.

20. **What is class imbalance?** Positive class is rare. Mitigated by `scale_pos_weight = n_neg / n_pos`.

21. **What is the PR curve?** Precision-Recall curve. Y-axis precision, X-axis recall. Robust to class imbalance.

22. **What is the ROC curve?** Receiver Operating Characteristic. Y-axis TPR, X-axis FPR. Less informative on imbalanced data.

23. **What is the Brier score?** Mean squared error of probability vs label. Lower is better-calibrated. 0.002 = excellent.

24. **What is ECE?** Expected Calibration Error. Average gap between predicted probability and observed frequency across bins. Lower is better.

25. **What is `decision_threshold`?** The cutoff above which `predicted_label=1`. Set per variant via precision-floor algorithm.

26. **What is the role of `LOW_PROB_CUTOFF`?** Constant (0.05) separating LOW from MEDIUM risk. MEDIUM = score ∈ [0.05, threshold).

27. **What is SHAP?** SHapley Additive exPlanations — Shapley-value-based per-feature contribution. Sum + bias = predicted score.

28. **What is TreeSHAP?** Efficient SHAP algorithm specific to tree ensembles (XGBoost, LightGBM, RF). Polynomial complexity in tree size.

29. **What is the difference between feature importance and SHAP?** Importance is global (across the whole training set); SHAP is per-row (what mattered for *this* claim).

30. **What are some examples of high-importance features?** `payer_overall_denial_rate`, `payer_cpt_denial_rate`, `cpt_dx_denial_rate`, `is_replacement_claim`. After CR-088: `cpt_category_encoded`, `primary_dx_chapter_encoded`. After CR-126 on 837I: `payer_overall_denial_rate_recent_2k_smoothed` (12.28 % gain).

31. **What is the cold-start problem?** When the training set has very few minority examples; CV folds don't have enough positives. Mitigated by clamping cv to minority count (CR-036).

32. **What is the unseen-category problem?** Predict-time category not in training vocabulary. Mitigated by falling back to training global mean + setting `unseen_<col>=1`.

33. **What is the missing-data problem?** Categorical = sentinel "MISSING"; numeric = median or 0; per-column safe defaults; `missing_count` is a feature.

34. **What is the reproducibility story?** Fixed `random_state=42`; M1 column-order check; persisted encoder + rarity_state; model_version + FE_version + calibrator_version recorded per prediction.

35. **What is the difference between training and predict-time encoding?** Train: 5-fold OOF. Predict: full-fit deterministic transform. Persistent state ensures consistency.

36. **What does `out_of_bounds='clip'` mean in IsotonicRegression?** At predict, if a raw score is outside the calibrator's training range, clip to nearest endpoint instead of extrapolating.

37. **What is the role of `n_estimators=200`?** Number of trees in the ensemble. Higher = more capacity, risk of overfitting; lower = simpler model.

38. **What is `max_depth=5`?** Tree depth limit. Default for tabular data; deeper risks overfitting.

39. **What is `learning_rate=0.05`?** Per-tree contribution shrinkage. Lower = more conservative.

40. **What is `min_child_weight`?** Minimum sum of instance weight per child node. Prevents over-fragmented trees.

41. **What is `gamma`?** Minimum loss reduction for split. Higher = more conservative.

42. **What is `reg_alpha` / `reg_lambda`?** L1 / L2 regularisation on leaf weights.

43. **What is `subsample`?** Fraction of rows used per tree. < 1.0 = stochastic gradient boosting; reduces overfitting.

44. **What is `colsample_bytree` / `colsample_bylevel`?** Fraction of columns per tree / per level. Reduces overfitting.

45. **What is `max_delta_step`?** Limit on the change in leaf output per step. Useful for highly-imbalanced data.

46. **What is the role of `eval_metric=logloss`?** Cross-entropy loss; the booster minimises this during training.

47. **What is the role of early stopping?** Stop training when held-out metric stops improving. Not used in current trainer (fixed n_estimators).

48. **What is the role of cross-validation in CR-079?** Inside each Optuna trial, compute 5-fold CV PR-AUC on TRAIN; aggregate with single-fit PR-AUC on VAL (70 % / 30 % weights).

49. **What is the role of MedianPruner?** Optuna pruner that kills trials whose intermediate score is below the median of completed trials. Saves compute.

50. **What's next on the ML roadmap?** Adopt recency features (CR-126B), revisit per-payer fine-tuning, causal feature evaluation, active learning loop.

---

# SECTION 8 — Presentation preparation

Four scripts in order of escalating depth. Each is written to stand alone — use the one that matches the time slot you're given.

## 8.1 Two-minute pitch

> The project is **Universal RCM Denial Management v2** — a multi-variant denial-prediction and recommendation platform for U.S. healthcare claims. It ingests X12 EDI 837 claim files (professional, institutional, dental) and 835 remittance files, parses every relevant segment, and runs each claim through a per-variant XGBoost model that outputs a calibrated denial probability and a top-5 list of plain-language remediation reasons.
>
> The system is built around lessons baked into the schema: NULLABLE service dates (no `date.today()` placeholders), a four-tier validator chain (structural / IG / payer / business), and a strict column-order check at predict time so XGBoost doesn't silently misattribute SHAP. Predictions are calibrated using isotonic regression and use a precision-floor threshold that maximises recall subject to ≥ 85 % precision.
>
> Production held-out metrics are ROC-AUC 0.998 (healthcare), 0.996 (home care), 0.95 (dental); reasons are mapped from raw SHAP features into 11 canonical denial buckets sorted by actionability tier — so the first thing an operator reads is also the most fixable. The recommendation engine fuses CARC codes from the actual remit, parser-validator errors, and ML SHAP reasons into one composite per-claim recommendation list.
>
> My contribution was on the feature-engineering and ML tuning track: leakage-safe target encoding, lifecycle features for replacement claims, recency features for payer drift, Optuna hyperparameter tuning, isotonic calibration, threshold derivation, and the SHAP-to-bucket reason renderer.

## 8.2 Five-minute pitch

(All of the above, plus.)

The architecture has three layers. The **parsing layer** uses an envelope decoder with a four-encoding fallback chain (utf-8-sig → utf-8 → cp1252 → latin-1) and an ISA-presence sanity check; ~30 segment handlers dispatched via a `(variant, segment_name)` registry; and a 4-tier validator chain that promotes ERROR-severity entries to a `dropped=True` flag — dropped claims still persist their raw segments for audit, but never reach training or prediction.

The **feature engineering layer** is a `FeatureBuilder` that assembles 14 categories (A coverage, B authorization, C clinical, D coding, E timely filing, F documentation, G patient history, H provider profile, I joint encoders, J base claim, K target-encoded categoricals, L rarity / unseen / missing, Z reference data availability, and M variant-specific). It uses a leakage-safe target encoder (5-fold out-of-fold at train, deterministic at predict) and enforces a strict column-order check so XGBoost SHAP attributions stay correct. For 837I home_care it also includes 11 lifecycle features that compare a freq=7 replacement to its freq=1 original — auth added, modifier added, diagnosis changed, etc.

The **ML layer** runs the standard XGBoost classifier with `tree_method=hist`. After a 70/15/15 stratified split, training fits the booster on TRAIN, calibrator on VAL (isotonic with monotonicity guard), and selects the threshold via precision-floor. CR-079 added Optuna Balanced tuning; CR-107 added leakage-safe denial-rate features; CR-117/118/120 added lifecycle features and promoted them for 837I only; CR-126 evaluated recency features and found 2,000-claim windowed payer denial rates yield the largest gain on 837I.

The recommendation engine combines CARC codes from the 835, parser validator errors, and ML SHAP reasons. SHAP outputs are mapped through 11 canonical denial buckets (authorization, documentation, procedure, diagnosis, timely_filing, coverage, billing, provider, history, similar, general) with variant-aware sentence overrides and actionability-tier sorting — directly-fixable reasons surface first.

The system is local-first today (docker PG + uvicorn + Vite) but is built for a cloud deployment: pool_pre_ping for idle drops, async DB throughout, monthly-partitioned tables, materialised views with concurrent refresh, AIR (Architecture Impact Review) discipline before every non-trivial change.

## 8.3 Ten-minute presentation outline

**Slide 1: Title.** Universal RCM Denial Management v2 — multi-variant denial prediction platform.

**Slide 2: Problem.** US healthcare submits billions of claims/year; 5–25 % denied; cost per denial $25–$118. Existing tools are single-variant or single-payer. v2 unifies 837P/I/D + 835 with leakage-safe ML and explainable recommendations.

**Slide 3: Architecture (high level diagram).** Frontend (React 18 + Vite + Tailwind) → FastAPI backend → PostgreSQL + pgvector. Parsing / feature engineering / ML modules.

**Slide 4: Data pipeline.** 837 upload → parse → validate (4 tiers) → persist → MV refresh → predict → recommend. 835 upload → parse → link to claim by `claim_number` → back-fill `prediction_log`.

**Slide 5: Database (simplified ER).** 41 base tables, 14 MVs, 4 PL/pgSQL functions, partitioned tables for raw_segments / parse_events / prediction_log / request_log. Soft-delete on edi_files / claims / patients / users. Lesson-driven nullability for service_from_date + remittance_date.

**Slide 6: Feature engineering.** 14 categories (A–Z + M variant), 100 universal + 4–11 variant-specific + 11 lifecycle features. Leakage-safe target encoding + strict M1 column-order check. RefDataLookup loader populated by CR-087/088 (HCPCS + ICD-10-CM from CMS).

**Slide 7: ML pipeline.** XGBoost per-variant. 70/15/15 split. Isotonic calibration on VAL with monotonicity guard. Precision-floor threshold (≥ 85 % precision, max recall). Held-out: 0.998 / 0.996 / 0.953 ROC-AUC.

**Slide 8: Explanations + recommendations.** SHAP top-15 → 11 canonical buckets → variant-aware sentence → actionability-tier sort → top-5 to UI. Composite recommendations: CARC + parser + ML.

**Slide 9: Major contributions (CR timeline highlights).** CR-079 Optuna tuning; CR-088 ref-data activation (+3.4 pp F1 on healthcare); CR-107 leakage-safe training; CR-120 lifecycle promotion for 837I (FPR −46 pp); CR-126 recency-window shootout (recent_2k → +12.28 % gain on 837I).

**Slide 10: Results, future, and Q&A.** Current production state, calibration quality, dental ceiling attribution, future RAG layer, auth + multi-tenancy roadmap.

## 8.4 Twenty-minute talk (section-by-section)

**Section 1 (2 min). Why this project.** US healthcare denial economics; v1 limitations; v2 design goals (multi-variant, leakage-safe, AIR discipline).

**Section 2 (2 min). RCM domain primer.** Claim lifecycle, EDI 837/835, frequency codes, CARC/RARC, denial vs rejection, replacement vs appeal.

**Section 3 (3 min). System architecture.** Frontend → backend → DB. Module map: parsing → features → ml → routers. Async stack. Lessons baked into schema (C3, P1, P2, P3, M1, H5/H6).

**Section 4 (3 min). Parsing layer deep-dive.** Envelope hardening, encoding fallback, ~30 handlers, 4-tier validators, persistence with insert-then-catch dedup, reparse workflow. Performance numbers from CR-019 → CR-025 (parse ~3.4k/s, persist ~3k/s).

**Section 5 (3 min). Feature engineering deep-dive.** 14 categories, 7 variant blocks, leakage-safe target encoding (OOF train, deterministic predict), RarityState, M1 strict column-order check, RefDataLookup graceful degradation. Walk through one example feature per category.

**Section 6 (3 min). ML pipeline.** 70/15/15 split, XGBoost defaults, scale_pos_weight, isotonic calibration with monotonicity guard, precision-floor threshold. Optuna search space + objective.

**Section 7 (2 min). Lifecycle + recency.** Why FB space had no info on original-vs-replacement diff; 11 lifecycle features; freq=1-only vs freq=1+freq=7 training strategy; 837I-only promotion; recency-window shootout outcome.

**Section 8 (1 min). Explanation layer.** SHAP top-15 → 11 buckets → variant-aware sentence → actionability tier sort. Show before/after of the CR-093 change (34.4 % → 94.4 % tier-A-first).

**Section 9 (1 min). Recommendation engine.** Composite CARC + parser + ML; status_badge (Resolved / Denied / High Risk); outcome tracking via prediction_log + remit linkage.

**Section 10 (close, 1 min).** Achievements summary; gaps; future work; thanks.

---

# SECTION 9 — Achievements (quantified)

## 9.1 Major optimisations

| Optimisation | CR | Quantified impact |
| --- | --- | --- |
| Persistence rewrite (bulk Core inserts with RETURNING) | CR-024 | Persist throughput ~134 → ~3,000 claims/s at 10 k scale |
| Skip per-segment `segment_handled` events | CR-020 | Parse 3,290 → 3,396 claims/s (+3 %); peak memory 60.12 → 47.68 MB (−20 %) |
| Auto-debounced MV refresh | CR-090 | 200-file bulk = 1 refresh, ~5 s after last write |
| `mv_claim_labels` refresh-before-train | CR-083 | Eliminated silent stale-corpus training |
| Bundle reload endpoint + dev `--reload` enforcement | CR-081 | Removed need for backend restart on promotion |
| `audit_log` removal | CR-051 | Reclaimed ~8 GB; future writes ≈ 0 |
| Legacy DB cleanup | CR-082 | Reclaimed 8.66 GB |

## 9.2 Feature engineering improvements

| Improvement | CR | Impact |
| --- | --- | --- |
| FeatureBuilder cutover (primary inference) | CR-067 | Unlocked per-variant features + leakage-safe encoding |
| Reference data activation (CPT category + DX chapter) | CR-088 | Healthcare F1 +3.4 pp (0.9457 → 0.9798) |
| Tier-A dead-feature retirement | CR-104 | 108 → 101 universal columns; expected ΔROC ≈ 0 |
| Retire `frequency_code_encoded` | CR-122B | 101 → 100 universal columns; 837D threshold 0.01 → 0.34 (MEDIUM bucket reachable) |
| Leakage-safe denial-rate override | CR-107 | Honest held-out metrics (837P 0.9974 → 0.9970, 837D 0.7811 → 0.7841, 837I 0.8952 → 0.8991) |

## 9.3 Lifecycle feature work

| Item | CR | Impact |
| --- | --- | --- |
| Stage 1 (6 features) compute path | CR-117 | 352/600 replacements have resolvable original; 28/600 prior denial; 252/600 auth added |
| Stage 2 (5 correction-deltas) | CR-118 | `correction_action_count > 1` from 0.3 % to 2.7 % on freq=7 sample (8× lift) |
| Variant-specific promotion (837I only) | CR-120 | HIGH-bucket FPR on replacements 0.989 → 0.527; precision +12 pp; F1 +12 pp |
| Post-promotion verification | CR-121, CR-121B | Reproduces all CR-120A measurements; 4/11 features alive at booster (combined 3.39 % gain) |

## 9.4 Recency feature work

| Item | CR | Impact |
| --- | --- | --- |
| Payer denial-rate audit | CR-124 | Confirmed lifetime-only features; identified suspicious clusters; informed CR-126 scope |
| Recency-window shootout | CR-126 | recent_2k wins 837I (12.28 % gain); 90d wins 837P (0.88 %). 837I freq=7 FPR 0.527 → 0.052 (−47.5 pp); F1 +7 pp |
| CPT/DX recency audit | CR-126C | Baseline documentation for future scope |
| Migration 0018 (recency MVs) | CR-126B | `mv_payer_denial_rates_recent_2k` + `mv_payer_denial_rates_90d` |

## 9.5 Model quality improvements (held-out)

| Variant | Before tuning | After tuning | Δ |
| --- | --- | --- | --- |
| 837P healthcare F1 | 0.9457 (pre-CR-088) | 0.9597 (post-CR-088 + tuning) | +1.4 pp |
| 837P healthcare PR-AUC | ≈0.97 | 0.9896 | ≈+2 pp |
| 837I home_care F1 (post-tuning) | 0.8915 (CR-084B) | 0.9056 | ≈+1.4 pp |
| 837I home_care F1 (post-lifecycle) | 0.728 → 0.851 precision; F1 +12 pp on audit cohort | — | — |
| 837I home_care (post-recency) | F1 0.919 → 0.992 on freq=7 audit | — | +7 pp |

## 9.6 Calibration improvements

| Item | CR | Impact |
| --- | --- | --- |
| Isotonic recalibration on home_care after CR-109 ingest | CR-112 | Brier −26 %; ECE −91 %. Calibrator no longer 47 pp over-confident |
| Threshold correction 0.75 → 0.50 | CR-114 | HIGH bucket repopulated; recall 100 % on audit cohort; FPR −46 pp |
| Monotonicity guard | CR-076 | Non-monotonic calibrators rejected; fallback to raw scores recorded |

## 9.7 Explanation-layer improvements

| Item | CR | Impact |
| --- | --- | --- |
| Variant-aware sentence overrides | CR-078A | 8 (bucket, subtype) overrides — care-setting-correct language |
| Canonical denial buckets | CR-092 | History-bucket alignment 0/7 → 4/14; raw-feature leakage 6 → 0 |
| Actionability-tier sort | CR-093 | Tier-A-first 34.4 % → 94.4 %; Tier-C-first 17.6 % → 0 % |

---

# SECTION 10 — Report assets

## 10.1 Screenshots required (capture from the running app)

- [ ] Upload page with file drop zones (blue 837 + green 835), both empty.
- [ ] Upload page after a 837 upload completes — green check, claims/lines/diagnoses counts visible.
- [ ] Upload page after parse with validation errors — red banner, first 50 errors listed.
- [ ] HighRiskList panel expanded, showing 3–5 reason cards per claim (CARC / Parser / ML badges).
- [ ] RecommendedFixesPanel showing a Denied claim with status badge.
- [ ] RecommendedFixesPanel showing a Resolved claim with green check.
- [ ] TrainModelCard — pre-training state (dataset stats visible).
- [ ] TrainModelCard — during training (spinner).
- [ ] TrainModelCard — post-training (per-variant metrics block).
- [ ] TrainingHistoryCard — multiple runs grouped by 60-second window.
- [ ] ClaimsPage — sortable table with status filter dropdown applied.
- [ ] ClaimsPage — pagination controls.
- [ ] ClaimDetailPage — header with status badge + DenialRiskCard.
- [ ] ClaimDetailPage — DenialRiskCard showing top-5 reasons + unseen indicators.
- [ ] ClaimDetailPage — service lines table + diagnoses table.
- [ ] ClaimDetailPage — remittance section with adjustments + remark codes.
- [ ] (Optional) Dev console — Environment page showing PG version, extensions, settings, alembic head.
- [ ] (Optional) Dev console — Parsing Telemetry page.

## 10.2 Diagrams required

- [ ] High-level architecture (frontend → backend → DB) — Section 3 of PROJECT_KNOWLEDGE_DUMP.
- [ ] Data flow diagram (Patient → 837 → Parse → Validate → Persist → Predict → Recommend → 835 → Outcome).
- [ ] Simplified ER diagram — Section 4 of this document.
- [ ] Migration timeline (0001 → 0018) — Section 5 of PROJECT_KNOWLEDGE_DUMP.
- [ ] Feature engineering layer block diagram — 14 categories + 7 variants + global fallback.
- [ ] ML training workflow swimlane (corpus → split → fit_transform → XGBoost → isotonic → threshold → save bundle).
- [ ] Prediction workflow swimlane (claims → group by variant → predictor → SHAP → reason renderer → response).
- [ ] Recommendation engine block diagram — CARC + parser + ML → composite list.
- [ ] Lifecycle feature compute diagram (freq=1 original ↔ freq=7 replacement diff).

## 10.3 Architecture diagrams required (more detailed than block diagrams)

- [ ] Layered architecture (frontend layer / API layer / domain layer / data layer / infrastructure layer).
- [ ] Deployment topology (docker-compose: pgvector PG, Redis, uvicorn, Vite).
- [ ] Module dependency graph for `src/rcm/` (which modules import which).
- [ ] FeatureBuilder pipeline (RefDataLookup → snapshots → encoders → categories → variant block → lifecycle).
- [ ] Artifact bundle structure (5 files per bundle directory).

## 10.4 Flowcharts required

- [ ] Parse-and-save flow (with branches for: duplicate file, validation drop, persist success).
- [ ] Training flow (with branches for: too few rows, monotonicity fail, threshold floor unreachable).
- [ ] Prediction flow (with branches for: unknown variant → global fallback, unseen categories → fallback + indicator).
- [ ] Recommendation composition flow (CARC found → parser found → ML reasons available → compose).

## 10.5 Database diagrams required

- [ ] Full ER (use a tool like DBeaver / pgAdmin export against the live schema).
- [ ] Partitioning diagram for `raw_segments` / `parse_events` / `prediction_log` / `request_log`.
- [ ] Materialised view dependency tree (`mv_claim_labels` → 13 dependents).

## 10.6 Tables / charts for the report body

- [ ] Production held-out metrics table (Section 12 of PROJECT_KNOWLEDGE_DUMP).
- [ ] Feature category map table (Section 8 of PROJECT_KNOWLEDGE_DUMP).
- [ ] CR timeline table (Section 9 of PROJECT_KNOWLEDGE_DUMP — pick the 10 most relevant for the intern's work).
- [ ] Achievements quantified table (Section 9 of this document).
- [ ] Hyperparameter defaults vs Optuna search space (Section 7 of PROJECT_KNOWLEDGE_DUMP).
- [ ] Confusion matrix per variant (compute from `feature_schema.json` metrics — n_held_out × precision/recall).
- [ ] Calibration plot per variant (reliability diagram — compute from production prediction_log + back-filled outcomes if available).
- [ ] ROC curve per variant (need stored OOF/VAL/HELD-OUT predictions — may not be on disk; see Gaps).

---

# SECTION 11 — Gaps (information still to be collected)

## 11.1 Missing information

- **Exact contribution attribution.** Need explicit confirmation from the mentor / org chart of which CRs the intern executed personally vs reviewed vs consumed. The CHANGELOG records changes, not authorship.
- **Person-hours per CR.** Useful for the "Effort breakdown" appendix some universities require. Approximate from commit timestamps and CR scope; ideally cross-check with the intern's daily log.
- **Mentor + organisation details.** Internship report templates usually require a paragraph on the host company (SBNA Software), team name, mentor's name + designation, project's official internal name.
- **Joining + ending dates.** Confirmed at the report opening; not deducible from the repo.
- **Final F1 / ROC at report-submission time.** May change between 2026-06-24 (the date of this dataset) and the actual submission date. Capture a fresh `feature_schema.json` per bundle on submission day.

## 11.2 Unknown / not-yet-captured metrics

- **Held-out PR-AUC + ROC curves as image assets.** Computable, but the OOF/VAL/HELD-OUT scores would need to be re-saved (currently only summary metrics are in `feature_schema.json`).
- **Calibration plot (reliability diagram) per variant.** Not currently on disk; can be regenerated post-hoc if VAL predictions are re-computed (5 min on the local docker DB).
- **Confusion matrix per variant at production threshold.** Computable from precision + recall + n; useful for the report.
- **Per-feature gain ranking per variant.** Available via `booster.get_score(importance_type='gain')`; not currently on disk per bundle.
- **Drift between current production and the next retrain.** Will be unknown until the next `/train`; capture both metric sets and report the delta if applicable.
- **End-to-end latency numbers.** Parser, FE, predict, render. Capturable via a single bench script.

## 11.3 Missing screenshots

(Same list as Section 10.1 — none are on disk; the intern needs to start the local app and capture each one.)

## 11.4 Missing diagrams

(Section 10.2 / 10.3 / 10.4 / 10.5 — all need to be drawn. Suggested tool: draw.io, Excalidraw, or PlantUML for sequence + activity diagrams.)

## 11.5 Missing datasets

- **No real PHI / production claims data in the repo** (correctly so). The corpus is synthetic + de-identified; the report should describe corpus shape (size, prevalence) and explicitly note that PHI is excluded.
- **No CMS-sourced raw HCPCS / ICD-10-CM files in the repo** (CR-087 loaded them into the DB; the raw downloads are not committed). If asked for "data sources", point to the CMS public URLs in the CR-087 entry.
- **No payer-policy structured rules** (`payer_policies.structured_rule`) populated for most payers. The system supports Tier-3 validation but the rule corpus is sparse; this should be flagged as a future-data gap, not a missing implementation.

## 11.6 Risk areas before submission

- **Confirm the version of every artefact on submission day** — `model_version`, `feature_engineering_version`, `calibrator_version`, `decision_threshold`. Quote them in the report from `feature_schema.json`, not from memory.
- **Confirm CHANGELOG completeness up to the submission date.** New CRs may land between 2026-06-24 and the report deadline.
- **Confirm AIR contract examples.** If the report is going to cite "AIR discipline" as a methodology contribution, attach 1–2 AIR documents as appendices. They are not in the repo today; the intern should write or recover them from the CHANGELOG.
- **Confirm that the dataset description in the report is sanitised** — no patient names, no real NPIs, no real claim numbers from any non-synthetic source.

## 11.7 Suggested next actions (concrete checklist)

1. **Start the local stack** (docker-compose up; `alembic upgrade head`; `scripts/run_dev`; `cd frontend && npm run dev`).
2. **Capture every screenshot in Section 10.1** with a real (synthetic) claim flowing through.
3. **Draw the seven diagrams in Section 10.2–10.5** using draw.io or Excalidraw. Export as PNG at 2×.
4. **Re-read PROJECT_KNOWLEDGE_DUMP.md** and write a 2-page synopsis around the most personally-owned CRs.
5. **Compile a 1-page CR table** (Section 9 of this document, edited to highlight personal involvement).
6. **Run a clean local `pytest tests/unit` + `pytest tests/integration`** and screenshot the green output — useful "Testing" evidence in the report.
7. **Verify the latest `feature_schema.json` for each variant** and quote the metrics in the Results section.
8. **Prepare 5 backup slides** answering the most likely viva questions (Section 7 of this document is the bank to draw from).

---

*End of Internship Report Preparation Dataset. Source of truth: `docs/PROJECT_KNOWLEDGE_DUMP.md` + the live repository state on 2026-06-24. Read-only; no code, artifacts, migrations, or models were modified to produce this document.*
