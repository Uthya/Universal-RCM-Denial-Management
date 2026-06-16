"""CR-070 — one-shot backfill of orphan CLPs from raw_segments.

When an 835 arrived before its 837 original (the CR-068A scenario), the
remit was dropped at ingest time with a parse_event:
    {event_type='validator_warning', details:{reason:'orphan_clp_unmatched',
     claim_number:X}}.
The raw CLP text was still preserved in `raw_segments`. Now that the
matching 837 claim exists (CR-068A recovery), we can reconstruct the
remit row by re-reading the CLP segment plus any CAS / LQ segments that
followed it.

This script is IDEMPOTENT: if a remit already exists on (claim_id,
edi_file_id) it is skipped (which is the natural unique-shape for an
835's CLP).

No schema change. No new tables, MVs, indexes, or registries. Writes
only into existing remittance_claims / adjustments / remark_codes tables
via the same column shape as `_bulk_insert_remittances`.

Outputs scripts/backfill_orphan_clps_post.json with every inserted id
for rollback.
"""
from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import asyncpg

LOCAL_DSN = "postgresql://rcm:rcm_dev_password@localhost:5433/rcm_denials_dev"


def _decimal_or(value: str | None, default: Decimal | None = None) -> Decimal | None:
    if value is None or value == "":
        return default
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return default


def _parse_clp(raw: str) -> dict:
    """X12 835 CLP segment: CLP*claim_number*status*billed*paid*patient_resp*
    filing_indicator*payer_ctrl*facility_code*frequency_code"""
    f = raw.split("*")
    # Pad to 11 fields so indexing is safe regardless of trailing-element-omission.
    f = f + [""] * (11 - len(f)) if len(f) < 11 else f
    return {
        "claim_number":                  f[1],
        "claim_status_code":             f[2],
        "billed_amount":                 _decimal_or(f[3], Decimal("0")),
        "paid_amount":                   _decimal_or(f[4], Decimal("0")),
        "patient_responsibility_amount": _decimal_or(f[5]),
        # f[6] = claim_filing_indicator (not stored)
        "payer_claim_control_number":   (f[7] or None) if f[7] else None,
        # f[8], f[9] not stored
    }


def _parse_cas_triples(raw: str) -> list[dict]:
    """CAS*group_code*reason1*amount1*qty1*reason2*amount2*qty2*..."""
    f = raw.split("*")
    if len(f) < 4:
        return []
    group_code = f[1]
    out: list[dict] = []
    # Triples start at index 2, in groups of 3 (reason, amount, quantity)
    i = 2
    while i < len(f):
        reason_code = f[i] if i < len(f) else ""
        amount      = _decimal_or(f[i + 1], Decimal("0")) if i + 1 < len(f) else Decimal("0")
        quantity    = _decimal_or(f[i + 2]) if i + 2 < len(f) else None
        if reason_code:
            out.append({
                "adjustment_group_code":  group_code,
                "adjustment_reason_code": reason_code,
                "adjustment_amount":      amount,
                "quantity":               quantity,
            })
        i += 3
    return out


def _parse_lq(raw: str) -> str | None:
    """LQ*HE*<remark_code>  or  LQ*RX*<remark_code>"""
    f = raw.split("*")
    if len(f) < 3:
        return None
    return f[2] or None


async def fetch_recoverable_events(c: asyncpg.Connection) -> list[asyncpg.Record]:
    return await c.fetch("""
        SELECT pe.id AS event_id,
               pe.edi_file_id,
               pe.details->>'claim_number' AS claim_number
          FROM parse_events pe
          WHERE pe.event_type='validator_warning'
            AND pe.details->>'reason'='orphan_clp_unmatched'
            AND EXISTS (
              SELECT 1 FROM claims c
              WHERE c.claim_number = pe.details->>'claim_number'
                AND c.deleted_at IS NULL
            )
          ORDER BY pe.edi_file_id, pe.id
    """)


async def resolve_freq1_claim_id(c: asyncpg.Connection, claim_number: str) -> int | None:
    """Prefer the freq=1 / NULL ORIGINAL over freq=7 / freq=2 / freq=3."""
    return await c.fetchval("""
        SELECT id FROM claims
        WHERE claim_number=$1 AND deleted_at IS NULL
        ORDER BY
          CASE
            WHEN frequency_code IS NULL OR frequency_code='1' THEN 0
            ELSE 1
          END,
          id
        LIMIT 1
    """, claim_number)


async def find_clp_segment(c: asyncpg.Connection, edi_file_id: int, claim_number: str
                          ) -> asyncpg.Record | None:
    return await c.fetchrow("""
        SELECT segment_position, raw_segment_text
        FROM raw_segments
        WHERE edi_file_id=$1
          AND segment_name='CLP'
          AND split_part(raw_segment_text, '*', 2) = $2
        LIMIT 1
    """, edi_file_id, claim_number)


async def find_following_segments(c: asyncpg.Connection, edi_file_id: int, clp_pos: int
                                 ) -> list[asyncpg.Record]:
    """All segments strictly after the CLP and strictly before the next
    CLP/LE/SE segment for the same edi_file_id."""
    next_boundary = await c.fetchval("""
        SELECT MIN(segment_position) FROM raw_segments
        WHERE edi_file_id=$1
          AND segment_position > $2
          AND segment_name IN ('CLP', 'LE', 'SE')
    """, edi_file_id, clp_pos)
    if next_boundary is None:
        next_boundary = 2 ** 31 - 1
    return await c.fetch("""
        SELECT segment_name, segment_position, raw_segment_text
        FROM raw_segments
        WHERE edi_file_id=$1
          AND segment_position > $2
          AND segment_position < $3
          AND segment_name IN ('CAS', 'LQ')
        ORDER BY segment_position
    """, edi_file_id, clp_pos, next_boundary)


async def file_remittance_date(c: asyncpg.Connection, edi_file_id: int):
    """Reuse an existing remit's remittance_date for this file if one exists.
    Otherwise None (P1: NULL is fine — the field is nullable on purpose)."""
    return await c.fetchval("""
        SELECT remittance_date FROM remittance_claims
        WHERE edi_file_id=$1 AND remittance_date IS NOT NULL
        LIMIT 1
    """, edi_file_id)


async def backfill_one(c: asyncpg.Connection, event: asyncpg.Record) -> dict:
    edi_file_id  = int(event["edi_file_id"])
    claim_number = event["claim_number"]

    # Resolve claim_id (prefer freq=1)
    claim_id = await resolve_freq1_claim_id(c, claim_number)
    if claim_id is None:
        return {"status": "no_claim_found", "claim_number": claim_number,
                "edi_file_id": edi_file_id}

    # Skip if remit already exists for (claim_id, edi_file_id) — idempotency guard
    already = await c.fetchval("""
        SELECT id FROM remittance_claims
        WHERE claim_id=$1 AND edi_file_id=$2
        LIMIT 1
    """, claim_id, edi_file_id)
    if already is not None:
        return {"status": "already_present", "claim_number": claim_number,
                "edi_file_id": edi_file_id,
                "existing_remit_id": int(already)}

    # Find the CLP
    clp = await find_clp_segment(c, edi_file_id, claim_number)
    if clp is None:
        return {"status": "no_clp_in_raw_segments", "claim_number": claim_number,
                "edi_file_id": edi_file_id}
    clp_fields = _parse_clp(clp["raw_segment_text"])
    clp_pos    = int(clp["segment_position"])

    # Find CAS + LQ following this CLP up to the next CLP/LE/SE
    follow = await find_following_segments(c, edi_file_id, clp_pos)
    cas_segs = [s for s in follow if s["segment_name"] == "CAS"]
    lq_segs  = [s for s in follow if s["segment_name"] == "LQ"]

    # Try to inherit the file's remittance_date from an existing remit
    rem_date = await file_remittance_date(c, edi_file_id)

    async with c.transaction():
        # Insert the remit
        new_remit_id = await c.fetchval("""
            INSERT INTO remittance_claims (
                claim_id, edi_file_id, claim_status_code,
                billed_amount, paid_amount, patient_responsibility_amount,
                payer_claim_control_number, remittance_date, raw_clp_segment
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING id
        """, claim_id, edi_file_id, clp_fields["claim_status_code"],
            clp_fields["billed_amount"], clp_fields["paid_amount"],
            clp_fields["patient_responsibility_amount"],
            clp_fields["payer_claim_control_number"],
            rem_date, clp["raw_segment_text"])

        # Insert adjustments
        adj_ids: list[int] = []
        for cas in cas_segs:
            triples = _parse_cas_triples(cas["raw_segment_text"])
            for t in triples:
                adj_id = await c.fetchval("""
                    INSERT INTO adjustments (
                        remittance_claim_id, adjustment_group_code,
                        adjustment_reason_code, adjustment_amount, quantity,
                        raw_cas_segment
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id
                """, new_remit_id, t["adjustment_group_code"],
                    t["adjustment_reason_code"], t["adjustment_amount"],
                    t["quantity"], cas["raw_segment_text"])
                adj_ids.append(int(adj_id))

        # Insert remark_codes
        rem_ids: list[int] = []
        for lq in lq_segs:
            rcode = _parse_lq(lq["raw_segment_text"])
            if not rcode:
                continue
            rid = await c.fetchval("""
                INSERT INTO remark_codes (
                    remittance_claim_id, remark_code, raw_lq_segment
                ) VALUES ($1, $2, $3)
                RETURNING id
            """, new_remit_id, rcode, lq["raw_segment_text"])
            rem_ids.append(int(rid))

    return {
        "status":            "ok",
        "claim_number":      claim_number,
        "edi_file_id":       edi_file_id,
        "claim_id":          int(claim_id),
        "new_remit_id":      int(new_remit_id),
        "new_adjustment_ids": adj_ids,
        "new_remark_code_ids": rem_ids,
        "clp02":             clp_fields["claim_status_code"],
        "billed_amount":     str(clp_fields["billed_amount"]),
        "paid_amount":       str(clp_fields["paid_amount"]),
    }


async def main() -> None:
    c = await asyncpg.connect(dsn=LOCAL_DSN)
    try:
        events = await fetch_recoverable_events(c)
        print(f"Recoverable orphan_clp events: {len(events)}")
        print()

        t_start = time.monotonic()
        results: list[dict] = []
        for i, ev in enumerate(events, 1):
            res = await backfill_one(c, ev)
            results.append(res)
            tag = {"ok": "OK", "already_present": "SKP",
                   "no_claim_found": "NCF", "no_clp_in_raw_segments": "NCS"
                  }.get(res["status"], "ERR")
            print(f"  [{i:>3}/{len(events)}] {tag}  {res['claim_number']:<12} "
                  f"edi_file={res['edi_file_id']:<4}  "
                  f"status={res.get('clp02','-')}  "
                  f"new_remit_id={res.get('new_remit_id','-')}  "
                  f"adj={len(res.get('new_adjustment_ids', []))}  "
                  f"rem={len(res.get('new_remark_code_ids', []))}")
        elapsed = round(time.monotonic() - t_start, 2)

        n_ok    = sum(1 for r in results if r["status"] == "ok")
        n_skip  = sum(1 for r in results if r["status"] == "already_present")
        n_err   = sum(1 for r in results if r["status"] not in ("ok", "already_present"))
        total_adj = sum(len(r.get("new_adjustment_ids",   [])) for r in results)
        total_rem = sum(len(r.get("new_remark_code_ids", [])) for r in results)

        print()
        print("=" * 72)
        print("BACKFILL SUMMARY")
        print("=" * 72)
        print(f"  events attempted    : {len(events)}")
        print(f"  remits inserted     : {n_ok}")
        print(f"  already present     : {n_skip}")
        print(f"  errors / unmatched  : {n_err}")
        print(f"  total adjustments   : {total_adj}")
        print(f"  total remarks       : {total_rem}")
        print(f"  wall-clock          : {elapsed}s")

        Path("scripts/backfill_orphan_clps_post.json").write_text(
            json.dumps({
                "events_attempted":  len(events),
                "remits_inserted":   n_ok,
                "already_present":   n_skip,
                "unmatched":         n_err,
                "total_adjustments": total_adj,
                "total_remarks":     total_rem,
                "elapsed_s":         elapsed,
                "results":           results,
            }, indent=2, default=str),
            encoding="utf-8",
        )
        print("\n  wrote scripts/backfill_orphan_clps_post.json")
    finally:
        await c.close()


if __name__ == "__main__":
    asyncio.run(main())
