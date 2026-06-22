"""CR-092 Issue 3 — canonical CARC/RARC → denial-bucket mapping.

This module is the single source of truth for resolving a CARC or RARC
code to one of the 11 canonical denial buckets used by the explanation
layer. Every downstream consumer (analytics, monitoring, audits,
recommendation engine, the reason renderer's CARC/RARC complement) must
go through ``carc_bucket()`` / ``rarc_bucket()`` so all surfaces produce
identical bucketing for the same code.

Design:

  * The actual CARC/RARC dictionary lives in the ``code_masters`` PG
    table (CR-086 loaded it from the X12/WPC list). That table's
    ``category`` column carries a domain category (e.g.
    ``frequency_limits``, ``coverage_eligibility``, ``documentation``).
  * This module maps each ``code_masters.category`` value to the
    matching ``reason_renderer`` bucket slug (one of the 11 canonical
    explanation buckets). The mapping is intentionally small + auditable
    + checked-in — drift between the explanation buckets and the
    CARC/RARC categories would otherwise re-create the consistency
    problem CR-092 is trying to eliminate.

Public API:

    await carc_bucket(code) -> str        # one of the 11 reason buckets
    await rarc_bucket(code) -> str
    await codes_to_buckets(codes) -> set[str]  # batch helper
    invalidate_cache()                     # for tests + post-WPC-import

The cache is process-local and populated lazily at first call. It
survives the lifetime of the uvicorn worker; call ``invalidate_cache()``
after a quarterly WPC import to pick up new codes.
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg

from rcm.core.config import settings

logger = logging.getLogger(__name__)


# Canonical 11 explanation buckets — these MUST match
# ``rcm.ml.reason_renderer.REASON_BUCKETS`` slugs. Keeping the list inline
# here as a contract: if reason_renderer adds a new bucket we want this
# module to fail-fast (the test in test_cr092_denial_buckets verifies the
# invariant).
_VALID_BUCKETS: frozenset[str] = frozenset({
    "authorization", "coverage", "procedure", "diagnosis", "timely_filing",
    "documentation", "history", "provider", "billing", "similar", "general",
})

# Mapping from ``code_masters.category`` (the curated CARC/RARC domain
# category persisted in PG) to the 11-slug explanation-side bucket. Every
# value MUST be in ``_VALID_BUCKETS``.
#
# The decision rationale for each row is in the comment so future changes
# can be reviewed against the original mapping intent.
_CATEGORY_TO_BUCKET: dict[str, str] = {
    # Direct lexical matches
    "authorization":          "authorization",
    "timely_filing":          "timely_filing",
    "documentation":          "documentation",
    "provider":               "provider",
    "coverage_eligibility":   "coverage",
    "coordination_benefits":  "coverage",   # COB is a coverage routing issue
    "workers_comp_liability": "coverage",   # special coverage category
    "age_gender":             "coverage",   # eligibility-side restrictions
    # Coding-side
    "coding_modifier":        "procedure",  # modifier issues are procedural coding
    "equipment_dme":          "procedure",  # DME billing belongs to procedure scope
    "medical_necessity":      "diagnosis",  # dx-supports-cpt failure
    # Billing / financial
    "financial_adjustment":   "billing",    # contractual write-offs etc.
    "contract_rate":          "billing",    # fee-schedule mismatch
    "duplicate":              "billing",    # duplicate-claim is a billing artifact
    # History / frequency  — CRITICAL for CARC 119 alignment per CR-092
    # audit: this is the row that closes the "frequency-limit denial is
    # misattributed" gap on the CARC side.
    "frequency_limits":       "history",
    # Informational
    "alert_informational":    "general",
    "other":                  "general",
}

# Per-code overrides: takes precedence over the category lookup.
#
# Many codes have a curator-assigned category in code_masters that doesn't
# match the audit's explanation-side framing. The audit (CR-092) explicitly
# framed CARC 119 ("Benefit maximum reached / frequency limit exceeded")
# as a HISTORY-related denial — i.e. the model is expected to detect it
# via patient-history features. But code_masters.category labels CARC 119
# as ``coverage_eligibility`` because WPC categorizes it administratively.
# When the audit framing diverges from the WPC framing, the audit framing
# wins for explanation-bucket purposes (so prediction-side reason_renderer
# slugs and CARC-side resolution actually agree).
#
# Keep this dict TIGHT — only codes where the WPC category is materially
# wrong for explanation-vs-adjudication alignment. Everything else uses
# the category mapping.
_CODE_TO_BUCKET_OVERRIDE: dict[tuple[str, str], str] = {
    # CARC frequency / benefit-max — these are HISTORY denials in the
    # audit framing (model predicts via patient_history features).
    ("CARC", "119"): "history",   # Benefit maximum reached
    ("CARC", "151"): "history",   # Payer deems frequency unsupported
    # CARC 45 ("Charge exceeds fee schedule") is mis-categorized as
    # ``duplicate`` in code_masters; semantically it's a billing/contract
    # write-off, not a duplicate.
    ("CARC", "45"):  "billing",
    # CARC 234 ("Procedure not paid separately") — code_masters has it as
    # ``other``; it's a bundling/procedure issue, surface as procedure.
    ("CARC", "234"): "procedure",
    ("CARC", "97"):  "procedure",  # Payment included in another service (bundle)
    # CARC 16 / 17 (catch-all "lacks information") — code_masters labels
    # these as ``documentation`` which is reasonable but the model's
    # explanation-side bucket for these is 'general' (because the actual
    # field that's missing is signalled by the RARC, not the CARC itself).
    ("CARC", "16"):  "general",
    ("CARC", "17"):  "general",
    # RARCs whose category is ``documentation`` but specify a particular
    # missing field — bucket them to the missing field's natural bucket
    # so audits align with the prediction-side slug.
    ("RARC", "M76"): "diagnosis",   # Missing/invalid dx
    ("RARC", "M77"): "procedure",   # Missing/invalid POS
    ("RARC", "M78"): "procedure",   # Modifier invalid for procedure
    ("RARC", "M51"): "procedure",   # Missing procedure code
    ("RARC", "M119"):"procedure",   # Missing NDC (drug)
    ("RARC", "N56"): "diagnosis",   # Procedure not valid for services
    ("RARC", "N657"):"procedure",   # Should be billed with more specific code
    ("RARC", "N822"):"procedure",   # Missing modifier
    ("RARC", "N382"):"coverage",    # Missing member ID
    ("RARC", "MA27"):"coverage",    # Missing entitlement number
    ("RARC", "N4"):  "coverage",    # Missing prior payer EOB (COB)
    ("RARC", "M86"): "history",     # Payment already made for same procedure in time window
}


# Process-local cache: {(code_type, code) -> bucket_slug}. Lazy-loaded on
# first call to carc_bucket / rarc_bucket / codes_to_buckets. The
# code_masters table is small (~1.5k rows) so the entire dict fits in RAM
# with no memory pressure.
_CODE_TO_BUCKET: dict[tuple[str, str], str] | None = None
_CACHE_LOCK: asyncio.Lock = asyncio.Lock()


async def _load_cache() -> dict[tuple[str, str], str]:
    """Load the (code_type, code) -> bucket lookup from code_masters."""
    conn = await asyncpg.connect(dsn=settings.sync_database_url(), timeout=8)
    try:
        rows = await conn.fetch(
            "SELECT code_type, code, category FROM code_masters"
        )
    finally:
        await conn.close()
    out: dict[tuple[str, str], str] = {}
    seen_unmapped: set[str] = set()
    for r in rows:
        key = (r["code_type"], r["code"])
        # Per-code override wins over the category mapping.
        if key in _CODE_TO_BUCKET_OVERRIDE:
            out[key] = _CODE_TO_BUCKET_OVERRIDE[key]
            continue
        cat = (r["category"] or "other").strip().lower()
        bucket = _CATEGORY_TO_BUCKET.get(cat)
        if bucket is None:
            # Unknown category — fall back to general but log ONCE per
            # category so a quarterly WPC update doesn't silently introduce
            # an un-bucketed category.
            if cat not in seen_unmapped:
                seen_unmapped.add(cat)
                logger.warning(
                    "CR-092 denial_buckets: unknown code_masters category %r "
                    "for %s — bucketing as 'general'. Add a mapping in "
                    "_CATEGORY_TO_BUCKET if this category is now common.",
                    cat, r["code_type"],
                )
            bucket = "general"
        out[key] = bucket
    logger.info(
        "CR-092 denial_buckets cache loaded: %d codes across %d code_types",
        len(out), len({k[0] for k in out}),
    )
    return out


async def _ensure_cache() -> dict[tuple[str, str], str]:
    global _CODE_TO_BUCKET
    if _CODE_TO_BUCKET is not None:
        return _CODE_TO_BUCKET
    async with _CACHE_LOCK:
        if _CODE_TO_BUCKET is None:
            _CODE_TO_BUCKET = await _load_cache()
    return _CODE_TO_BUCKET


async def carc_bucket(code: str) -> str:
    """Return the canonical denial bucket for a CARC code.

    Unknown codes fall back to ``'general'``. Empty/None inputs also map
    to ``'general'`` so callers don't need defensive checks.
    """
    if not code:
        return "general"
    cache = await _ensure_cache()
    return cache.get(("CARC", str(code).strip()), "general")


async def rarc_bucket(code: str) -> str:
    """Return the canonical denial bucket for a RARC code."""
    if not code:
        return "general"
    cache = await _ensure_cache()
    return cache.get(("RARC", str(code).strip()), "general")


async def codes_to_buckets(
    carcs: list[str] | None = None,
    rarcs: list[str] | None = None,
) -> set[str]:
    """Batch helper — resolve a mixed CARC + RARC list to the deduplicated
    set of buckets they all belong to. Useful for prediction-vs-adjudication
    audits and analytics rollups.
    """
    cache = await _ensure_cache()
    buckets: set[str] = set()
    for c in carcs or []:
        if c:
            buckets.add(cache.get(("CARC", str(c).strip()), "general"))
    for r in rarcs or []:
        if r:
            buckets.add(cache.get(("RARC", str(r).strip()), "general"))
    return buckets


def invalidate_cache() -> None:
    """Drop the in-process cache. Call after a quarterly WPC import (when
    code_masters has been refreshed) or in tests that mutate code_masters."""
    global _CODE_TO_BUCKET
    _CODE_TO_BUCKET = None
