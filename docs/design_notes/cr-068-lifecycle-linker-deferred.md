# CR-068 — ClaimLifecycle producer (DEFERRED)

**Status**: Design notes only. Not implemented. Deferred per consumer-first analysis.
**Date drafted**: 2026-06-12
**Decision**: defer until a downstream consumer is approved.

## Why deferred

The producer was drafted and analysed, but landed against the "no DB object
without verified consumer" principle (principle B in
`feedback_storage_minimization`). At the time of drafting, `claim_lifecycles`
had zero runtime readers anywhere in `src/` — neither FeatureBuilder, nor the
trainer, nor any router, nor any MV consumed by training. The PL/pgSQL helper
`claim_lifecycle_resolved()` exists but has no Python caller. The
`mv_lifecycle_outcomes` MV exists but is read by no SQL in `src/`. The
`correction_examples` table has the FK declared but is empty with no writer.

Populating ~11,264 lifecycle rows with no consumer = persisting data ahead of
need. The decision is to wait until the **first concrete consumer** is being
implemented and bundle the producer into that consumer's AIR. Likely first
consumer: a `mv_claim_labels` propagation change that lifts the trainable
denial rate from 0.04% to ~26%, which would have direct, observable model
impact.

## Archived design — linkage algorithm

```
For each replacement r in claims WHERE frequency_code='7' AND deleted_at IS NULL:
  key = (r.claim_number, r.payer_id)
  candidates = freq=1/NULL claims with that same (claim_number, payer_id)

  if len(candidates) == 0:  -> ORPHAN, no row written
  elif len(candidates) == 1: -> SINGLE_MATCH, insert claim_lifecycles row
  else:                      -> AMBIGUOUS, no row written; log for review
```

Row shape for SINGLE_MATCH:
- `original_claim_id` = candidate id
- `parent_claim_id`   = candidate id (no chains in this dataset)
- `child_claim_id`    = replacement id
- `relationship_type` = `'replacement'`
- `iteration_number`  = `1`
- `days_to_resolution` = `r.service_from_date - orig.service_from_date` when
  both non-null and non-negative; otherwise `NULL`
- `diff_summary` = `NULL`

Disambiguation policy chosen: **skip ambiguous**. Time-order disambiguation is
unreliable on this dataset (1,130 replacements have a service date that
precedes their candidate original). Lowest-id is arbitrary. Skipping keeps
semantics honest and the 0.82% loss is well below noise floor.

## Archived coverage estimate (against the 56k local corpus)

| Bucket | Replacements | % | Lifecycle row written? |
|---|---|---|---|
| SINGLE_MATCH | 11,264 | 84.9% | YES |
| AMBIGUOUS    | 109    | 0.8%  | NO — logged |
| ORPHAN       | 1,897  | 14.3% | NO — logged |
| **Total**    | **13,270** | | ~11,264 rows |

Per-variant SINGLE_MATCH coverage:

| Variant | Subtype | Replacements | Single-match | Coverage |
|---|---|---|---|---|
| 837P | healthcare | ~11,500 | ~10,500 | ~91% |
| 837D | dental     | ~890    | ~550    | ~62% |
| 837I | home_care  | ~880    | ~260    | ~30% |
| 837I | inst_other | ~5      | ~2      | — |

Dental and home_care coverage is depressed by the **22 failed LR1K
`*_original_837.dat` uploads** during bulk-load phase 1 (HTTP 500 batch).
Diagnosing and re-uploading those is being pursued as **CR-068A**, which
reduces the orphan population organically without writing a single
`claim_lifecycles` row.

## Archived orphan analysis

1,897 replacements have no freq=1 sibling sharing `(claim_number, payer_id)`.
Trace of representative orphan `D0553` confirms no original exists in DB. The
files responsible are the LR1K_D and LR1K_I `*_original_837.dat` files that
returned HTTP 500 during bulk upload. Each file carries ~85 claims; 22 ×
~85 ≈ 1,870, accounting for the bulk of the 1,897 (the remaining ~27 are
attributable to other minor data quality issues to be confirmed by CR-068A).

## Archived ambiguity analysis

| `(claim_number, payer_id)` key collisions | Keys | Rows |
|---|---|---|
| 1 (unique)        | 53,119 | 53,119 |
| 2 duplicates      | 110    | 220   |
| 3–5 duplicates    | 47     | 141   |

Of the 110+47 = 157 ambiguous keys, 109 replacements map back to them. If a
future consumer's contract needs to resolve these (e.g. a multi-parent
lifecycle row, or a richer linkage key), this is the population to triage.

## Alternative linkage strategy considered and rejected

Strategy B (replacement.`previous_payer_claim_control_no` ≡
original.`remittance_claims.payer_claim_control_number`) recovered only
18/13,270 = 0.1% on this dataset. The synthetic generator only reuses the
original's CLP07 in the 18 Pattern-A cases where the original itself is
denied; for the bulk (paid-origin → denied-replacement) chains the
`previous_payer_claim_control_no` value is independently generated and does
not match any original's CLP07. Strategy A (`claim_number` + `payer_id`) is
the right contract for this dataset.

For a future production-data scenario where claim_number collisions are more
frequent, Strategy B may regain importance — that is a contract decision for
the consumer's AIR, not the producer's.

## When to revive this design

Revive when one of the following has a concrete, approved AIR:
- `mv_claim_labels` propagation (most likely first consumer)
- RAG correction-example seeding from lifecycle
- Appeals workflow that walks ancestry
- Per-variant lifecycle features that FeatureBuilder reads

At revive time, the linker code is a sub-section of *that* consumer's AIR.
The contract may be revised; this document is the starting point, not the
final answer.

## What this document is NOT

- Not a CHANGELOG entry (no code changed).
- Not authorisation to run a linker.
- Not a contract — the linkage key is subject to revision by the consumer
  AIR that ultimately depends on it.
