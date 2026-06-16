# R1 — Per-Variant Field Expectations

**Status**: documentation only. Not parsed by application code. Lives here to
explain *why* the R1 verification script (`scripts/r1_verify_dataset.py`)
applies different null-tolerance values per variant.

This file exists because of CR-061's R1 verification-script refinement. The
script's tolerance map is the single source of truth at execution time; this
document explains the reasoning behind each entry so a future reviewer
doesn't have to reverse-engineer it.

---

## Why variants have different field expectations

The X12 EDI 837 standard ships in three flavours that look similar but
encode three completely different clinical/billing models:

| Variant | Standard | Domain | Implementation Guide |
|---|---|---|---|
| 837P | Professional | Office / clinic visits, telehealth, lab, imaging | TR3 005010X222A1 |
| 837I | Institutional | Inpatient, outpatient hospital, home health, hospice | TR3 005010X223A2 |
| 837D | Dental | Restorative, preventive, orthodontic, oral surgery | TR3 005010X224A2 |

Each TR3 mandates a different required-field set. A field that is *required*
on 837P (e.g., place_of_service via CMS-1500 box 24B) is *prohibited or
absent* on 837I (UB-04 form has no POS column — uses facility type code +
revenue codes instead). The same applies to dental: it uses tooth/surface
identifiers instead of POS, and ICD diagnoses are optional rather than
required.

The verification script's tolerance map must mirror this reality. A single
global tolerance ("primary_pos must be < 5% null") would incorrectly flag
837I and 837D as broken when they're following their own TR3 correctly.

---

## Per-field reasoning

### `primary_dx` (primary diagnosis code, ICD-10-CM)

| Variant | Tolerance | Why |
|---|---|---|
| 837D / dental | **80% null OK** | CDT (dental) claims often bill without an ICD diagnosis — dental procedures are identified by CDT code + tooth number + surface, not by an underlying medical diagnosis. The diagnosis is optional in CMS' dental processing. Sparse `primary_dx` is **expected**, not a defect. |
| 837P / healthcare | **5% null** (strict) | Required by CMS-1500. Should be present on every claim. |
| 837I / home_care | **5% null** (strict) | Required by UB-04 Form Locator 67 (Principal Diagnosis). Should be present. |

### `primary_pos` (Place of Service — X12 code from `claim_lines.place_of_service`)

| Variant | Tolerance | Why |
|---|---|---|
| 837D / dental | **100% null OK** | Dental claims don't populate `place_of_service` at the line level. The "place" semantic is captured implicitly (dental office is the default). Loading the column for 837D returns NULL for every row by design. |
| 837P / healthcare | **5% null** (strict) | Required. CMS-1500 box 24B mandates POS for every service line. |
| 837I / home_care | **100% null OK** | Institutional claims use `facility_type_code` (FL4 type-of-bill) instead of POS. The 837I parser doesn't populate `claim_lines.place_of_service`. |

### `home_care_episode` (variant-specific child table)

| Variant | Tolerance | Why |
|---|---|---|
| 837D / dental | **100% null OK** | Home care is a 837I/home_care concept. Dental never has this. |
| 837P / healthcare | **100% null OK** | Home care is a 837I/home_care concept. Professional never has this. |
| 837I / home_care | **5% null** (strict) | This IS the home-care variant. The episode (start/end dates, OASIS assessment date, visit count, LUPA flag) must be present. |

The `_load_children()` function in `dataset.py:260` queries
`home_care_episodes` for every claim regardless of variant. For non-home-care
variants the JOIN returns nothing, and `episodes_by_claim.get(cid)` resolves
to `None`. That's how the DataFrame ends up with `home_care_episode=None`
for ~100% of 837D and 837P rows. Correct by design.

### `transport_cert` (variant-specific child table)

| Variant | Tolerance | Why |
|---|---|---|
| All variants currently loaded | **100% null OK** | The `transport` subtype lives under 837P (837P/transport) but the current corpus has zero rows in that subtype. Future training data may populate it. Until then `transport_cert` is expected to be empty across all loaded variants. |

When a 837P/transport corpus appears, this tolerance flips to strict (5%
null) for that one variant. The other three variants stay at 100% null OK.

### `payer_canonical_name`

| Variant | Tolerance | Why |
|---|---|---|
| All variants | **50% null tolerated** | Payer resolution depends on the parser matching SBR/N1*PR segments to canonical payer records. Some claims legitimately have unresolved payers (e.g., SBR-only claims with cryptic payer IDs). CR-060 handles unresolved payers gracefully in the prediction path. |

### Other fields (claim_id, claim_number, service_variant, claim_subtype, denied, total_charge_amount, service_from_date)

| Field | Tolerance | Why |
|---|---|---|
| `claim_id`, `claim_number`, `service_variant`, `claim_subtype`, `denied`, `service_from_date` | **0% null** (strict on every variant) | These are NOT NULL in the `claims` table and in `mv_claim_labels`. Any null indicates a data-corruption defect, not a variant idiom. |
| `total_charge_amount` | **5% null** (slight slack) | CLM02 is required in all 837 flavours but the parser may occasionally fall back to NULL on malformed CLM segments. Slack accommodates parse imperfection without masking real issues. |

---

## What this document is NOT

- ❌ Configuration consumed by code. The tolerance values live in
  `scripts/r1_verify_dataset.py` as Python constants — this document
  explains them but cannot drive them.
- ❌ A schema. Application code must never `open()` or `yaml.load()` this
  file.
- ❌ A registry. There is no DB table mirroring this document.
- ❌ A source of truth for *which fields exist*. That is the SQL
  in `dataset.py` and the `_empty_corpus()` column list.

## When to update

- A new variant is added (e.g., 837P/specialty becomes its own training
  cohort): add a section above describing its field expectations and update
  the script's tolerance map accordingly.
- An existing variant gains data for a previously-empty field (e.g., the
  first 837P/transport corpus arrives): update the `transport_cert`
  section and the script's tolerance.
- An expected-strict field drifts: that's a data-quality regression worth a
  separate AIR — don't loosen the tolerance to hide it.

## Why this lives in `docs/` and not in `data/` or as a config file

Per the storage-minimization principles enshrined in CR-061, verification
metadata MUST NOT be persisted in the database, parsed from a config file,
or treated as runtime state. The tolerance map is a code-local constant;
this document is a code-adjacent narrative. Both die when the verification
script is retired — exactly the right lifetime.
