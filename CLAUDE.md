# Project conventions for AI agent sessions

This file is read automatically at session start. It tells the agent how to
behave for THIS project specifically. Keep it short and prescriptive.

## Mandatory: maintain CHANGELOG.md

The `CHANGELOG.md` at repo root is the operational record of every change to
this system. **You MUST add a new entry for any meaningful change** — schema
migration, parsing-layer change, FE feature add/remove, ML pipeline change,
bug fix, perf optimization, decision made via AskUserQuestion.

Trivial changes that DO NOT need an entry:
- Typo fixes in comments / docstrings
- Whitespace / formatting only
- Test assertion text tweaks that don't change what's tested

Read `CHANGELOG.md` itself for the entry format. The format is non-negotiable
— every entry has 9 sections, and the section that distinguishes this file
from a git log is **"System behavior after this change"**. Future readers
should be able to understand what the system does differently because of
each entry without grepping code.

## Project-specific conventions

### Lessons baked into this code (do not regress)

Many design choices encode lessons from v1 failures. The CHANGELOG documents
which entries enforce which lesson. The most load-bearing ones:

- **C3**: `claims.service_from_date` is NULLABLE. NEVER use `date.today()` as
  a placeholder. If the date is missing, let the validator drop the row.
- **P1**: `remittance_claims.remittance_date` is NULLABLE. Missing DTM*050/
  *405 → WARNING (not ERROR); row persists with NULL.
- **P2**: Encoding fallback chain is `utf-8-sig → utf-8 → cp1252 → latin-1`
  with an ISA-presence sanity check. latin-1 alone never raises and silently
  accepts garbage.
- **P3**: `safe_date` emits WARN on non-empty unparseable input, returns None
  silently on empty input.
- **29c0fd4**: ISA delimiters must be non-alphanumeric, non-whitespace;
  `ISA[3] == ISA[6]` required.
- **M1**: Feature column ORDER must match the registry at predict time —
  XGBoost mis-attributes SHAP silently on column drift. The
  `validate_feature_frame` check is non-negotiable at the end of every
  `FeatureBuilder._assemble`.
- **H5/H6**: Every `prediction_log` row must carry `model_version`,
  `feature_engineering_version`, `calibrator_version`, `decision_threshold`.

### Code style

- Match the existing style in nearby files. No major refactors without
  explicit approval.
- No emojis in code, comments, or docstrings unless the user explicitly
  asks for them.
- Tests for new code go in the same `tests/` subdirectory that already
  holds tests for that layer.
- For sized changes (>200 LOC across multiple files), use the task tool
  to track sub-phases.

### Test discipline

- Unit tests in `tests/unit/` — no DB, use in-memory or sklearn data
- Integration tests in `tests/integration/` — require `RCM_INTEGRATION_DSN`
  env var; skipped otherwise; CLEAN UP after themselves (filename-prefix
  pattern)
- Performance benchmarks in `scripts/bench_*.py` — runnable standalone

### Remote DB constraints

The shared remote dev cluster (`104.130.220.20:30432`) has documented
constraints (see `CHANGELOG.md` CR-009):
- pgvector distance OPERATORS crash the backend (cluster-side bug; schema
  migrations + index creation work fine; RAG-time operations don't)
- `maintenance_work_mem = 64MB` (tight; build big indexes CONCURRENTLY)
- `max_wal_size = 400MB` (keep bulk inserts in ≤5k-row batches)
- Idle connection drops; engine config already uses `pool_pre_ping=True` +
  `pool_recycle=1800`

## Pointers

- DB schema: `src/rcm/models/` + `src/migrations/versions/`
- EDI parsing: `src/rcm/parsing/`
- Feature engineering: `src/rcm/features/`
- ML scaffold: `src/rcm/ml/`
- Tests: `tests/`
- Benchmarks: `scripts/bench_*.py`
- System change history: `CHANGELOG.md`  ← always read at session start
