"""R0 — Refresh all materialized views CONCURRENTLY.

Phase 3 restoration step 0. Reports row counts before/after for every MV so
the architecture-impact log is self-documenting. CONCURRENTLY means read
traffic (none, but in principle) keeps working during the refresh.

Refresh order respects implicit dependencies:
    1. mv_claim_labels             — base label set, everything else needs it
    2. mv_payer_denial_rates       — payer × variant × subtype
    3. mv_payer_cpt_denial_rate    — payer × primary_cpt
    4. mv_payer_dx_denial_rate     — payer × primary_dx
    5. mv_payer_pos_denial_rate    — payer × place_of_service
    6. mv_cpt_dx_denial_rate       — clinical alignment
    7. mv_provider_denial_profiles — provider rollups
    8. mv_provider_payer_denial_rate
    9. mv_provider_cpt_denial_rate
   10. mv_lifecycle_outcomes       — correction history
   11. mv_patient_claim_history    — per-patient ordering
   12. mv_drift_baselines          — drift detection snapshot

Each REFRESH is async + CONCURRENTLY. CONCURRENTLY requires a unique index
on every MV (migration 0010 added these).

Usage:
    PYTHONPATH=src python scripts/restoration_r0_refresh_mvs.py
"""

from __future__ import annotations

import asyncio
import time

import asyncpg


_REFRESH_ORDER = [
    "mv_claim_labels",
    "mv_payer_denial_rates",
    "mv_payer_cpt_denial_rate",
    "mv_payer_dx_denial_rate",
    "mv_payer_pos_denial_rate",
    "mv_cpt_dx_denial_rate",
    "mv_provider_denial_profiles",
    "mv_provider_payer_denial_rate",
    "mv_provider_cpt_denial_rate",
    "mv_lifecycle_outcomes",
    "mv_patient_claim_history",
    "mv_drift_baselines",
]


async def _row_count(c: asyncpg.Connection, name: str) -> int:
    try:
        return int(await c.fetchval(f"SELECT count(*) FROM {name}"))
    except Exception:
        return -1


async def main() -> None:
    dsn = ("postgresql://postgres:P0stgreSQL%21Dev%23847@104.130.220.20:30432/"
           "rcm_denials")
    c = await asyncpg.connect(dsn=dsn, timeout=15)

    print(f'{"mv":<35} {"before":>10} {"after":>10} {"delta":>10} {"secs":>6}')
    print('-' * 75)
    grand_before = grand_after = grand_secs = 0
    failures = []

    for mv in _REFRESH_ORDER:
        before = await _row_count(c, mv)
        t0 = time.perf_counter()
        try:
            await c.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv}")
            err = None
        except Exception as exc:
            # CONCURRENTLY needs a unique index on the MV. If it's missing
            # (or the MV has never been populated with WITH DATA), fall back
            # to a plain refresh.
            try:
                await c.execute(f"REFRESH MATERIALIZED VIEW {mv}")
                err = None
            except Exception as exc2:
                err = f"{type(exc2).__name__}: {exc2}"
        secs = time.perf_counter() - t0
        after = await _row_count(c, mv)
        delta = after - before if before >= 0 and after >= 0 else after

        grand_before += max(before, 0)
        grand_after  += max(after, 0)
        grand_secs   += secs
        if err:
            failures.append((mv, err))

        marker = ' ✓' if err is None else ' ✗'
        print(f'{mv:<35} {before:>10} {after:>10} {delta:>+10} {secs:>5.1f}s{marker}')
        if err:
            print(f'  ↳ {err}')

    print('-' * 75)
    print(f'{"TOTAL":<35} {grand_before:>10} {grand_after:>10} '
          f'{grand_after - grand_before:>+10} {grand_secs:>5.1f}s')
    if failures:
        print(f'\n{len(failures)} MV(s) failed to refresh:')
        for mv, err in failures:
            print(f'  {mv}: {err}')
    await c.close()


if __name__ == "__main__":
    asyncio.run(main())
