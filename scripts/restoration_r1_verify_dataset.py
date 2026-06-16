"""R1 — Verify load_training_corpus returns real data per variant.

Read-only. No writes. Calls features.dataset.load_training_corpus for every
(variant, subtype) the FE registry knows about. Reports row count + key
column completeness (denied label, payer, primary_cpt, primary_dx). The
output is the evidence file for R1 in the restoration log.

Usage:
    PYTHONPATH=src DATABASE_URL=... JWT_SECRET_KEY=... \
        python scripts/restoration_r1_verify_dataset.py
"""

from __future__ import annotations

import asyncio

from rcm.core.database import async_session
from rcm.features.dataset import load_training_corpus


# (service_variant, claim_subtype) keys per spec §2.2 + the global fallback
_VARIANTS = [
    ("837P", "healthcare"),
    ("837P", "therapy"),
    ("837P", "transport"),
    ("837P", "specialty"),
    ("837I", "home_care"),
    ("837I", "institutional_other"),
    ("837I", "inpatient"),
    ("837I", "hospice"),
    ("837I", "specialty"),
    ("837D", "dental"),
    (None,    None),                 # global slice — no filter at all
]


async def main() -> None:
    print(f'{"variant":<8} {"subtype":<22} '
          f'{"rows":>6} {"denied":>7} {"paid":>6} '
          f'{"w/payer":>8} {"w/cpt":>6} {"w/dx":>5}')
    print('-' * 80)

    async with async_session() as session:
        for variant, subtype in _VARIANTS:
            label = f"{variant or '-':<8} {subtype or 'GLOBAL':<22}"
            try:
                df = await load_training_corpus(
                    session, service_variant=variant, claim_subtype=subtype,
                )
            except Exception as exc:
                print(f'{label} ERROR  {type(exc).__name__}: {exc}')
                continue

            n = len(df)
            if n == 0:
                print(f'{label} {n:>6}  (empty)')
                continue

            denied = int(df["denied"].sum())
            paid   = n - denied
            w_pay  = int(df["payer_canonical_name"].notna().sum())
            w_cpt  = int(df["primary_cpt"].notna().sum())
            w_dx   = int(df["primary_dx"].notna().sum())
            print(f'{label} {n:>6} {denied:>7} {paid:>6} '
                  f'{w_pay:>8} {w_cpt:>6} {w_dx:>5}')


if __name__ == "__main__":
    asyncio.run(main())
