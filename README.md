# Universal RCM Denial Management — v2

Multi-variant denial prediction across 837P / 837I / 837D / 835 EDI transactions.
Per-variant ML models, leakage-safe feature engineering, RAG-ready recommendation hooks.

This phase delivers: **database + parsing + feature engineering** only.

## Quickstart

```bash
# 1. Set up Python env
uv venv && source .venv/Scripts/activate     # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"

# 2a. Local dev DB (recommended for iteration)
docker compose up -d
# .env already points at localhost:5432

# 2b. OR target the remote dev DB
mv .env .env.local.bak && mv .env.remote .env

# 3. Verify connectivity
python scripts/verify_db_connection.py

# 4. Apply migrations
alembic upgrade head

# 5. Re-verify to confirm tables exist
python scripts/verify_db_connection.py

# 6. (Optional) Start the developer console
#    Terminal A — backend
PYTHONPATH=src uvicorn rcm.main:app --reload --port 8000
#    Terminal B — frontend
cd frontend && npm install && npm run dev
#    Browser → http://127.0.0.1:5173
```

## Developer console (`/api/dev/*` + frontend at 127.0.0.1:5173)

Local-only observability surface. Backend FastAPI under `src/rcm/main.py`,
frontend React/Vite/Tailwind under `frontend/`. **Not a production user UI**
— PHI toggle defaults OFF, write endpoints require `?confirm=true`.

Currently working pages:
- **Home** — system-health tiles
- **Environment** — DB info, extensions, PG settings, versions

Pages on the roadmap (visible-but-disabled in sidebar): EDI Inspector,
Claims Browser, Parsing Telemetry, ML Models, Predictions, Features, RAG,
Database, Jobs, Audit. See `CHANGELOG.md` CR-038 for the design + phasing.

## Database connection

* `.env` (gitignored) holds the live config — points at local docker by default
* `.env.remote` (gitignored) is pre-filled for the shared remote dev cluster
* `.env.example` (committed) is the template

**Never commit `.env` or `.env.remote`.** Verify with `git check-ignore .env`.

URL-encode special characters in passwords:
`!` → `%21`, `#` → `%23`, `@` → `%40`.

## Repo layout

```
src/
├── rcm/
│   ├── core/              # config, async engine, enums, logging
│   ├── models/            # SQLAlchemy 2.0 mapped classes by domain
│   ├── schemas/           # Pydantic v2 request/response models (later)
│   ├── parsing/           # EDI handlers, validators, persistence (later)
│   ├── features/          # per-variant feature engineering (later)
│   ├── ml/                # training + per-variant predictors (later)
│   ├── services/          # recommendation provider interface (later)
│   ├── routers/           # FastAPI routes (later)
│   ├── workers/           # arq tasks (later)
│   └── main.py            # app factory (later)
└── migrations/            # alembic chain
scripts/                   # one-off utilities (verify_db_connection.py, etc.)
tests/
```

## Known remote-DB constraints

The shared remote dev PG cluster (Bitnami container on 104.130.220.20) has:

* `maintenance_work_mem = 64MB` — build indexes `CONCURRENTLY`
* `max_wal_size = 400MB` — keep bulk inserts in ≤5k-row batches
* Idle connection drops — `pool_pre_ping=True` + `pool_recycle=1800` already set
* Shared with other teams — schedule heavy work off-hours

For `pgvector` / `pg_partman` / `pg_cron`: coordinate `CREATE EXTENSION` with the DBA (requires superuser). Local docker image (`pgvector/pgvector:pg16`) only ships pgvector.

## Change history

See `CHANGELOG.md` for the full system change registry. Every entry documents
both what changed AND how the system behaves differently after the change,
with cross-references between related changes and bugs.

## Status

**DB layer — complete** (signed off; head = `0010_add_materialized_views`)
- 41 base tables + 25 partition children + 19 ENUMs + 12 materialized views
- 6 PL/pgSQL functions (incl. `find_similar_claims` over pgvector)
- Audit triggers on every PHI table
- Validated against remote PG 18.3 + pgvector 0.8.2

**Parsing layer — complete**
- Envelope: ISA hardening, encoding chain w/ ISA sanity, multi-ISA detection
- `ParseContext` + safe extractors (`safe_decimal`/`safe_date` — no `date.today()` placeholders)
- CAS triplet parser (stride-2/3 disambiguation)
- 16 segment handlers across `NM1/N1/PER/DMG/HL/SBR/CLM/SV1/SV2/SV3/HI/DTP/DTM/REF/CR1/CR3/CRC/TOO/DN1/DN2/PWK/AMT/NTE/CLP/CAS/LQ/SVC/MIA/MOA`
- 4-tier validator chain (structural / IG / payer-policy / business)
- Variant + subtype detection (837P/I/D vs healthcare/home_care/dental/therapy/transport/specialty)
- Bulk Core insert persistence with insert-then-catch dedup
- Reparse endpoint (soft-deletes original, re-runs from stored raw_text)
- **67 unit tests + 6 integration tests** (all pass; integration verified against remote PG)

**Feature engineering — vertical-slice complete (healthcare variant)**
- Full architecture: FeatureSpec registry, M1 strict column-order validation, denial-driven catalog
- 12 categories (A coverage / B authorization / C clinical / D coding / E timely / F documentation / G patient history (SQL) / H provider (MV) / I joint encoders (MV) / J base / K target-encoded (leakage-safe) / L rarity+unseen / Z availability)
- Reference-data fallback contract: features ALWAYS in schema, missing data → safe defaults + per-claim `reference_data_completeness` score
- Train/predict parity: single `FeatureBuilder` code path, encoder + rarity_state persisted in artifact bundle
- **114 features** for healthcare variant
- Minimal ML scaffold: `train_healthcare_model` (XGBoost + isotonic calibration + precision-floor threshold) and `HealthcarePredictor`
- 98/98 unit tests + Phase 3 E2E pass against remote PG (seed → MV refresh → train → reload → predict)

**Feature engineering — all variants registered + global fallback (CR-034–037)**
- 6 new variant blocks: therapy (+9), transport (+8), home_care (+11), dental (+8), specialty (+10 unifying oncology/DME/behavioral/lab-rad), institutional_other (+5)
- 10 real `(variant, subtype)` keys + `_global` fallback registered in dispatch
- 837I aliases: inpatient / hospice / specialty all route to institutional_other column list
- Unknown variant tuple → 108 universal columns (no crash, ever)
- Cold-start safe: encoder clamps cv to minority-class count for small/imbalanced training sets
- Fixed Phase 2 DTM*405 production-date fallback bug (was silently swallowed by dispatcher)
- 178 unit tests + 9 integration tests, all green

**Next phases (not yet started)**
- [ ] Developer Verification UI (per v2 plan: 12 pages, MVP = 6 pages)
- [ ] Phase 4 (ML): per-variant Optuna search, drift baselines, per-prediction `prediction_log` writes, global fallback model + `PredictorRouter`
- [ ] Phase 5: recommendation provider + RAG hooks
