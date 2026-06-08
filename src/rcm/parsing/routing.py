"""Variant + subtype detection.

Spec §2.1: variant = transaction set ID (837P/I/D/835), derived from GS08.
Spec §2.2: subtype = business flow (healthcare / home_care / dental /
therapy / transport / specialty / inpatient / hospice), derived from
variant + content (CPT, modifiers, revenue codes, provider taxonomy).

Variant is detected up front (before segments are dispatched) because it
selects the handler set. Subtype is derived AFTER the segment loop runs,
when the claim's lines and modifiers are populated.
"""

from __future__ import annotations

from rcm.parsing.context import ClaimRec, ParseContext


# -----------------------------------------------------------------------
# Variant — from GS08
# -----------------------------------------------------------------------

_GS08_TO_VARIANT: dict[str, str] = {
    "005010X222": "837P",
    "005010X223": "837I",
    "005010X224": "837D",
    "005010X221": "835",
    "005010X214": "277CA",
    "005010X231": "999",
}


def detect_variant(gs08: str | None) -> tuple[str, str]:
    """Return `(file_type, service_variant)`.

    `file_type` is the edi_files.file_type enum value ('edi_837'/'edi_835'/...).
    `service_variant` is the claims.service_variant string ('837P'/'837I'/
    '837D') or empty string for non-837 transactions.
    """
    if not gs08:
        return "edi_837", ""
    prefix = gs08[:10] if len(gs08) >= 10 else gs08
    variant = _GS08_TO_VARIANT.get(prefix, "")
    if variant == "835":
        return "edi_835", ""
    if variant.startswith("837"):
        return "edi_837", variant
    if variant == "277CA":
        return "edi_277", ""
    if variant == "999":
        return "edi_999", ""
    return "edi_837", ""


# -----------------------------------------------------------------------
# Subtype — content-driven
# -----------------------------------------------------------------------

_THERAPY_MODIFIERS = frozenset({"GP", "GO", "GN", "KH", "KX"})
_AMBULANCE_HCPCS_PREFIXES = ("A0",)
# Provider taxonomy prefixes (NUCC) commonly seen on DME / oncology / behavioral
_DME_TAXONOMY_PREFIXES = ("332B",)              # DME suppliers
_ONCOLOGY_TAXONOMY_PREFIXES = ("207RH", "208VP")  # hematology/oncology, gen surgical onc
_BEHAVIORAL_TAXONOMY_PREFIXES = ("103T", "104100", "171M", "2084P0800X", "2084P0805X")

# Revenue code buckets (837I)
def _rev_in(code: str | None, lo: int, hi: int) -> bool:
    if not code:
        return False
    try:
        n = int(code)
    except ValueError:
        return False
    return lo <= n <= hi


def derive_subtype(claim: ClaimRec) -> str:
    """Return the appropriate claim_subtype string for `claim`."""
    if claim.service_variant == "837D":
        return "dental"

    if claim.service_variant == "837I":
        revs = [line.revenue_code for line in claim.lines if line.revenue_code]
        # Home health 0551–0589
        if any(_rev_in(r, 551, 589) for r in revs):
            return "home_care"
        # Inpatient room/board 0100–0219
        if any(_rev_in(r, 100, 219) for r in revs):
            return "inpatient"
        # Hospice 0820–0859
        if any(_rev_in(r, 820, 859) for r in revs):
            return "hospice"
        return "institutional_other"

    # 837P branches
    primary_cpt = next((line.procedure_code for line in claim.lines if line.procedure_code), None)
    if primary_cpt and primary_cpt.startswith(_AMBULANCE_HCPCS_PREFIXES):
        return "transport"

    # Any line has a therapy discipline modifier
    for line in claim.lines:
        for mod in (line.modifier1, line.modifier2, line.modifier3, line.modifier4):
            if mod and mod.upper() in _THERAPY_MODIFIERS:
                return "therapy"

    # Provider taxonomy specialty checks (when present)
    tax = (claim.variant_data or {}).get("billing_provider_taxonomy") or ""
    if any(tax.startswith(p) for p in _DME_TAXONOMY_PREFIXES):
        return "specialty"
    if any(tax.startswith(p) for p in _ONCOLOGY_TAXONOMY_PREFIXES):
        return "specialty"
    if any(tax.startswith(p) for p in _BEHAVIORAL_TAXONOMY_PREFIXES):
        return "specialty"

    return "healthcare"


def finalize_subtypes(ctx: ParseContext) -> None:
    """Apply derive_subtype to every claim and update ctx state."""
    for claim in ctx.claims:
        claim.claim_subtype = derive_subtype(claim)
    # Record the file-level inferred subtype as the most common one
    if ctx.claims:
        counts: dict[str, int] = {}
        for c in ctx.claims:
            counts[c.claim_subtype] = counts.get(c.claim_subtype, 0) + 1
        ctx.claim_subtype = max(counts.items(), key=lambda kv: kv[1])[0]
