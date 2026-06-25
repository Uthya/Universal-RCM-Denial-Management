# Universal RCM Denial Management — Project Knowledge Dump

A complete technical reference compiled for the Summer Industrial Internship Report, Academic Evaluation, Project Presentation, and Viva Voce. Sourced directly from the repository state on 2026-06-24.

---

## Table of contents

1. Project overview
2. Domain knowledge
3. System architecture
4. Technology stack
5. Database design
6. Data pipeline
7. Machine learning pipeline
8. Feature engineering
9. Major CRs / enhancements (CR-079 onward)
10. Recommendation engine
11. User workflow
12. Results
13. Challenges faced
14. Future enhancements
15. Internship contributions

---

## SECTION 1 — Project overview

**Project title.** Universal RCM Denial Management — v2: A multi-variant, leakage-safe denial-prediction and recommendation platform for U.S. healthcare claim transactions (837P, 837I, 837D) and their remittance counterparts (835).

**Problem statement.** Healthcare providers in the United States submit billions of insurance claims every year through X12 EDI transactions. A material fraction (~5–25 % depending on specialty) are denied by payers, sometimes for structural defects (missing fields, invalid modifiers), sometimes for policy reasons (no prior authorization, late filing), and sometimes for clinical reasons (medical necessity, coding-pair mismatches). Denials trigger costly back-office rework, delay cash flow, and erode patient trust. There is no single tool that:

- Parses every common professional / institutional / dental 837 variant uniformly;
- Predicts denial probability **before** the claim is sent to the payer;
- Explains the prediction in business-language reason buckets, not raw feature names;
- Recommends a concrete remediation drawn from CARC code semantics, parser validators, and ML-derived risk factors; and
- Closes the loop with the actual 835 outcome so the system learns from every adjudication.

**Business problem solved.**
- **Pre-submission risk surfacing.** Operators see HIGH / MEDIUM / LOW risk per claim before transmission, with the top denial-reason buckets ranked by *actionability* (not raw SHAP magnitude).
- **Unified parsing.** A single parser covers 837P (professional), 837I (institutional / home care), 837D (dental), and 835 (remittance), so a multi-line practice does not need three separate tools.
- **Outcome-driven learning.** Every prediction is logged with its model version, FE version, calibrator version, and threshold; when the 835 arrives, the actual outcome is back-filled, producing an honest accuracy signal across time.
- **Explanation that survives a viva.** Predictions are rendered through canonical denial buckets (`authorization`, `documentation`, `procedure`, `diagnosis`, `timely_filing`, `coverage`, `billing`, `provider`, `history`, `similar`, `general`) so a clinician, biller, or compliance auditor can act on them without reading the underlying feature engineering code.

**Why this project was created.**
1. **Lessons from v1.** The system is a deliberate v2 rebuild that bakes documented v1 failure modes into the schema and code: nullable critical dates (no `date.today()` placeholders), four-tier validator chain, M1 strict column-order check at predict time, and a 4-encoding fallback chain with ISA-presence sanity. These guards are non-negotiable.
2. **Multi-variant support from day 1.** v1 was 837P-only; v2 supports six real `(variant, subtype)` keys plus a global fallback, so claim types beyond outpatient professional (home care, ambulance, DME, dental, oncology, behavioral) are not bolted on.
3. **Storage minimisation & AIR discipline.** Every non-trivial change goes through an 8-section Architecture Impact Review (introduced after CR-050/CR-052, when one inadvertent UPDATE on a 50 M-row table inflated `audit_log` to 7 M rows / 8 GB in days). Persistent state is added only when there is a verified consumer.

**Target users.**
- **RCM denial analysts** — review HIGH-risk claims pre-submission, accept/decline recommended fixes.
- **Billing operators / coders** — drill into a single claim, see the top risk factors and the remit-derived CARC code if it has already been adjudicated.
- **Provider IT / RCM admins** — upload bulk 837 and 835 batches, monitor model drift, retrain on demand.
- **Future**: Compliance teams (audit trail), clinical leadership (denial-pattern dashboards), and machine-learning engineers (drift baselines, feature snapshots, model artifact bundles).

**Expected benefits.**
- Reduction in *avoidable* denials (those with parser-detectable defects or with strong ML-flagged risk factors) before transmission.
- Faster operator triage through actionability-tiered reasons (Tier-A "directly fixable" surfaces first, not just the largest-SHAP feature).
- Auditable model history — every retrain logs its corpus size, FE version, calibrator version, threshold, prevalence, and held-out metrics.
- Foundation for a recommendation+RAG layer that can eventually draft appeal letters grounded in payer policy documents.

---

## SECTION 2 — Domain knowledge

### Revenue Cycle Management (RCM)

RCM is the end-to-end financial process that begins when a patient schedules a visit and ends when the provider's account is settled with every party who owes money for that encounter (payer + patient). The cycle has roughly seven stages: pre-registration, eligibility verification, encounter / charge capture, **coding**, **claim submission**, **remittance processing**, and patient collections. Denial management lives between claim submission and remittance: it is the discipline of preventing denials, working denied claims, and resubmitting / appealing them.

### Claim lifecycle

```
        ┌───────────────┐        ┌──────────────┐        ┌──────────────┐
837 ───▶ │ Original (1) │ ────▶ │ Submitted    │ ────▶ │ Adjudicated  │
        └───────┬───────┘        │ to payer     │        │ (paid /      │
                │                └──────────────┘        │  denied)     │
                ▼                                         └──────┬───────┘
        ┌──────────────┐                                         │
        │ Replacement  │ ◀───── (operator corrects + resubmits)  │
        │ (freq=7)     │ ───────────────────────────────────────▶│
        └──────────────┘                                          │
                                                                  ▼
                                                          ┌──────────────┐
                                                          │ 835 remit    │
                                                          │ (CLP+CAS+LQ) │
                                                          └──────────────┘
```

The numeric frequency code on `CLM05-3` records the lifecycle position:
| Freq | Meaning |
| ---- | ------- |
| 1 | Original (first submission) |
| 6 | Corrected (rarely used by all payers; treated as replacement-like) |
| 7 | Replacement / resubmission of a prior claim |
| 8 | Void / cancel a prior claim |

### EDI 837 (Health Care Claim)

ASC X12 transaction set 837 is the **claim submission** message. It carries everything the payer needs to adjudicate: patient demographics, subscriber + payer identifiers, provider NPIs, dates of service, ICD-10 diagnosis codes, CPT/HCPCS/CDT procedure codes, modifiers, charges, units, and place of service. Three implementation guides matter:

- **837P** — Professional. Implementation guide `005010X222A1`. Used by physicians, therapists, ambulance, etc.
- **837I** — Institutional. IG `005010X223A2`. Used by inpatient hospitals, home-health agencies, hospices.
- **837D** — Dental. IG `005010X224A2`. Used by dental practices.

Every 837 is wrapped in an ISA/IEA interchange envelope and a GS/GE functional-group envelope. GS08 carries the IG identifier, which is how the system detects the variant at parse time.

### EDI 835 (Remittance Advice)

ASC X12 transaction set 835 is the **payment / adjudication response** from the payer. Implementation guide `005010X221A1`. Key segments:

- **CLP** — Claim Payment Information (one per remitted claim). `CLP02` is the claim-status code: `1`=Processed-as-Primary, `2`=Processed-as-Secondary, `3`=Processed-as-Tertiary, `4`=Denied, `19`=Processed-Adjustment-Forwarded, `20`=Processed-as-Primary-Forwarded-to-Additional-Payer, `22`=Reversal.
- **CAS** — Claim Adjustment Segment. Triplets of (group code, **CARC**, amount). Group codes: `CO`=Contractual Obligation, `PR`=Patient Responsibility, `OA`=Other Adjustments, `PI`=Payer-Initiated, `CR`=Correction-Reversal.
- **SVC** — Service Payment Information (line-level breakdown).
- **LQ** — License/Reference (used for **RARC** remark codes).
- **MIA / MOA** — Inpatient / Outpatient adjudication (DRG, professional component, etc.).
- **DTM** — Date / time qualifiers (e.g., `DTM*405` = production date, `DTM*050` = received date).

### CARC and RARC codes

**CARC** = Claim Adjustment Reason Code. Standardised by the Washington Publishing Company (WPC). Examples: `1`=Deductible, `16`=Lacks information, `29`=Past timely filing limit, `45`=Charge exceeds fee schedule, `50`=Non-covered, `97`=Bundled service.

**RARC** = Remittance Advice Remark Code. WPC-standardised supplemental codes. Examples: `M76`=Missing/invalid diagnosis, `M78`=Modifier invalid, `N130`=Consult payer for further info.

CARC is **why the payer adjusted**; RARC is **how to interpret** that adjustment. v2 ingests both, joins to the `code_masters` reference table (1,506 rows: 308 CARC + 1,198 RARC), and maps each to one of 11 canonical denial buckets via `src/rcm/ml/denial_buckets.py`.

### Denials, rejections, and replacement claims

- **Denial** — Payer received the claim, adjudicated it, and refused to pay (`CLP02='4'`). A 835 row exists. Money owed becomes write-off, patient responsibility, or appeal target.
- **Rejection** — The clearinghouse or payer's front-end EDI engine refused the claim *before* adjudication. No 835 is generated; the claim must be re-keyed / corrected and re-submitted. v2 captures rejections as ERROR-severity validator entries that drop the claim with `dropped=True`.
- **Replacement claim** — A new 837 with frequency code `7`, referencing the original payer claim control number (`REF*F8`). It supersedes the original. The CR-117/CR-118 **lifecycle features** compute the diff between original and replacement (auth added? modifier added? dx changed?).

### Frequency codes (CLM05-3)

`1` Original, `6` Corrected, `7` Replacement, `8` Void. The system filters `frequency_code IS NULL OR '1'` in `mv_claim_labels` (training labels), but admits freq-7 replacements whose remit denies (CR-072 denial propagation: `+9,539` rows propagated as `denied=1` in migration 0017).

### Healthcare billing workflow (where this system fits)

```
Patient encounter
       ▼
Charge capture in EMR / practice-management system
       ▼
Coding (ICD-10, CPT/HCPCS/CDT, modifiers, POS)
       ▼
Pre-submission scrubbing  ◀── ★ THIS SYSTEM (v2 RCM Denial Management)
       │     - parse 837
       │     - validate (4 tiers)
       │     - predict denial risk
       │     - render reasons + fixes
       ▼
Clearinghouse / payer front-end
       ▼
Payer adjudication
       ▼
835 remittance  ◀── ★ THIS SYSTEM (ingests + closes the loop)
       ▼
Posting, patient statements, denials work-queue
       ▼
Replacement (freq=7) or appeal  ◀── ★ THIS SYSTEM (lifecycle features)
```

---

## SECTION 3 — System architecture

### High-level architecture

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Frontend (React 18 + Vite + Tailwind, 127.0.0.1:5173)                     │
│  Pages: UploadPage, ClaimsPage, ClaimDetailPage                             │
│  Widgets: UploadCard×2, HighRiskList, RecommendedFixesPanel,               │
│           TrainModelCard, TrainingHistoryCard, DenialRiskCard               │
└──────────────────────────────┬─────────────────────────────────────────────┘
                               │ JSON over HTTP (TanStack React Query)
                               ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  FastAPI backend (uvicorn, port 8000)                                      │
│  Routers:                                                                  │
│    /api/edi/*             (upload, list files, pending pairs)              │
│    /api/claims/*          (list, detail)                                   │
│    /api/predictions/*     (train, predict-file, predict-claim, dataset…)   │
│    /api/ml/*              (training history)                               │
│    /api/recommendations/* (by-file composite recommendations)              │
│    /api/dev/*             (developer console: env, db, telemetry, claims…) │
└──────────────────────────────┬─────────────────────────────────────────────┘
                               │
   ┌───────────────────────────┼───────────────────────────────────────┐
   ▼                           ▼                                       ▼
┌─────────────┐  ┌──────────────────────────────┐  ┌────────────────────────────────┐
│  Parsing    │  │  Feature engineering         │  │  ML / Predictor                │
│  (EDI →     │  │  FeatureBuilder              │  │  XGBoost per-variant bundles   │
│   ORM)      │  │   - 14 categories            │  │   - 837P healthcare            │
│             │  │   - 7 variant blocks         │  │   - 837I home_care (lifecycle) │
│             │  │   - leakage-safe target enc. │  │   - 837D dental                │
│             │  │   - reference data lookup    │  │   + simple_pipeline fallback   │
└─────┬───────┘  └─────────────┬────────────────┘  └────────────────┬───────────────┘
      │                        │                                    │
      ▼                        ▼                                    ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  PostgreSQL 16 + pgvector 0.8.2  (Docker on localhost:5433, rcm_denials_dev)│
│  41 base tables + 4 partitioned + 14 materialised views + 4 PL/pgSQL fns    │
│  Soft-delete on edi_files / claims / patients / users.                      │
│  18 alembic migrations (0001 → 0018).                                       │
└────────────────────────────────────────────────────────────────────────────┘
```

### Data flow

1. **Upload** — Operator drops a 837 or 835 file onto a `UploadCard`. Frontend POSTs `multipart/form-data` to `POST /api/edi/upload`.
2. **Parse** — `parse_and_save(session, raw_bytes, file_name)` runs in one async transaction. Returns `EdiFile` row with `parse_summary` JSON.
3. **Validate** — 4-tier validator chain marks claims with ERROR-severity issues as `dropped=True`; persistence skips them (but retains `raw_segments` for audit).
4. **Persist** — Bulk-Core inserts using `INSERT ... RETURNING` to retrieve generated ids; insert-then-catch dedup on `content_hash`.
5. **MV refresh (debounced)** — `schedule_mv_refresh()` marks `mv_claim_labels` dirty; a background worker fires `REFRESH CONCURRENTLY` after 5 s of quiescence (CR-090). Bulk burst → one refresh.
6. **Predict** — `POST /api/predictions/predict-file/{id}` loads all parsed claims for the file, batches them per `(service_variant, claim_subtype)`, and routes to the correct FeatureBuilder predictor bundle. Returns risk summary + HIGH-risk claims with reason buckets.
7. **Recommend** — `POST /api/recommendations/by-file/{id}` joins (a) CARC adjustments from 835, (b) parser validator errors, (c) ML risk factors (when present) into a unified per-claim list. Returns `status_badge` ∈ {Resolved, Denied, High Risk}.
8. **Log** — Every prediction is written to the partitioned `prediction_log` table with `model_version`, `feature_engineering_version`, `calibrator_version`, `decision_threshold`, `top_risk_factors`, `unseen_indicators`. CR-065 shadow logging runs a second pipeline in parallel for parity audits.

### Module interaction (Python)

```
src/rcm/main.py
  │
  ├── routers/public/edi.py             → parsing.parser.parse_and_save
  ├── routers/public/claims.py          → models.claims (read-only)
  ├── routers/public/predictions.py     → ml.trainer  / ml.predictor / ml.shadow
  ├── routers/public/ml.py              → models.ml_pipeline (read-only)
  ├── routers/public/recommendations.py → models.remittance + models.ingestion
  └── routers/dev/*                     → developer console surface

parsing/
  envelope.py + safe.py        — encoding, ISA, safe extractors
  context.py                   — ParseContext + 15 record dataclasses
  dispatchers/base.py          — HANDLER_REGISTRY
  handlers/*.py                — ~30 segment handlers (NM1, CLM, SV1/2/3, CAS, CLP, …)
  validators/{tier1..tier4}.py — structural / IG / payer / business
  routing.py                   — variant + subtype detection
  persistence.py               — bulk Core inserts + INSERT-RETURNING
  parser.py                    — parse_edi / parse_and_save / reparse

features/
  registry.py                  — FeatureSpec list, per-variant column tuples
  builder.py                   — FeatureBuilder.fit_transform / transform
  encoders.py                  — LeakageSafeTargetEncoder, RarityState
  dataset.py                   — load_training_corpus + denial propagation
  categories/*                 — A coverage, B authorization, … Z availability
  variants/*                   — healthcare, therapy, transport, home_care, dental,
                                  specialty, institutional_other, global_fallback

ml/
  trainer.py                   — train_variant (load → split → fit → calibrate → save)
  predictor.py                 — HealthcarePredictor / DentalPredictor / …
  artifacts.py                 — ModelArtifactBundle (load/save bundle)
  reason_renderer.py           — SHAP → bucket → business sentence
  denial_buckets.py            — CARC/RARC → bucket
  shadow.py                    — paired prediction logging
  simple_pipeline.py           — fixed-vocab fallback model
```

### API interaction

Every public route is JSON-in / JSON-out. CORS is open to `http://localhost:5173`. There is no authentication in the current phase (the system is local-only and the `users` table is scaffolded for a later auth layer). All responses include `detail` on error, and the dev surface (`/api/dev/*`) requires `?confirm=true` for any write endpoint.

---

## SECTION 4 — Technology stack

### Backend (Python 3.12)

| Component | Library | Pinned range |
| --- | --- | --- |
| Web framework | FastAPI | `>=0.115.0,<0.117.0` |
| ASGI server | uvicorn[standard] | `>=0.32.0,<0.36.0` |
| Multipart | python-multipart | `>=0.0.12` |
| Validation | pydantic | `>=2.9.0,<3.0.0` |
| Settings | pydantic-settings | `>=2.6.0,<3.0.0` |
| ORM | SQLAlchemy[asyncio] | `>=2.0.36,<2.1.0` |
| PG async driver | asyncpg | `>=0.30.0,<0.31.0` |
| Migrations | alembic | `>=1.14.0,<1.16.0` |
| Vector | pgvector (Py) | `>=0.3.6,<0.5.0` |
| Background jobs | arq + redis | arq `>=0.26.0`, redis `>=5.2.0` |
| ML core | xgboost | `>=2.1.0,<2.3.0` |
| ML utility | scikit-learn | `>=1.5.0,<1.7.0` |
| Numerics | pandas / numpy | pandas `>=2.2.0`, numpy `>=1.26.0` |
| Hyperparam search | optuna | `>=4.0.0,<5.0.0` |
| Persistence | joblib | `>=1.4.0,<2.0.0` |
| Explainability | shap | `>=0.46.0,<0.48.0` |
| Logging | structlog | `>=24.4.0,<26.0.0` |
| Auth (scaffold) | passlib[bcrypt], python-jose[cryptography] | scaffolded, not wired |
| Dates | python-dateutil | `>=2.9.0` |
| Retries | tenacity | `>=9.0.0` |
| Testing | pytest, pytest-asyncio, pytest-cov, aiosqlite, httpx | pinned |
| Lint / type | ruff, mypy | ruff `>=0.7.0`, mypy `>=1.13.0` |

### Frontend (Node + browser)

| Component | Library | Version |
| --- | --- | --- |
| UI library | React | `^18.3.1` |
| Build / dev | Vite | `^5.4.7` |
| Styling | TailwindCSS | `^3.4.12` |
| Routing | react-router-dom | `^6.26.2` |
| Async cache | @tanstack/react-query | `^5.59.0` |
| Tables | @tanstack/react-table | `^8.20.5` |
| Charts | recharts | `^2.12.7` |
| Components | @headlessui/react | `^2.2.0` |
| HTTP | axios | `^1.7.7` |
| Dates | date-fns | `^4.1.0` |
| Code viewer | @monaco-editor/react | `^4.6.0` |
| JSON viewer | @uiw/react-json-view | `^2.0.0-alpha.30` |
| PostCSS / Autoprefixer | postcss / autoprefixer | `^8.4.47` / `^10.4.20` |

### Database

| Component | Version |
| --- | --- |
| PostgreSQL | 16 (Docker, authoritative) / 18.3 (remote dev cluster) |
| pgvector | 0.8.2 (remote) / pgvector/pgvector:pg16 (local) |
| pg_partman / pg_cron | not enabled (require superuser; partition management is manual in alembic) |
| Redis | 7-alpine (for arq background jobs) |

### Infrastructure / dev

- **uv** for venv + dependency installation (`uv venv && uv pip install -e ".[dev]"`).
- **docker-compose.yml** runs PG + Redis with healthchecks.
- **alembic** for schema migrations (`alembic upgrade head`).
- **scripts/run_dev.sh / .ps1** launch uvicorn with `--reload` enforced (CR-081 runtime-consistency safeguard).

### Testing

- `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`).
- Unit tests in `tests/unit/` (no DB; SQLite in-memory).
- Integration tests in `tests/integration/` (gated on `RCM_INTEGRATION_DSN` env var).
- Performance benchmarks in `scripts/bench_*.py` (standalone, run against local docker PG).

### Deployment posture

The current phase is a local developer + small-team deployment: docker PG on `:5433`, uvicorn on `:8000`, Vite dev server on `:5173`. There is no production cloud deployment yet; the architecture and migrations are explicitly written to support a remote pgvector-enabled cluster (PG 18.3 already validated in CR-009).

---

## SECTION 5 — Database design

### Major tables (41 base + 25 partition children + 14 MVs)

Tables are grouped by the nine domains defined in `src/rcm/models/`.

#### A. Ingestion (3 tables)
| Table | Purpose |
| --- | --- |
| `edi_files` | Per-uploaded-file metadata. Soft-delete. Stores `raw_text` + `content_hash` (unique). `parse_status` ∈ pending / parsing / parsed / failed / partial. `parse_summary` JSONB. |
| `raw_segments` | **Partitioned monthly** by `created_at`. Every parsed segment with `handler_status` ∈ handled / skipped_unhandled / parse_error / validator_dropped. Composite PK `(id, created_at)`. |
| `parse_events` | **Partitioned monthly** by `created_at`. Telemetry: `segment_handled`, `segment_skipped`, `validator_warning`, `validator_error`, `claim_dropped`, `parse_error`. Composite PK `(id, created_at)`. JSONB `details` with GIN index. |

#### B. Claims core (6 tables)
| Table | Purpose |
| --- | --- |
| `patients` | Patient master: `member_id` (unique), demographics. Soft-delete. |
| `providers` | Provider roster: `npi` (unique), taxonomy, name. |
| `subscribers` | Insurance bridge: `member_id`, `patient_id`, `payer_id`, relationship code. |
| `claims` | Core claim: `service_variant` (837P/I/D), `claim_subtype`, `claim_number`, payer / patient / provider FKs, `total_charge_amount`. **NULLABLE** `service_from_date` (Lesson C3). `variant_data` JSONB with GIN index. |
| `claim_lines` | Service lines: `line_number` (unique per claim), `procedure_code`, **4 modifier slots** (v1 only had 1), `billed_amount`, `units`, `place_of_service`, `service_date`, `diagnosis_pointers` ARRAY. Variant fields: `revenue_code` (837I), `hipps_code`, `tooth_number` / `tooth_surfaces` (837D), `ndc_drug_code`. |
| `diagnoses` | One row per dx per claim: `sequence_number`, `diagnosis_code`, `diagnosis_type`, `present_on_admission`. |

#### C. Variant extensions (5 tables)
| Table | Purpose |
| --- | --- |
| `claim_certifications` | Specialty certs (ambulance, homebound, DME, orthodontic, mammography, EPSDT, hospice, plan_of_care). |
| `claim_amounts` | CAS-derived amount qualifiers. |
| `claim_attachments` | PWK attachment metadata. |
| `home_care_episodes` | 837I/HC: `episode_start_date`, `episode_end_date`, `hipps_code`, OASIS date, `visit_count`, `discipline_mix` JSONB, `is_lupa`, `homebound_certified`. |
| `transport_certifications` | Ambulance/DME: `transport_miles`, `patient_weight_lbs`, origin/destination JSONB, `level_of_service`. |

#### D. Remittance (3 tables)
| Table | Purpose |
| --- | --- |
| `remittance_claims` | 835 CLP-level remit: `claim_status_code` (`CLP02`), `billed_amount`, `paid_amount`, `patient_responsibility_amount`. **NULLABLE** `remittance_date` (Lesson P1). |
| `adjustments` | CAS triplets: `adjustment_group_code` (CO/PR/OA/PI/CR), `adjustment_reason_code` (CARC), `adjustment_amount`. |
| `remark_codes` | RARC codes per remit claim. |

#### E. Lifecycle (2 tables)
| Table | Purpose |
| --- | --- |
| `claim_lifecycles` | Original → parent → child chain: `relationship_type` ∈ replacement / resubmission / void / correction / appeal, `iteration_number`, `days_to_resolution`. |
| `appeals` | Appeal: `appeal_level`, `status`, `outcome`, `generated_letter`, `human_edits`, `final_letter`. |

#### F. Reference / master data (9 tables)
| Table | Purpose |
| --- | --- |
| `code_masters` | WPC CARC/RARC/POS/claim-status lookups; enriched with `denial_reason_plain`, `recommended_action`, `category`, `severity`, `is_billable_denial`, `is_patient_responsibility` (extended in mig 0011). |
| `payers` | Payer master: `canonical_name` (unique), `aliases` ARRAY with GIN index. |
| `procedure_codes` | CPT / HCPCS / CDT / HIPPS / NDC catalogue with JSONB `metadata` (annual_limit, requires_pwk, valid_pos_codes, age_min/max, gender_restriction). |
| `diagnosis_codes` | ICD-10-CM / PCS / ICD-9-CM (74,719 ICD-10-CM rows after CR-087 load). |
| `payer_policies` | Per-payer rules: prior_auth, referral_required, timely_filing, frequency_limit, modifier_required, age_limit. JSONB `structured_rule` with GIN index. |
| `cms_knowledge` | LCD / NCD / manual_chapter / NCCI / MUE / claims_processing documents. |
| `ncci_edits` | CMS PTP + MUE pairs (column1+column2+edit_type unique). |
| `cms_lcd_coverage` | CPT × DX × state coverage rules. |

#### G. ML pipeline (4 tables)
| Table | Purpose |
| --- | --- |
| `prediction_log` | **Partitioned monthly** by `prediction_time`. Every prediction with `model_version`, `feature_engineering_version`, `calibrator_version`, `decision_threshold`, `fell_back_to_global`, `top_risk_factors` JSONB, `unseen_indicators` JSONB, `pipeline_name`, `prediction_type`, `prediction_group_id`. |
| `model_training_metrics` | One row per `(variant, subtype, training_run)`: corpus size, hyperparameters, OOF / validation / held-out metrics, artifact_paths. |
| `feature_snapshots` | Per-training-row feature vector (JSONB) + `denied` label + `fold_assignment`. Foundation for drift analysis. |
| `model_artifacts` | Trained-file inventory: `artifact_type` ∈ model/calibrator/encoder, `file_path`, `content_hash`. |

#### H. RAG (5 tables, pgvector-backed)
| Table | Purpose |
| --- | --- |
| `knowledge_documents` | Source-typed knowledge (LCD/NCD/payer policy/correction example). `content_hash` unique. |
| `knowledge_chunks` | Chunked content with `embedding Vector(1024)`. **HNSW index** with `m=16, ef_construction=64`. |
| `claim_embeddings` | One row per claim with embedding (for similarity search). |
| `correction_examples` | Before/after claim pairs for RAG few-shot. JSONB `original_features_snapshot`, `corrected_features_snapshot`, `structural_diff`, `narrative`. |
| `rag_generations` | RAG output log: prompt, retrieved_chunk_ids ARRAY, model_name, generated_text, user_feedback, tokens, latency_ms, cost_cents. |

#### I. Operations (5 tables)
| Table | Purpose |
| --- | --- |
| `tenants` | Multi-tenancy stub. |
| `users` | Auth: `email` (unique), `hashed_password`, `role` ∈ admin/analyst/viewer. |
| `audit_log` | **Originally partitioned**; entire infrastructure dropped in migration 0014 (CR-051) after `audit_log` inflated to 7 M rows / 8 GB in days from a single UPDATE. |
| `request_log` | **Partitioned monthly** by `request_at`. API telemetry. |
| `background_jobs` | Job queue with `status` ∈ queued/running/completed/failed. |

#### J. Pending pair registry (1 table)
| Table | Purpose |
| --- | --- |
| `pending_claim_pairs` | Silent tracking of original→replacement pairings to avoid false "no replacement yet" warnings. |

### Migration timeline

| File | Adds |
| --- | --- |
| `0001_init_core` | Domains A + B + D + E + F core. 25 tables, 13 ENUMs. Partitioned `raw_segments` + `parse_events` (monthly + default + 3 future). |
| `0002_add_variant_extensions` | Domain C: 5 tables + `certification_type` ENUM. |
| `0003_add_ml_pipeline` | Domain G: partitioned `prediction_log`, 3 model tables. |
| `0004_add_operations` | Domain I: partitioned `audit_log` + `request_log`, tenants/users/jobs. Deferred FK wiring for `edi_files.uploaded_by_user_id`, `appeals.created_by_user_id`. |
| `0005_add_ncci_lcd` | `ncci_edits` + `cms_lcd_coverage`. |
| `0006_enable_extensions` | `CREATE EXTENSION vector`; pg_partman / pg_cron optional. |
| `0007_add_rag_scaffolding` | Domain H: 5 tables, 3 HNSW vector indexes. |
| `0008_add_audit_triggers` | `audit_trigger_fn()` + `touch_updated_at_fn()` on every PHI table. (Later dropped in 0014.) |
| `0009_add_domain_functions` | 4 PL/pgSQL helpers: `derive_service_variant`, `is_denied`, `claim_lifecycle_resolved`, `find_similar_claims`. |
| `0010_add_materialized_views` | 12 MVs including the keystone `mv_claim_labels`. |
| `0011_extend_code_masters` | Enriched CARC/RARC fields (denial_reason_plain, recommended_action, severity, category, etc.). |
| `0012_add_pending_pair_registry` | `pending_claim_pairs`. |
| `0013_add_shadow_logging` | `prediction_log.pipeline_name`, `prediction_type`, `prediction_group_id`. |
| `0014_drop_audit_log` | Drops audit infrastructure (triggers + table + partitions) after CR-051. |
| `0015_mv_claim_labels_deleted` | Filters `mv_claim_labels` to `deleted_at IS NULL` + recreates 9 dependents (CR-056). |
| `0016_mv_pch_deleted` | Same filter on `mv_patient_claim_history` (CR-057). |
| `0017_mv_claim_labels_propagated` | Propagates denial labels from freq=7 replacements onto freq=1 originals (+9,539 rows, CR-072). |
| `0018_add_recency_denial_mvs` | `mv_payer_denial_rates_recent_2k` and `mv_payer_denial_rates_90d` (CR-126B). Raw volume + denied_count; Bayesian smoothing in Python. |

### Materialised views (14)

| Name | What it captures |
| --- | --- |
| `mv_claim_labels` | **Keystone**. Pre-computed per-claim denial label from CLP02. Filters soft-deleted + missing service_from_date + non-replacement. 10 dependents. |
| `mv_payer_denial_rates` | Per (payer, variant, subtype) volume + denial_rate. |
| `mv_payer_cpt_denial_rate` | Per (payer, variant, CPT). |
| `mv_payer_dx_denial_rate` | Per (payer, variant, primary_dx). |
| `mv_payer_pos_denial_rate` | Per (payer, variant, place_of_service). |
| `mv_payer_denial_rates_recent_2k` | Last 2,000 claims per payer (raw volume + denied_count). |
| `mv_payer_denial_rates_90d` | Last 90 days per payer (raw volume + denied_count). |
| `mv_provider_denial_profiles` | Per billing_provider_id: claims, denial_rate, payer_diversity. |
| `mv_provider_payer_denial_rate` | Per (billing_provider, payer). |
| `mv_provider_cpt_denial_rate` | Per (billing_provider, primary_CPT). |
| `mv_cpt_dx_denial_rate` | Per (CPT, dx) clinical alignment. |
| `mv_lifecycle_outcomes` | Per `original_claim_id`: `chain_length`, `final_iteration`, `total_days_to_resolution`, `eventually_paid`. |
| `mv_patient_claim_history` | Per claim windowed over patient: claim history snapshot + `patient_seq`. |
| `mv_drift_baselines` | Per (variant, subtype) volume + prevalence + `snapshot_at`. |

All MVs have unique indexes supporting `REFRESH MATERIALIZED VIEW CONCURRENTLY`.

### PL/pgSQL functions (4)

| Function | Purpose |
| --- | --- |
| `derive_service_variant(p_edi_file_id) → TEXT` | Reads GS08 from raw_segments. |
| `is_denied(p_claim_id) → BOOLEAN` | True if any remit row has `CLP02='4'`. |
| `claim_lifecycle_resolved(p_claim_id) → BOOLEAN` | Recursive CTE walk through `claim_lifecycles`. |
| `find_similar_claims(p_embedding, p_limit, p_variant) → SETOF BIGINT` | HNSW nearest-neighbour over `claim_embeddings`. |

### Important indexes

- **Partial indexes** filter rare values: `edi_files.deleted_at IS NULL`, `claims.service_from_date IS NOT NULL`, `parse_events.event_type IN ('segment_skipped', 'parse_error')`, `request_log.status_code >= 400`, `background_jobs.status IN ('queued','running')`.
- **GIN indexes** on JSONB / ARRAY: `payers.aliases`, `payer_policies.applies_to_codes`, `cms_knowledge.applies_to_codes`, `cms_lcd_coverage.cpt_codes`, `claims.variant_data`, `claim_lines.line_data`, `parse_events.details`, all RAG metadata columns.
- **Multi-column** for query plans: `(payer_id, service_variant, service_from_date)`, `(billing_provider_id, service_from_date)`, `(claim_id, line_number)`, `(procedure_code, place_of_service)`, `(payer_id, policy_type)`.
- **HNSW** on every `Vector` column with `vector_cosine_ops`, `m=16`, `ef_construction=64`.

### Data lifecycle

- **Soft-delete** on `edi_files`, `claims`, `patients`, `users` via `deleted_at`. Every critical MV filter explicitly excludes soft-deleted rows (CR-056, CR-057).
- **Reparse** on an `EdiFile` row soft-deletes the original and inserts a fresh row from stored `raw_text`.
- **Audit infrastructure removed** (CR-051) after the 7 M-row inflation incident; will be re-introduced in targeted form when needed.
- **Partition rollover** is manual through alembic (no pg_partman). 4 future-month partitions pre-created; long-term solution awaits a partition-management job.

### Design decisions worth quoting

- `claims.service_from_date` is **NULLABLE** — the validator drops claims with missing service dates rather than writing `date.today()` as a placeholder (Lesson C3).
- `remittance_claims.remittance_date` is **NULLABLE** (Lesson P1).
- Partitioned tables use **composite PKs** `(id, partition_key)` because PG requires the partition column in every unique constraint.
- All FKs use either CASCADE or SET NULL — **zero FKs at NO ACTION default** (verified in CR-008).
- Reference-data tables include `code_metadata` JSONB to keep schema flexible without churning columns.

---

## SECTION 6 — Data pipeline

### How 837 files are processed

```
raw bytes
  │
  ▼
decode_edi(bytes)
  fallback chain: utf-8-sig → utf-8 → cp1252 → latin-1
  ISA-presence sanity check (Lesson P2)
  │
  ▼
detect_delimiters(text)
  rules (29c0fd4): non-alphanumeric, non-whitespace, ISA[3] == ISA[6]
  │
  ▼
tokenize(text, delim.segment)  →  list[str] of segments
  │
  ▼
detect_variant(GS08)
  X222A1 → 837P / edi_837
  X223A2 → 837I / edi_837
  X224A2 → 837D / edi_837
  X221A1 → 835   / edi_835
  │
  ▼
ParseContext()  ←  initialised with file_name + variant
  │
  ▼
For each segment:
  dispatch_segment(seg_name, elements, ctx)
    → HANDLER_REGISTRY[(variant, seg_name)] or fallback
    → handler mutates ParseContext (claims, lines, diagnoses, …)
    → broad-exception catch → emit parse_error event, continue
  │
  ▼
finalize_subtypes(ctx)
  derive_subtype(claim):
    rev 551–589           → home_care
    CPT starts with A0    → transport
    modifier ∈ GP/GO/GN/KH/KX → therapy
    CDT D-codes           → dental sub-types
    NDC + J-codes         → specialty/oncology
    else                  → healthcare / institutional_other
  │
  ▼
validators.run_all(ctx, session)
  tier 1 — structural   (ST/SE, GS/GE, ISA/IEA reconciliation)
  tier 2 — IG           (e.g., DTP*472 required for 837P)
  tier 3 — payer policy (reads payer_policies table)
  tier 4 — business     (date ordering, duplicate claim_number, line-sum vs CLM02)
  → ERROR-severity rows mark claim.dropped = True
  │
  ▼
persistence.save_parse_context(session, ctx, content_hash=…)
  bulk Core INSERT ... RETURNING for ids
  insert-then-catch on EdiFile.content_hash (DuplicateFileError)
  raw_segments + parse_events bulk-inserted in ≤ 5k-row batches
  dropped claims STILL persist their raw_segments with handler_status='validator_dropped'
  →  EdiFile row returned to caller
```

### How 835 files are processed

Same pipeline; variant detected as `edi_835` from GS08 `X221A1`. Key handlers:

- `CLP` — creates a `RemittanceClaimRec` with status code; resolves the matching `claims` row by `claim_number`.
- `CAS` — triplet parser auto-detects stride-2 vs stride-3 forms; attributes to current SVC line (line-level) or CLP (claim-level).
- `SVC` — service-line payment break-down.
- `LQ` — captures RARC remark codes.
- `MIA` / `MOA` — currently emit `segment_handled` events with raw element counts (full inpatient/outpatient adjudication parsing deferred).

Orphan CLPs (no matching `claims` row) are skipped with a `parse_event` log entry; not failed.

### Parsing flow specifics

- **Handler dispatch** uses `HANDLER_REGISTRY[(variant, segment_name)]` with a `'*'` fallback. Handlers register via side-effect imports.
- **Broad exception catch** — any handler exception is converted to a `parse_error` event + ERROR validator entry; the rest of the file continues (v1 crashed on unexpected exception types).
- **`safe_date` semantics**: empty input → `None` silently; non-empty unparseable input → `WARN` + `None`; never substitutes `date.today()` (Lesson C3 + P3).
- **CAS triplet parser** disambiguates stride-2 (compact non-spec form) vs stride-3 (spec form) and emits a warning when the compact form is detected.
- **Encoding chain** is applied with an ISA-presence sanity check in the first 1 KB. `latin-1` alone is never the first attempt because it never raises and would accept garbage.

### Validation flow

| Tier | Owner | Examples |
| --- | --- | --- |
| 1 — Structural | `tier1_structural.py` | ST/SE counter match, GS/GE counter match, ISA/IEA reconciliation, multi-ISA warning. |
| 2 — IG | `tier2_ig.py` | 837P claim with no DTP*472 → drop (Lesson C3); 835 missing DTM*050/*405 → WARNING only (row persists with NULL `remittance_date`, Lesson P1). |
| 3 — Payer policy | `tier3_payer.py` | Reads `payer_policies` table; checks prior_auth, referral_required, timely_filing per (payer × CPT × variant). Skipped when no session is passed (unit-test path). |
| 4 — Business | `tier4_business.py` | Date ordering, duplicate `claim_number` within a file, line-sum vs `CLM02` tolerance. |

`_mark_dropped_from_errors` promotes ERROR entries to `claim.dropped=True` / `remit.dropped=True`. Persistence then skips them.

### Storage flow

- Master data (`payers`, `providers`, `patients`, `subscribers`) is upserted concurrency-safely (CR-069 eliminated a cold-start UniqueViolation race).
- `EdiFile` is added with insert-then-catch on `content_hash`; `IntegrityError` becomes `DuplicateFileError` (HTTP 409 territory). The `partial unique index WHERE deleted_at IS NULL` lets reparses re-add after a soft delete (CR-023).
- Claims, lines, diagnoses, remit, adjustments, remarks → bulk Core inserts with `INSERT ... RETURNING`.
- `raw_segments` + `parse_events` bulk-inserted in ≤ 5,000-row batches to stay under remote PG's `max_wal_size = 400 MB`.
- Performance after the CR-024 rewrite: parse ~3.4 k claims/s, persist ~lifted from 134 to ~3 k claims/s at 10 k scale (CR-025).

### Feature generation flow

```
FeatureBuilder(service_variant, claim_subtype, include_lifecycle=False)
  │
  ├── ensure_ref_data(session)  → RefDataLookup (procedure_codes,
  │                                diagnosis_codes, ncci_edits,
  │                                cms_lcd_coverage, payer_policies)
  │
  ├── load_patient_history(session, claim_ids)   → PatientHistorySnapshot (G)
  ├── load_provider_profile(session, ids)        → ProviderProfileSnapshot (H)
  ├── load_joint_encoders(session, ids)          → JointEncoderSnapshot (I)
  ├── load_lifecycle(session, claim_ids)         → LifecycleSnapshot (CR-117/118)
  │
  ├── fit_transform(df, y, safe_rates=…, safe_recency_rates=…)
  │     1. Enrich df with derived categoricals (cpt_category, dx_chapter via CR-088)
  │     2. Fit RarityState (training vocab + per-value volume)
  │     3. LeakageSafeTargetEncoder.fit_transform (5-fold OOF on TRAIN slice only)
  │     4. _assemble(): produce categories J → A → B → C → D → E → F → G → H → I → K → L → Z → M (variant) → (lifecycle if enabled)
  │     5. Substitute leakage-safe denial rates (CR-107) and recency rates (CR-126B)
  │     6. validate_feature_frame (M1 check — exact column order)
  │
  └── transform(df)
        Same path except:
          - Encoder reused as fitted (full-fit transform, no CV)
          - Global MV denial rates used (no leakage-safe overrides at predict)
          - Lifecycle features NOT injected at predict
```

### Prediction flow

```
POST /api/predictions/predict-file/{edi_file_id}
  │
  ▼
load claims for edi_file_id  (with lines, diagnoses, certifications, …)
  │
  ▼
group by (service_variant, claim_subtype)
  │
  ▼
for each group:
  predictor = _FB_PREDICTOR_CACHE[(variant, subtype)]   # lazy load
  bundle = ModelArtifactBundle.load(artifacts/featurebuilder/{variant}_{subtype}/)
  X = await bundle.builder.transform(session, df)
  validate booster.feature_names == X.columns         # CR-076 #3 hard-fail
  raw_scores = bundle.booster.predict(DMatrix)
  calibrated = bundle.calibrator.transform(raw_scores) if bundle.calibrator else raw_scores
  shap_matrix = bundle.booster.predict(DMatrix, pred_contribs=True)[:, :-1]
  labels  = (calibrated >= bundle.decision_threshold).astype(int)
  levels  = _risk_level(calibrated)            # HIGH / MEDIUM / LOW
  factors = _top_risk_factors(X.columns, shap_matrix, k=15)
  reasons = render_risk_factors(factors, top_k=5, claim_subtype=subtype)
  log to prediction_log (model_version, FE version, calibrator version,
                          decision_threshold, top_risk_factors, unseen_indicators,
                          pipeline_name='featurebuilder', prediction_group_id)
  if shadow logging enabled: spawn parallel simple_pipeline log
  │
  ▼
PredictFileResponse(
  risk_summary={HIGH, MEDIUM, LOW},
  high_risk_claims=[...]
)
```

---

## SECTION 7 — Machine learning pipeline

### Models used

| Variant | Subtype | Pipeline | Algorithm | Calibration |
| --- | --- | --- | --- | --- |
| 837P | healthcare | FeatureBuilder | XGBoost classifier (`tree_method=hist`) | Isotonic on validation slice |
| 837I | home_care  | FeatureBuilder (with `include_lifecycle=True` post-CR-120) | XGBoost | Isotonic (refit in CR-112) |
| 837D | dental     | FeatureBuilder | XGBoost | Isotonic |
| Any unknown / institutional_other | — | `simple_pipeline.py` fallback | XGBoost with fixed one-hot vocabulary | CalibratedClassifierCV (isotonic) |

### Why XGBoost was selected

- **Interpretability via SHAP.** Native `pred_contribs=True` exposes per-feature contributions directly from the booster; this is what feeds `reason_renderer.render_risk_factors`.
- **Strong performance on tabular medical data.** Production held-out ROC-AUC is 0.95–0.998 across the three variants (see Section 12).
- **Class-imbalance support.** `scale_pos_weight = n_neg / max(1, n_pos)` is auto-computed per variant — critical for dental (1.19 % denial rate).
- **Stable feature-name preservation.** `booster.feature_names` is validated against `X.columns` at predict time (CR-076 #3) to prevent silent SHAP misattribution. XGBoost preserves names through serialisation in `model.json`.
- **Production maturity.** Robust joblib / JSON serialisation; well-supported `tree_method="hist"` for batch scoring.

### Training workflow

```
1. Refresh mv_claim_labels  (CR-083 — first statement in /train; abort 503 on failure)
   │
2. load_training_corpus(session, service_variant, claim_subtype)
   - reads from mv_claim_labels joined to claims + lines + diagnoses
   - applies CR-071 "any denial wins" propagation across (claim_number, payer_id) pairs
   - admits freq=7 replacements when include_freq7=True (CR-120A)
   - asserts ≥ 20 total, ≥ 5 positive, ≥ 5 negative
   │
3. Stratified split 70 / 15 / 15
   - TRAIN  (70 %)  →  encoder fit + booster fit
   - VAL    (15 %)  →  calibrator fit + threshold selection
   - HELD-OUT (15 %) →  final reported metrics (NEVER touched during training)
   - StratifiedKFold with random_state = 42
   │
4. compute_leakage_safe_denial_rates(TRAIN)
   - per row, per denial-rate feature, denied_count / volume over earlier-dated TRAIN rows
   - merge_asof(allow_exact_matches=False) → strict < on service_from_date
   - rates injected back into the FB feature set as override (CR-107)
   │
5. FeatureBuilder.fit_transform(session, TRAIN, y_train, safe_rates=…)
   - Categories J, A, B, C, D, E, F, G, H, I, K, L, Z, M(variant), [lifecycle]
   - 5-fold OOF target encoding for K
   - M1 column-order check
   → X_train.astype('float32')  (99 / 71 / 74 features)
   │
6. FeatureBuilder.transform(session, VAL),  FeatureBuilder.transform(session, HELD-OUT)
   - Use fitted encoder + rarity state
   - Global MV rates (no leakage-safe override at non-train surfaces)
   │
7. XGBClassifier.fit(X_train, y_train)
   - n_estimators=200, max_depth=5, learning_rate=0.05
   - scale_pos_weight = neg/pos auto
   - eval_metric=logloss, tree_method=hist
   - random_state=42
   │
8. Calibrate isotonic on VAL raw scores
   - IsotonicRegression(out_of_bounds='clip', y_min=0.001, y_max=0.999)
   - 20-point monotonicity test → fallback to raw scores if non-monotonic
   │
9. Threshold via precision-floor (PRECISION_FLOOR = 0.85)
   - search t ∈ {0.01, 0.02, …, 0.98}
   - return max-recall t with precision ≥ 0.85
   - fallback to max-precision t if floor unreachable
   │
10. Save bundle to artifacts/featurebuilder/{variant}_{subtype}/
    model.json, calibrator.joblib, encoder.joblib, rarity_state.joblib,
    feature_schema.json (canonical contract — feature_columns list,
    decision_threshold, training_size, prevalence, OOF/VAL/HELD-OUT metrics).
    model_version = "v1.fb.YYYYMMDDTHHMMSS.{variant}_{subtype}"
    │
11. INSERT INTO model_training_metrics (...)
12. POST /api/predictions/reload-bundles    (clears _FB_PREDICTOR_CACHE)
```

### Hyperparameter defaults

```
n_estimators       = 200
max_depth          = 5
learning_rate      = 0.05
scale_pos_weight   = auto (n_neg / max(1, n_pos))
subsample          = 1.0
colsample_bytree   = 1.0
reg_alpha          = 0.0
reg_lambda         = 1.0
tree_method        = "hist"
eval_metric        = "logloss"
random_state       = 42
```

### Optuna hyperparameter tuning (CR-079)

Search space:
| Hyperparameter | Range |
| --- | --- |
| n_estimators | int[100, 800, step=50] |
| max_depth | int[3, 9] |
| learning_rate | float[0.01, 0.30, log] |
| min_child_weight | int[1, 20, log] |
| gamma | float[0, 5] |
| subsample | float[0.5, 1.0] |
| colsample_bytree | float[0.4, 1.0] |
| colsample_bylevel | float[0.4, 1.0] |
| reg_alpha | float[1e-8, 10, log] |
| reg_lambda | float[1e-8, 10, log] |
| scale_pos_weight | float[0.5 × auto, 2.0 × auto] |
| max_delta_step | int[0, 10] |

Objective: `0.7 × (5-fold CV PR-AUC on TRAIN) + 0.3 × (single-fit PR-AUC on VAL)`. TPE sampler + MedianPruner.

### Isotonic calibration

- **Why:** non-parametric, monotonic, robust to bi-modal label distributions.
- **Fit on VAL** (not OOF) because VAL comes from the same final booster used at predict time.
- **Clip y to [0.001, 0.999]** to prevent extreme predictions.
- **Monotonicity guard** — 20-point test; if non-monotonic, calibrator is set to `None` and bundle records `calibrator_version=None`.

### Threshold derivation — precision-floor

```
PRECISION_FLOOR = 0.85
for t in linspace(0.01, 0.98, 98):
    p, r = precision_recall_at(t)
    if p >= 0.85:
        candidates.append((t, r))
        max_p = max(max_p, (t, p))
return max-recall candidate, else max_p
```

Business rationale: predict HIGH only when the model is ≥ 85 % confident — keeps operator trust high; recall is maximised subject to that floor.

### Production thresholds

| Variant | Threshold |
| --- | --- |
| 837P / healthcare | 0.05 |
| 837I / home_care | 0.50 (after CR-114 correction from 0.75) |
| 837D / dental | 0.34 (fallback from max-precision when 0.85 floor unreachable due to 1.19 % prevalence) |

### Prediction-log row contract

Every row in `prediction_log` carries: `claim_id`, `prediction_id` (UUID), `predicted_risk`, `predicted_label`, `risk_level`, `service_variant`, `claim_subtype`, `model_version`, `feature_engineering_version`, `calibrator_version`, `decision_threshold`, `input_completeness`, `top_risk_factors` JSONB, `unseen_indicators` JSONB, `feature_snapshot` JSONB, `fell_back_to_global`, `pipeline_name`, `prediction_type`, `prediction_group_id`. Composite PK `(id, prediction_time)`; partitioned monthly.

### SHAP reason rendering

```
booster.predict(DMatrix, pred_contribs=True)   →  shap_matrix[:, n_features+1]
strip bias                                     →  shap_matrix[:, n_features]
sort by |shap|, keep top-15 features
filter to positive-impact features only (negative = protective)
map feature → bucket (11 canonical buckets)
apply variant-aware bucket override (CR-092 Issue 1)
deduplicate by bucket slug (keep highest |shap| per bucket)
sort by (tier ASC, |shap| DESC)               ←  CR-093: actionability above magnitude
apply variant-aware sentence override (CR-078A)
return top-5 (slug, label, sentence, impact, direction='increases denial risk')
```

The 11 canonical buckets and their actionability tiers:

| Tier | Bucket | Default sentence |
| --- | --- | --- |
| 0 | authorization | Authorization requirements may not be fully satisfied for this claim. |
| 0 | documentation | Supporting documentation may be insufficient. |
| 0 | procedure | The billed procedure information may increase denial risk. |
| 0 | diagnosis | Diagnosis details may require additional review. |
| 1 | timely_filing | Submission timing should be reviewed against payer limits. |
| 1 | coverage | Coverage eligibility or member information should be reviewed. |
| 1 | billing | Billing details may require verification. |
| 1 | provider | Provider-related claim information should be reviewed. |
| 2 | history | Previous claim patterns indicate higher denial risk. |
| 2 | similar | Claims with similar characteristics show higher denial risk. |
| 2 | general | This claim contains factors commonly associated with denials. |

### Evaluation metrics

Computed three times per training run — on OOF, VAL, and HELD-OUT. Each surface reports: `n`, `prevalence`, `accuracy_at_threshold`, `precision_at_threshold`, `recall_at_threshold`, `f1_at_threshold`, `roc_auc`, `pr_auc`, `brier_uncalibrated`, `brier_calibrated`, `positive_rate`.

---

## SECTION 8 — Feature engineering

### Category map (universal 100 + variant-specific 4–11)

| Code | Category | Representative features | Captures |
| --- | --- | --- | --- |
| **A** | Coverage / Eligibility | `patient_age_at_service`, `patient_age_band`, `cob_position_encoded`, `payer_overall_denial_rate`, `payer_overall_denial_rate_recent_2k_smoothed`, `payer_overall_denial_rate_90d_smoothed` | Age, COB, payer denial rates (lifetime + recency-windowed Bayesian-smoothed). |
| **B** | Authorization / Referral | `has_prior_authorization`, `auth_required_for_cpt_payer`, `auth_missing_when_required`, `auth_number_format_valid`, `has_referral`, `referral_required_for_cpt_payer`, `referral_missing_when_required` | Prior-auth + referral presence and payer-rule matches. |
| **C** | Clinical / Medical Necessity | `cpt_dx_alignment_score`, `has_unspecified_diagnosis`, `primary_dx_chapter_encoded`, `is_high_complexity_em`, `principal_dx_supports_procedure`, `dx_severity_score` | CPT–DX pairing, ICD chapter, complexity, specificity. |
| **D** | Coding Integrity | `has_modifier`, `modifier_count_total`, `is_likely_unbundled`, `cpt_frequency_for_patient_ytd`, `cpt_frequency_exceeds_limit`, `is_replacement_claim` | Modifier rules, NCCI unbundling, annual-limit checks. |
| **E** | Timely Filing | `service_to_submission_days`, `payer_timely_filing_days`, `timely_filing_proximity_ratio`, `is_past_timely_filing`, `is_near_timely_filing` | Days-to-submission vs payer threshold. |
| **F** | Documentation | `has_paperwork_attachment`, `paperwork_required_for_cpt`, `paperwork_missing_when_required`, `has_certification_segment`, `has_notes` | PWK presence + certification + notes. |
| **G** | Patient history | `claims_in_last_30d`, `claims_in_last_90d`, `claims_in_last_365d`, `prior_denials_with_payer`, `prior_denials_with_payer_and_cpt`, `prior_paid_with_payer`, `days_since_last_claim`, `is_new_patient_to_provider`, `annual_charges_for_patient`, `same_day_visits_for_patient` | Temporal windows + per-payer prior outcomes (read from `mv_patient_claim_history`). |
| **H** | Provider profile | `billing_provider_npi_encoded`, `rendering_provider_npi_encoded`, `provider_specialty_taxonomy_encoded`, `provider_overall_denial_rate`, `provider_payer_denial_rate`, `provider_cpt_denial_rate`, `provider_volume_band` | Provider-level rates from `mv_provider_*` MVs. |
| **I** | Joint encoders | `payer_cpt_denial_rate`, `payer_dx_denial_rate`, `payer_pos_denial_rate`, `cpt_dx_denial_rate`, `provider_payer_denial_rate`, `provider_cpt_denial_rate_joint` | Composite denial rates from `mv_payer_cpt_denial_rate`, `mv_cpt_dx_denial_rate`, etc. |
| **J** | Base claim | `total_charge_amount`, `total_billed_amount`, `charge_to_billed_ratio`, `line_count`, `diagnosis_count`, `total_units`, `units_per_line`, `service_month`, `service_day_of_week`, `weekend_service`, `service_duration_days` | CLM-level summary + temporal decomposition. |
| **K** | Encoded categoricals | `payer_name_encoded`, `primary_cpt_encoded`, `primary_dx_encoded`, `place_of_service_encoded`, `facility_type_code_encoded`, `cpt_category_encoded`, `primary_dx_chapter_encoded` | LeakageSafeTargetEncoder outputs (CV-fitted at train, deterministic at predict). The last two were added in CR-088 from procedure / diagnosis reference metadata. |
| **L** | Rarity / unseen / missing | `is_rare_payer`, `is_rare_dx`, `is_rare_cpt`, `unseen_payer`, `unseen_cpt`, `unseen_dx`, `unseen_rendering_provider`, `unseen_any`, `missing_payer`, `missing_diagnosis`, `missing_procedure`, `missing_pos`, `missing_count` | Training-vocabulary membership flags. |
| **Z** | Availability / completeness | `avail_procedure_codes_metadata`, `avail_payer_policies`, `avail_ncci_edits`, `avail_lcd_coverage`, `reference_data_completeness` | Per-ref-table flags + mean completeness. |
| **M** | Variant-specific | (see below) | Per (variant, subtype) extra columns. |

`frequency_code_encoded` was retired in CR-122B (gain=0 across all variants).

### Lifecycle features (CR-117 Stage 1 + CR-118 Stage 2)

Opt-in via `include_lifecycle=True`. Used in production for 837I / home_care only (CR-120).

| # | Feature | Type | Description |
| --- | --- | --- | --- |
| 1 | `had_prior_denial` | bool | The freq-1 original of this freq-7 replacement was denied. |
| 2 | `prior_denial_bucket` | int | Canonical bucket of the original's top CARC (0–10; 0 if none). |
| 3 | `days_since_original_denial` | int | Days from original remit to replacement service date (clip 0–365). |
| 4 | `auth_added_in_replacement` | bool | Original auth NULL/blank, replacement auth present. |
| 5 | `referral_added_in_replacement` | bool | Same logic for referral. |
| 6 | `modifier_added_in_replacement` | bool | `set(repl.modifiers) − set(orig.modifiers)` non-empty. |
| 7 | `diagnosis_changed_in_replacement` | bool | DX set diff. |
| 8 | `procedure_changed_in_replacement` | bool | CPT set diff. |
| 9 | `lines_changed_in_replacement` | int | Signed delta in `claim_lines` count, clipped [-99, +99]. |
| 10 | `charge_changed_in_replacement` | int | Sign of `total_charge_amount` delta ∈ {-1, 0, +1}. |
| 11 | `correction_action_count` | int | Count of binary correction flags (0–7). |

### Recency features (CR-126B)

Two Bayesian-smoothed payer denial rates over recent windows:

| Feature | Window | Source MV | Smoothing |
| --- | --- | --- | --- |
| `payer_overall_denial_rate_recent_2k_smoothed` | Last 2,000 claims per payer | `mv_payer_denial_rates_recent_2k` | `(denied + α·prior) / (volume + α)` |
| `payer_overall_denial_rate_90d_smoothed` | Last 90 days per payer (anchored to `max(service_from_date)`) | `mv_payer_denial_rates_90d` | Same formula |

Constants: `α = 25`, `prior = 0.2772` (empirical lifetime denial rate per CR-124). Training-time path uses strict-< filtering on `service_from_date` to prevent leakage; predict path reads the MV directly.

### Historical features

Category G + Category H. Patient features are read from `mv_patient_claim_history` (windowed `patient_seq`); provider features from `mv_provider_denial_profiles`, `mv_provider_payer_denial_rate`, `mv_provider_cpt_denial_rate`.

### Joint features

Category I. Six joint denial-rate MVs power composite encoders. At train time these are overridden per row with leakage-safe rates (`merge_asof(allow_exact_matches=False)` on `service_from_date`). At predict time the global MV value is used.

### Per-variant feature counts (post-CR-122B)

| Variant id | Subtype | Universal | + Variant block | + Lifecycle | Total |
| --- | --- | --- | --- | --- | --- |
| 837P_healthcare | healthcare | 100 | 4 | 0 | 104 |
| 837P_therapy | therapy | 100 | 9 | 0 | 109 |
| 837P_transport | transport | 100 | 8 | 0 | 108 |
| 837I_home_care | home_care | 100 | 11 | 11 | 122 |
| 837I_institutional_other | inpatient / hospice / specialty | 100 | 5 | 0 | 105 |
| 837D_dental | dental | 100 | 8 | 0 | 108 |
| 837P_specialty | oncology / DME / behavioral / lab-rad | 100 | 10 | 0 | 110 |
| `_global` (fallback) | — | 100 | 0 | 0 | 100 |

(The exact production numbers reported in `feature_schema.json` may differ by ±1 due to schema_version drift; the orders-of-magnitude above are correct.)

### LeakageSafeTargetEncoder

- **Train time:** sklearn `TargetEncoder(smooth="auto", target_type="binary", cv=5, random_state=42)`. Each training row's encoded value comes from a model trained on the other four folds — strict OOF, no label leakage.
- **Cold-start safety:** clamps CV folds to minority-class count for small/imbalanced sets (CR-036).
- **Predict time:** the encoder is loaded from `encoder.joblib` and applied with `transform_row({"payer_canonical_name": "AETNA", ...})`. Unseen categories fall back to the training global mean.
- **Persisted state per column:** `encoder`, `vocabulary` (frozenset for unseen detection), `global_mean`, `n_training_rows`.

### M1 strict column-order check

Every variant's exact ordered column tuple lives in `registry.py`:

```python
FEATURE_COLUMNS_HEALTHCARE = _UNIVERSAL_COLUMNS + tuple(s.name for s in _HEALTHCARE_VARIANT)
```

`validate_feature_frame(df, variant, subtype, …)` asserts `list(df.columns) == expected` (exact order, missing, extra). XGBoost will silently mis-attribute SHAP if order drifts — so order-mismatch is a hard `FeatureSchemaError`. Enforced after `_assemble()` in both training and prediction.

---

## SECTION 9 — Major CRs / enhancements (CR-079 onward)

### CR-079 — 2026-06-16 — FeatureBuilder hyperparameter tuning (Optuna Balanced)
**Problem:** Phase 4 AIR approved tuning the three trainable FB variants; existing trainer used fixed hyperparameters.
**Solution:** Built a separable per-variant tuning harness (Optuna 4.9, TPE + MedianPruner, 60 trials/variant), promotion gates per variant. Promoted only 837I/home_care.
**Impact:** 837I bundle replaced (n_estimators 200, max_depth 7, lr 0.0149); F1 0.9545 (+6.32 pp), PR-AUC 0.9658 (+4.06 pp); threshold 0.150 → 0.230. 837P / 837D unchanged.

### CR-080 — 2026-06-16 — Consolidated training-history entries
**Problem:** Training history card showed three rows per `/train` (one per variant).
**Solution:** API-boundary grouping with a 60-second window; deterministic synthetic `training_run_id`. Zero schema change.
**Impact:** One row per training invocation with three variant blocks side-by-side.

### CR-081 — 2026-06-16 — Promotion safety + runtime consistency
**Problem:** Promoting a tuned bundle on disk didn't invalidate `_FB_PREDICTOR_CACHE` in the running uvicorn.
**Solution:** New endpoint `POST /api/predictions/reload-bundles`. Promotion workflow: dry-run → preview → apply → reload → confirm. `scripts/run_dev.{sh,ps1}` enforce `--reload`.
**Impact:** Bundle promotion no longer requires backend restart.

### CR-082 + CR-082A — 2026-06-16 — Legacy database deletion (~8.66 GB)
**Problem:** Two orphaned legacy DBs (`rcm_denials` 8.6 GB, `rcm_v2_verify` 45 MB) on native PG `:5432`.
**Solution:** 5-step verification (code grep, FB smoke test, pg_stat_activity audit), repointed `bench_parser.py`, dropped both DBs.
**Impact:** 8.66 GB reclaimed; single authoritative docker PG on `:5433`.

### CR-082B — 2026-06-16 — Documentation sync after legacy-DB deletion
**Solution:** README + integration-test docstrings updated to docker PG. Added "Docker PG is authoritative" subsection.

### CR-083 — 2026-06-17 — Training corpus integrity (refresh mv_claim_labels before /train)
**Problem:** 400-file bulk upload added 14,250 claims but the next `/train` produced identical `total_claims_used` rows — trainer was silently operating on a stale MV.
**Solution:** Refresh `mv_claim_labels` as the first statement in `/train`; abort with HTTP 503 if refresh fails.
**Impact:** Every successful training run operates on a corpus reflecting the actual current state.

### CR-084A — 2026-06-17 — Hyperparameter tuning re-validation on CR-083 corpus
**Solution:** Re-ran balanced tuning (60 trials/variant) on the post-CR-083 corpus (3–4× larger for dental / home_care).
**Impact:** CR-079 decisions confirmed: keep 837P (F1 −0.0056), keep 837D (F1 −0.0036), recommend 837I promotion (F1 +0.0280).

### CR-084B — 2026-06-17 — 837I tuned-bundle promotion (manual override)
**Problem:** 837I candidate passed most gates but barely missed PR-AUC threshold (Δ +0.0033 vs +0.008 required).
**Solution:** Manual atomic file move (not script gate override). Promotion gate unchanged.
**Impact:** 837I → v1.fb.tuned.20260617T064320, threshold 0.26, F1 0.8915.

### CR-085 — 2026-06-17 — Dental performance investigation
**Problem:** Despite 4× corpus growth + tuning, dental F1 stuck ~0.66.
**Solution:** Audit-only investigation: label quality, SHAP, feature coverage, reference data, distribution.
**Impact:** Binding constraints are information (missing dx in 67 % of dental claims; 7 ref tables empty), not model capacity. Ranked remediations: populate ref data (+4–7 pp F1), source diverse EDI (+5–10 pp), add dental MVs (+1–3 pp).

### CR-086 — 2026-06-17 — Replicate X12 `code_masters` to local docker
**Solution:** PostgreSQL COPY of 1,506 rows (308 CARC + 1,198 RARC) from remote → local.
**Impact:** X12 denial codes resolvable locally with full metadata.

### CR-087 — 2026-06-17 — Load HCPCS + ICD-10-CM reference data
**Solution:** Loaded HCPCS July 2026 (8,724 codes) + ICD-10-CM FY2026 (74,719 codes) from public CMS sources. Stubs for AMA-licensed CPT / ADA-licensed CDT only where codes already in corpus.
**Impact:** Reference data scaffolded for human surfaces. **Caveat:** FE pipeline did not yet load this data — fixed by CR-088.

### CR-088 — 2026-06-17 — FeatureBuilder reference-data activation
**Solution:** Implemented `RefDataLookup.from_session()` async loader (5 SELECTs per ref table). Extended Category K encoder with `cpt_category_encoded` + `primary_dx_chapter_encoded`. Retrained all 3 variants.
**Impact:** Healthcare F1 +3.4 pp (0.9457 → 0.9798); dental unchanged (corpus constraint); home_care mixed. **Realised CR-081 hazard:** tuned 837I bundle from CR-084B silently overwritten by /train.

### CR-090 — 2026-06-17 — Auto-refresh `mv_claim_labels` after upload (debounced)
**Solution:** `schedule_mv_refresh()` marks MV dirty; worker waits 5 s of quiescence then fires one `REFRESH CONCURRENTLY`. Burst uploads coalesce.
**Impact:** 200-file bulk upload completes in ~28 s with MV refreshed ~5 s later (one refresh, not 200). Per-upload latency unchanged.

### CR-091 — 2026-06-17 — D + I optimisation pass
**Solution:** 6-step optimisation: stock retrain, SHAP audit, coverage diff, tuning (60 trials), gate evaluation, threshold-only fix. Discovered 837I threshold bug (selector chose 0.15; held-out optimum 0.23).
**Impact:** Manual 837I threshold 0.15 → 0.23 recovered +5.22 pp F1 without retraining. Dental ceiling confirmed corpus-diversity-bound.

### CR-092 — 2026-06-18 — Explanation-layer correctness
**Problem:** (1) History-related denials attributed to billing. (2) Raw FB column names leaking through API. (3) CARC/RARC bucket mapping fragmented.
**Solution:** (1) `_VARIANT_BUCKET_OVERRIDES` (e.g., `total_units` → history on home_care only). (2) Route `simple_pipeline` reasons through `render_risk_factors`. (3) Canonical `denial_buckets.py` with per-code overrides.
**Impact:** History-bucket alignment 0/7 → 4/14 (28.6 %). Raw-feature leakage 6 → 0. Prediction behaviour unchanged.

### CR-092A — 2026-06-18 — Cleanup of temporary audit artifacts
**Solution:** Deleted only CR-083+ scope temporary files. CHANGELOG preserves all numeric results.

### CR-093 — 2026-06-18 — Denial-reason prioritisation (actionability over SHAP)
**Problem:** Operators assumed "first reason shown is the highest-value thing to fix" but renderer sorted by pure SHAP magnitude. 17.6 % of HIGH-risk claims showed informational reasons first.
**Solution:** Per-bucket actionability tier (0 directly actionable, 1 partially, 2 informational). Sort key changed from `-impact` to `(tier, -impact)`.
**Impact:** Tier-A-first claims 34.4 % → 94.4 %. Tier-C-first 17.6 % → 0 %. SHAP impact still shown.

### CR-104 — 2026-06-19 — Tier-A dead-feature retirement (9 features removed)
**Problem:** CR-103 validated that 9 Tier-A dead features (gain=0, constant-valued) could be removed without metric regression.
**Solution:** Dropped from registry + category modules; retrained all 3 variants.
**Impact:** Universal columns 108 → 101. Bundles slightly faster.

### CR-107 — 2026-06-19 — Leakage-safe training for 8 MV-backed denial-rate features
**Problem:** CR-106 found 8 MV-backed denial-rate features had no temporal filter — training rows could see future labels (99.1 % of claims share cohorts). Inflated held-out ROC by 0.11–0.36.
**Solution:** `compute_leakage_safe_denial_rates` overrides each training row with denied_count/volume over earlier-dated TRAIN rows only. Strict-< on `service_from_date`.
**Impact:** Held-out ROC now honest: 837P 0.9974 → 0.9970, 837D 0.7811 → 0.7841, 837I 0.8952 → 0.8991. Production / recent-sample ROC unchanged.

### CR-112 — 2026-06-19 — 837I isotonic recalibration
**Problem:** After CR-109 ingested 12,505 new home_care claims, calibrator was 47 pp over-confident (predicted 98.6 %, actual 51.6 %).
**Solution:** Refit isotonic on fresh validation slice (70/30 pre/post-CR-109 mix); re-derived threshold.
**Impact:** Brier −26 %, ECE −91 %. Threshold raised 0.01 → 0.75. HIGH-bucket precision 0.48 → 0.74.

### CR-114 — 2026-06-22 — 837I threshold correction (0.75 → 0.50)
**Problem:** CR-113 audit found HIGH bucket empty in practice (calibrator peaks near 0.75; no production score crossed it).
**Solution:** Threshold 0.75 → 0.50 (calibrator output mid-range).
**Impact:** HIGH bucket repopulated. Recall hits 100 % on audit cohort, FPR −46 pp (0.989 → 0.527), precision +12 pp (0.728 → 0.851).

### CR-115 — 2026-06-22 — Replacement-claim prediction audit
**Solution:** Audit-only. Ran FB predictors on every freq=7 claim; compared predicted bucket to actual outcome; inspected SHAP carry-over.
**Impact:** Booster has zero signal to differentiate corrected vs uncorrected replacements. `is_replacement_claim` + frequency-based history dominate. Root cause: missing lifecycle features. Fix deferred to CR-116+.

### CR-116 — 2026-06-22 — AIR: lifecycle-aware replacement features
**Solution:** AIR-only design (12-feature lifecycle-aware design, 3-stage rollout). Route B (query-time CTE) chosen to minimise DB migration burden.
**Impact:** Design locked in for CR-117/118/120. No code change.

### CR-117 — 2026-06-22 — Lifecycle features Stage 1 (6 features; compute path only)
**Solution:** New `lifecycle.py` + `load_original_snapshots` loader. Computes `had_prior_denial`, `prior_denial_bucket`, `days_since_original_denial`, `auth_added_in_replacement`, `referral_added_in_replacement`, `correction_action_count`. Not yet wired into `FeatureBuilder._assemble`.
**Impact:** Features computable on demand: 352/600 replacements have resolvable original; 28/600 show prior denial; 252/600 show auth added. M1 invariant preserved.

### CR-118 — 2026-06-22 — Lifecycle features Stage 2 (5 correction-delta features)
**Solution:** Added `modifier_added_in_replacement`, `diagnosis_changed_in_replacement`, `procedure_changed_in_replacement`, `lines_changed_in_replacement`, `charge_changed_in_replacement`. `correction_action_count` redefined to sum all 7 flags (max 7).
**Impact:** Lifecycle set complete (11 features). `correction_action_count > 1` rose from 0.3 % to 2.7 % on freq=7 sample — 8× lift.

### CR-119 — 2026-06-22 — AIR: lifecycle feature integration strategy
**Solution:** AIR-only. Atomic CR-120 strategy locked in: new `_LIFECYCLE` tuple appended to `_BASE`, mandatory calibrator refit + threshold re-derivation. Training-corpus choice (freq=1 only vs freq=1+freq=7) deferred to CR-120A.

### CR-120A — 2026-06-22 — Training strategy validation (freq=1 vs freq=1+freq=7)
**Solution:** Opt-in `include_lifecycle` flag (default False). Trained two candidate bundles per variant into separate artifact directories. Evaluated on fixed freq=7 audit cohort + fixed freq=1 holdout.
**Impact:** Candidate A regresses every variant. Candidate B on 837P / 837D degenerates (booster learns `is_replacement_claim → denied` shortcut). Candidate B on 837I substantive improvement: FPR −46 pp, precision +12 pp, F1 +12 pp. **Recommendation: variant-specific promotion for 837I/B only.**

### CR-120 — 2026-06-22 — Variant-specific lifecycle promotion (837I only)
**Solution:** Promoted Candidate B for 837I/home_care only. 837P / 837D unchanged.
**Impact:** 837I bundle v1.fb.20260622T104132 (123 cols, include_lifecycle=True, threshold 0.01). High-bucket FPR on replacements 0.989 → 0.527; precision 0.728 → 0.851.

### CR-121 — 2026-06-22 — Post-promotion lifecycle verification
**Impact:** All CR-120A lift reproduced in production. Lifecycle features fire on ~72 % of replacements; 4 of 11 alive at booster (combined 3.39 % gain). Identified structural MEDIUM-bucket emptiness (threshold 0.01 < LOW_PROB_CUTOFF 0.05).

### CR-122 — 2026-06-22 — AIR: frequency-code feature consistency
**Impact:** Confirmed `frequency_code_encoded` dead (gain=0 all variants). Recommended retirement.

### CR-122B — 2026-06-22 — Retire `frequency_code_encoded` (3-variant retrain)
**Impact:** Universal columns 101 → 100. Variant counts: 837P 105→104, 837D 109→108, 837I 123→122. Booster output unchanged (gain=0 column cannot affect ranking). 837D threshold 0.01 → 0.34 (MEDIUM bucket now reachable).

### CR-121B — 2026-06-22 — Lifecycle bundle re-verification (post-CR-122B)
**Impact:** All CR-121 measurements reproduced exactly on the 122-column bundle (v1.fb.20260622T130757).

### CR-124 — 2026-06-24 — Payer denial-rate feature audit (30 D / 90 D)
**Impact:** Only 2 of 5 requested features exist; the rest don't. All 9 production denial-rate features are lifetime (no date window). 30 D window too thin (178 claims). 90 D shows robust top-25 leaderboard. Identified suspicious clusters (high-volume synthetic batches, umbrella-brand payers with zero claims).

### CR-126 — 2026-06-24 — Recency-window shootout
**Solution:** 1 experimental booster per variant carrying 5 recency candidates (recent_2k, recent_5k, recent_10k, 90d, lifetime_smoothed). Isolated artefacts under `artifacts/experiments/cr126/`.
**Impact:** recent_2k wins on 837I (12.28 % gain); 90d wins on 837P (0.88 % gain). recent_10k + lifetime_smoothed universally dead. Recommended adoption of BOTH recent_2k and 90d. 837I freq=7 quality **dramatically improved**: FPR 0.527 → 0.052 (−47.5 pp), F1 0.919 → 0.992 (+7 pp).

### CR-126C — 2026-06-24 — CPT/DX recency denial-rate audit
**Impact:** Documentation of baseline for future reference; informs CR-126B implementation scope.

---

## SECTION 10 — Recommendation engine

`POST /api/recommendations/by-file/{edi_file_id}` is a composite per-claim endpoint that fuses three orthogonal signal sources into a single response.

### 1. CARC-driven recommendations (source = `"carc"`)

For every adjudicated remit, the system queries `remittance_claims ⨝ adjustments` by `claim_number` and maps each adjustment's `adjustment_reason_code` (CARC) to a hardcoded `(reason, fix)` tuple in `_CARC_FIXES`. Example entries:

| CARC | Reason | Recommended fix |
| ---: | --- | --- |
| 1 | Deductible amount | Verify deductible balance with the payer; re-bill if exhausted. |
| 16 | Claim lacks information required for adjudication | Re-check service-line composites (procedure / modifiers / pointers) and resubmit. |
| 18 | Exact duplicate claim/service | Confirm with the payer that the prior claim was received; do not resubmit blindly. |
| 29 | Time limit for filing expired | Submit a timely-filing appeal with documentation of the original submission date. |
| 45 | Charge exceeds fee schedule / contracted amount | Confirm contracted rate; write off the contractual difference. |
| 50 | Non-covered service per medical-necessity policy | Add medical-necessity documentation (LCD/NCD reference) and submit a corrected claim. |
| 96 / 97 / 109 / 204 | (similar entries) | (similar guidance) |

If the CARC is not in `_CARC_FIXES`, a generic fallback is emitted: "Look up CARC {code} on the payer's reason-code reference…".

The UI labels these recommendations with a `CARC` badge.

### 2. Parser-validator recommendations (source = `"parser"`)

For every `parse_event` row with `event_type IN ('validator_error', 'claim_dropped')` matching the file, the system extracts the `segment_name`, `field`, and `message` from the JSONB `details` column and surfaces a recommendation with location `{segment_name}.{field}` and a generic fix: "Open the 837 in the EDI Inspector → Parse Trace and correct the segment, then re-submit."

UI badge: `Parser`.

### 3. ML risk-factor recommendations (source = `"model"`)

When a claim has been scored by `POST /api/predictions/predict-file/{id}`, the `top_risk_factors` list from the prediction is run through `reason_renderer.render_risk_factors(factors, top_k=5, claim_subtype=…)`:

1. Each raw FB feature name maps to one of 11 canonical buckets (`authorization`, `documentation`, `procedure`, `diagnosis`, `timely_filing`, `coverage`, `billing`, `provider`, `history`, `similar`, `general`).
2. A variant-aware bucket override may re-route a feature (CR-092 Issue 1) — e.g., `total_units` lands in `history` only on `home_care`.
3. The variant-aware sentence override (CR-078A) produces care-setting-specific language (e.g., on dental, "Treatment documentation may require review.").
4. Sort key is `(actionability_tier, -|shap|)` so directly actionable buckets surface first (CR-093).
5. Top-5 items returned with `(slug, label, sentence, impact, direction='increases denial risk')`.

UI badge: `ML`.

### Status badge logic

Each claim gets one of:

| Badge | Condition |
| --- | --- |
| `Resolved` | Matched 835 with `paid_amount > 0` and no outstanding recommendations. |
| `Denied` | Matched 835 with `CLP02='4'` — at least one CARC recommendation present. |
| `High Risk` | ML prediction puts the claim in HIGH bucket but no remit yet. |

### Recommendation success tracking

The `prediction_log` table records every prediction with `model_version`, `feature_engineering_version`, `calibrator_version`, `decision_threshold`, `top_risk_factors`, `unseen_indicators`, `pipeline_name`, `prediction_group_id`. When the 835 arrives, the matched row updates `actual_denied`, `actual_status`, `resolved_at`, `resolved_by_remittance_id`. Pairing logic relies on `pending_claim_pairs` (CR-068A) to track original → replacement linkage and prevent false-positive "no replacement yet" warnings.

Shadow logging (CR-065) runs the alternate pipeline (`featurebuilder` ↔ `simple_pipeline`) in parallel with the production path; both rows share a `prediction_group_id` so post-hoc parity audits can compare predictions per claim.

---

## SECTION 11 — User workflow

1. **Upload 837** — Operator drops a 837 file onto the blue UploadCard. The frontend POSTs to `/api/edi/upload`; the response includes parse status, claims_count, validation errors, and `pair_status`. A warning surfaces if the 837 is a replacement (freq=7) without an original on file.

2. **Parse** — Backend runs `parse_and_save`: envelope decoding, ISA hardening, ~30 handlers, 4-tier validation, bulk persistence. Failed claims are dropped; their raw_segments are still retained. The Upload card switches to a green check (or red error) within seconds.

3. **Feature engineering** — Implicit on training; explicit on prediction. `FeatureBuilder.transform()` runs per-claim against the loaded encoder + rarity state + MV snapshots.

4. **Prediction** — Operator clicks the "Predict risk" action (or the system triggers it automatically post-upload). `/api/predictions/predict-file/{id}` returns:
   - `risk_summary` — counts of HIGH / MEDIUM / LOW.
   - `high_risk_claims[]` — per-claim risk_score, risk_level, top denial reasons (in business language, with actionability ordering).

5. **Risk scoring** — Each claim's calibrated score is bucketed:
   - `HIGH` if `score ≥ decision_threshold`
   - `MEDIUM` if `score ≥ LOW_PROB_CUTOFF (0.05)`
   - `LOW` otherwise.
   The DenialRiskCard on `ClaimDetailPage` displays the score %, the top-5 reasons, and unseen-indicator warnings (e.g., "Payer was not seen during training").

6. **Recommendations** — Operator opens the "Recommended fixes" panel. `/api/recommendations/by-file/{id}` returns the composite per-claim list (CARC + Parser + ML). Each claim's `status_badge` indicates whether it is `Resolved`, `Denied`, or `High Risk`.

7. **Upload 835** — Once the payer responds, operator drops the 835 file onto the green UploadCard. Backend parses CLP/CAS/LQ/SVC, links each remit to its claim by `claim_number`, persists adjustments + remark codes. The MV refresh is scheduled (debounced).

8. **Outcome tracking** — The matched `claim_id` in `prediction_log` gets `actual_denied`, `actual_status`, `resolved_at`, `resolved_by_remittance_id` back-filled. Each claim's `status_badge` updates: `Denied` claims surface their CARC-derived recommendations; `Resolved` claims get a green check and disabled rec rows.

9. **Analytics** — Operator opens the TrainModelCard / TrainingHistoryCard. `/api/predictions/dataset-stats` shows corpus size, denied count, paid count, and overall denial rate. `/api/ml/training-history` shows the per-variant timeline of training runs with held-out F1 / Precision / Recall / ROC-AUC. Retraining is a single click; metrics post within seconds to minutes depending on corpus size.

---

## SECTION 12 — Results

### Current production models (as of 2026-06-24)

| Variant | Subtype | Model version | Features | Threshold | Training rows | Denial rate |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 837P | healthcare | `v1.fb.20260624T044257.837P_healthcare` | 99 | 0.05 | 29,960 (TRAIN of 42,801 total) | 4.76 % |
| 837I | home_care  | `v1.fb.20260624T044308.837I_home_care`   | 71 (with lifecycle) | 0.50 (post-CR-114) | 10,556 (TRAIN of 15,080 total) | 20.96 % |
| 837D | dental    | `v1.fb.20260624T044055.837D_dental`      | 74 | 0.34 | 8,457 (TRAIN of 12,082 total) | 1.19 % |

### Held-out metrics

| Metric | 837P / healthcare | 837I / home_care | 837D / dental |
| --- | ---: | ---: | ---: |
| ROC-AUC | **0.9979** | **0.9962** | 0.9528 |
| PR-AUC | **0.9896** | **0.9807** | 0.3014 |
| Precision @ threshold | 0.9460 | 0.8333 | 0.6000 |
| Recall @ threshold | 0.9739 | 0.9916 | 0.1364 |
| F1 @ threshold | 0.9597 | 0.9056 | 0.2222 |
| Accuracy @ threshold | 0.9961 | 0.9567 | 0.9884 |
| Brier (calibrated) | 0.0022 | 0.0182 | 0.0099 |

### Calibration quality

- **Healthcare & home_care.** Brier 0.002–0.018; isotonic monotonicity test passes. After CR-112 the home_care calibrator regression (98.6 % predicted vs 51.6 % actual) was eliminated; CR-114 then re-aligned the decision threshold so the HIGH bucket is non-empty in production.
- **Dental.** Brier 0.0099, but the precision-floor of 0.85 is unreachable on a 1.19 % prevalence corpus — threshold fell back to max-precision at 0.34 (after CR-122B retrain, 0.01 → 0.34, which made the MEDIUM bucket reachable for the first time).

### Feature-importance findings

- **Healthcare.** Top SHAP groups are typically `payer_overall_denial_rate`, `payer_cpt_denial_rate`, `cpt_dx_denial_rate`, and `is_replacement_claim`. After CR-088 added `cpt_category_encoded` + `primary_dx_chapter_encoded`, those two enter the top-10 immediately (+3.4 pp F1).
- **Home care.** Lifecycle features carry meaningful signal: of the 11, four are alive at the booster (combined 3.39 % gain), dominated by `correction_action_count`. After CR-126, `payer_overall_denial_rate_recent_2k_smoothed` becomes the single largest gain feature on 837I (12.28 %), and on freq=7 claims drives FPR 0.527 → 0.052 (CR-126 audit).
- **Dental.** Constant on ~68/116 features at the time of CR-085. After CR-088 + CR-104 + CR-122B the surviving feature set is roughly 74 and the model's discriminative ceiling is corpus-limited.

### Lifecycle-feature findings (CR-117–CR-121B)

- 352 / 600 replacements in the audit cohort have a resolvable freq-1 original.
- 28 / 600 originals were denied; 252 / 600 add an auth in the replacement; 16 / 600 carry `correction_action_count > 1`.
- Wiring lifecycle features into the booster + retraining with freq=1+freq=7 corpus (CR-120, 837I only) cut HIGH-bucket FPR on replacements from 0.989 to 0.527 and lifted F1 by ~12 pp on the audit cohort.

### Recency-feature findings (CR-126)

- `payer_overall_denial_rate_recent_2k_smoothed` is the single largest-gain feature on 837I when added; `payer_overall_denial_rate_90d_smoothed` is the best on 837P.
- `recent_10k` and `lifetime_smoothed` candidates are universally dead (0 % gain) — recency must be tighter than 10k or 90 days to add signal.
- Combined effect on 837I freq=7: F1 0.919 → 0.992; FPR 0.527 → 0.052.

---

## SECTION 13 — Challenges faced

1. **Dataset quality.** The healthcare corpus (~43 k claims) is broad and synthetic-seeded; dental (~12 k) is shallow and missing diagnosis codes on 67 % of rows (CR-085). The system explicitly attributes dental's ceiling to *corpus diversity*, not model capacity, and proposes ranked remediations (reference data, diverse EDI sources, dental-specific MVs) instead of more tuning trials.

2. **Feature leakage.** Initial denial-rate features were computed without temporal filtering; 99.1 % of training rows shared cohorts with future rows, inflating held-out ROC by 0.11–0.36. Fixed in CR-107 by `merge_asof(allow_exact_matches=False)` on `service_from_date`, producing honest held-out metrics for the first time.

3. **Calibration drift.** After CR-109 ingested 12.5 k new home_care claims, the existing isotonic calibrator was 47 pp over-confident on the new cohort. CR-112 refit the calibrator on a mixed pre/post slice; CR-114 then corrected the decision threshold so the HIGH bucket repopulated in production.

4. **Threshold tuning.** The trainer's selector minimised some criteria but produced a 0.15 threshold for 837I when the held-out optimum was 0.23 (CR-091). Manual threshold edits, validated against held-out metrics, were used as a fast path rather than retraining from scratch.

5. **Replacement-claim handling.** Before CR-117/CR-118/CR-120, the FE space had no information about original→replacement diff. The booster systematically flagged corrected replacements as HIGH risk because `is_replacement_claim` dominated. The fix was a three-stage rollout: compute the diff (CR-117/118), validate the training strategy (CR-120A), promote variant-specifically (CR-120 for 837I only — 837P / 837D degenerated when freq=7 rows were added).

6. **Data drift.** The corpus grew 3–4× for dental and home_care in mid-June. CR-083 ensured `mv_claim_labels` is refreshed before every `/train`, with a 503 abort on failure — no more silent training on stale labels. CR-090 added debounced background refresh after every upload so the MV stays current without operator action.

7. **Bundle promotion races.** Promoting a tuned bundle on disk did not invalidate the in-process predictor cache (CR-081). Two specific incidents made this visible: CR-088's retrain silently overwrote the CR-084B tuned 837I bundle, and operators got stale predictions from a freshly promoted bundle. Fixed with `POST /api/predictions/reload-bundles` + `scripts/run_dev` enforcing `--reload`.

8. **Audit-log inflation (CR-051/CR-052).** A single `_propagate_remit_status_to_claims()` UPDATE touching every adjudicated claim after each 835 inflated `audit_log` to 7 M rows / 8 GB within days. CR-051 dropped the entire audit infrastructure (no active downstream consumer); CR-052 added IS-DISTINCT-FROM guards to skip no-op UPDATEs and batched the pair check.

9. **Explanation-layer hygiene.** Audits found three defects (CR-092): history-related denials landing in the billing bucket, raw FB column names leaking through API responses (e.g., `paperwork_missing_when_required` shown verbatim), and CARC/RARC bucket mapping fragmented across three places. Fixed by canonicalising `denial_buckets.py`, routing `simple_pipeline` reasons through the unified renderer, and adding variant-aware bucket overrides.

10. **Pgvector cluster bug.** During CR-009 validation, the remote dev cluster's pgvector 0.8.2 binary crashed the PG backend on distance operators (`<->`, `<#>`, `<=>`). Migrations succeed (no operator invocation), but RAG-time queries are blocked on the cluster. v2 documents this as a cluster ops issue (not a code defect) and uses local docker pgvector for any RAG development.

---

## SECTION 14 — Future enhancements

### Planned improvements

- **RAG-grounded recommendations.** The `knowledge_chunks` + `claim_embeddings` + `correction_examples` + `rag_generations` tables are scaffolded; the next phase wires an LLM that retrieves payer policy + LCD/NCD passages similar to the claim and drafts a remediation paragraph or appeal letter. The pgvector index is already HNSW-built; the operator dependency is a stable cluster.
- **Per-prediction RAG output log.** Tokens, latency, cost, user feedback already in `rag_generations`. Adds production observability for LLM-grounded surfaces.
- **Appeal letter generation.** `appeals` table fields `generated_letter`, `human_edits`, `final_letter` exist for the loop. The pattern is: ML flags HIGH, operator approves appeal, RAG drafts letter, operator edits, system saves the diff and uses it as a future correction example.
- **Drift baselines.** `mv_drift_baselines` already snapshots per-(variant, subtype) volume + prevalence. A monitoring job comparing live prediction distributions against the baseline would surface drift early.
- **Partition rollover automation.** Currently manual through alembic; a quarterly migration or pg_partman (when superuser is available) would close the gap.
- **Audit trail re-introduction (targeted).** CR-051 dropped audit_log entirely. A targeted re-introduction restricted to write endpoints (not every PHI table) would restore compliance value without the 7 M-row write inflation.

### Experimental ideas

- **Adopt recency features in production.** CR-126 showed `recent_2k` + `90d` payer denial rates wins on 837I (FPR −47.5 pp, F1 +7 pp) and 837P. CR-126B AIR would graduate the experimental features into the registry and retrain all three variants.
- **Variant-specific Optuna for 837P / 837D.** CR-084A confirmed 837P / 837D tuned candidates regress on the current corpora; with the next dataset infusion (more diverse dental EDI, CR-085's #2 remediation) tuning is likely to break the F1 ceiling.
- **Lifecycle features on 837P.** CR-120A showed 837P degenerated when freq=7 was added. With a larger replacement-claim corpus or a corrected "shortcut" feature (e.g., zero-out `is_replacement_claim` when lifecycle features are dense) 837P may join 837I in lifecycle-aware mode.
- **Public-API auth + multi-tenancy.** `tenants` + `users` already scaffolded; passlib + python-jose are pinned in `pyproject.toml`. Adding OIDC/JWT, per-tenant data isolation, and request_log-based rate limiting is the next step before any cloud deployment.

### Research opportunities

- **Causal feature evaluation.** SHAP magnitudes are correlational. Counterfactual evaluation ("what if the operator had added a modifier? would denial probability drop?") is the natural next research question.
- **Per-payer fine-tuning.** Currently one model per (variant, subtype). A second-tier per-payer model for the top 10 payers may close the payer-policy gap that `tier3_payer.py` partially fills.
- **Active learning loop.** Use HIGH-confidence wrong predictions as targets for fresh labelling. The shadow-logging pairs and held-out evaluation already provide the substrate.

---

## SECTION 15 — Internship contributions

This section captures the work, modules, enhancements, skills, and knowledge that materialised during the internship period and can be defended in viva.

### Major work completed

- **Vertical-slice feature engineering**: 14 categories, 7 variant blocks, leakage-safe target encoding, M1 strict column-order enforcement, reference-data graceful degradation, lifecycle and recency feature subsystems.
- **Production ML pipeline**: per-variant XGBoost training with isotonic calibration, precision-floor threshold derivation, artifact bundles (`model.json`, `calibrator.joblib`, `encoder.joblib`, `rarity_state.joblib`, `feature_schema.json`), and Optuna hyperparameter tuning.
- **Explanation layer**: SHAP-driven reason rendering with 11 canonical denial buckets, variant-aware sentence overrides, actionability-tier sorting.
- **Recommendation engine**: composite (CARC + parser + ML) per-claim endpoint with status badges and resolution tracking.
- **Operational hardening**: training-corpus integrity (CR-083), auto-MV-refresh after upload (CR-090), bundle promotion safety with cache invalidation (CR-081), and runtime-consistency enforcement.
- **Audit & investigation discipline**: every non-trivial change preceded by an 8-section Architecture Impact Review; audits (CR-085, CR-115, CR-121, CR-124, CR-126, CR-126C) systematically separate "model capacity" problems from "information" problems.

### Modules implemented or enhanced

| Path | What it does |
| --- | --- |
| `src/rcm/features/registry.py` | FeatureSpec list + per-variant ordered column tuples + M1 validator. |
| `src/rcm/features/builder.py` | `FeatureBuilder.fit_transform` and `transform` orchestration. |
| `src/rcm/features/encoders.py` | `LeakageSafeTargetEncoder` (5-fold OOF at train, deterministic at predict) + `RarityState`. |
| `src/rcm/features/dataset.py` | `load_training_corpus` + CR-071 denial propagation. |
| `src/rcm/features/categories/*.py` | 14 category modules (A coverage … Z availability + lifecycle). |
| `src/rcm/features/variants/*.py` | 7 variant blocks (healthcare, therapy, transport, home_care, dental, specialty, institutional_other) + global fallback. |
| `src/rcm/ml/trainer.py` | Training workflow: corpus load, split, fit, calibrate, threshold, persist. |
| `src/rcm/ml/predictor.py` | Variant predictors + booster.feature_names hard-check (CR-076 #3). |
| `src/rcm/ml/artifacts.py` | `ModelArtifactBundle` (save/load). |
| `src/rcm/ml/reason_renderer.py` | SHAP → bucket + variant override + actionability tier sort (CR-093). |
| `src/rcm/ml/denial_buckets.py` | CARC/RARC → bucket mapping (CR-092). |
| `src/rcm/ml/shadow.py` | Paired prediction logging. |
| `src/rcm/ml/simple_pipeline.py` | Fixed-vocabulary fallback model. |
| `src/rcm/parsing/{envelope,safe,context,parser,routing,persistence}.py` | EDI parsing foundation + persistence. |
| `src/rcm/parsing/handlers/*.py` | ~30 segment handlers across 837P/I/D + 835. |
| `src/rcm/parsing/validators/*.py` | 4-tier validator chain. |
| `src/rcm/routers/public/*.py` | EDI, claims, ML, predictions, recommendations routes. |
| `src/rcm/routers/dev/*.py` | Developer console (env, db, telemetry, claims, edi, jobs, models, uploads). |
| `src/migrations/versions/0001–0018` | 18 alembic migrations. |
| `frontend/src/pages/{Upload,Claims,ClaimDetail}Page.jsx` | React 18 + Tailwind UI. |
| `scripts/{cr079_tune,cr079_promote,bench_parser,verify_db_connection,run_dev}.{py,sh,ps1}` | Tuning + promotion + benchmark + verification + dev launcher. |

### Enhancements delivered

- 18 alembic migrations from 0001 (init) to 0018 (recency MVs).
- 14 materialised views, 4 PL/pgSQL functions, partitioning on 4 tables.
- 100 universal + 4–11 variant-specific features + 11 lifecycle features.
- 3 production model bundles (837P, 837I, 837D) with held-out ROC-AUC 0.95–0.998.
- Composite recommendation endpoint fusing CARC + parser + ML signals.
- Architecture Impact Review discipline (8-section template + red-flag checklist) enforced before every non-trivial change.
- 178 unit tests + 9 integration tests (parsing layer) + Phase 3 E2E pass.

### Technical skills applied

- **Healthcare EDI domain**: X12 837P/I/D + 835 implementation guides, CARC/RARC semantics, ICD-10-CM / CPT / HCPCS / CDT / HIPPS code systems, claim lifecycle (frequency codes, replacement, appeals).
- **Python 3.12** with FastAPI, SQLAlchemy 2.0 (async ORM + Core inserts), Pydantic v2, asyncpg, structlog.
- **Databases**: PostgreSQL 16/18, partitioning, materialised views, partial / GIN / HNSW indexes, PL/pgSQL, alembic migrations, pgvector.
- **Machine learning**: XGBoost, scikit-learn (`TargetEncoder`, `IsotonicRegression`, `StratifiedKFold`, `merge_asof`), Optuna 4.x (TPE + MedianPruner), SHAP, joblib.
- **Frontend**: React 18 (Hooks), TanStack React Query + Table, react-router 6, Tailwind 3, recharts, Vite 5, Monaco editor.
- **DevOps & tooling**: Docker Compose, uv, ruff, mypy, pytest, pytest-asyncio.
- **Engineering practice**: Architecture Impact Reviews, change-log discipline, leakage-safe ML, calibration + threshold derivation, drift baselines, shadow logging, M1 contract enforcement.

### Knowledge gained

- How to design a database schema that bakes failure-mode lessons (Lessons C3, P1, P2, P3, M1, H5/H6) into NOT NULL / NULLABLE choices, partial indexes, composite PKs, and validator semantics.
- Why isotonic calibration beats sigmoid/Platt for medical claim denial probability (non-parametric, monotonic, robust to bi-modal label distributions).
- Why precision-floor threshold selection is the right business-aligned algorithm (not Youden's J or F1-max) for pre-submission denial flagging.
- How to prevent silent SHAP misattribution in XGBoost (booster.feature_names + M1 column-order check, hard-fail at predict time).
- How to write leakage-safe target encoders (5-fold OOF at train, deterministic global transform at predict, persisted vocabulary + global mean).
- How to deal with replacement-claim signal (lifecycle features + correction-deltas + freq=1+freq=7 training strategy, variant-specific promotion).
- The discipline of running an audit before changing code: e.g., `CR-085` (dental performance investigation), `CR-115` (replacement-claim audit), `CR-124` (payer denial-rate feature audit). Many of the most consequential decisions in this project were *not to ship* a proposed change because the audit revealed a more fundamental constraint.

---

*End of project knowledge dump. Compiled directly from the source repository at*  
`C:\Users\Gowdham B\Documents\RCM Denial Management\Universal-RCM-Denial-Management`  
*on 2026-06-24. For raw audit trail, see `CHANGELOG.md` (9,134 lines, CR-001 to CR-126C).*
