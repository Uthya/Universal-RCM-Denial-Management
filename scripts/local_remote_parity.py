"""Local-vs-Remote schema parity report.

Compares structural shape (tables, MVs, indexes, constraints, extensions,
alembic head) between the freshly-migrated local docker PG (:5433) and the
remote dev PG (:30432). Schema only — no data comparison.
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any

import asyncpg

LOCAL = "postgresql://rcm:rcm_dev_password@localhost:5433/rcm_denials_dev"
REMOTE = "postgresql://postgres:P0stgreSQL%21Dev%23847@104.130.220.20:30432/rcm_denials"


async def snapshot(dsn: str, label: str) -> dict:
    c = await asyncpg.connect(dsn=dsn, timeout=15)
    out: dict[str, Any] = {"label": label}

    out["pg_version"] = (await c.fetchval("SELECT version()"))[:60]
    out["alembic_head"] = await c.fetchval("SELECT version_num FROM alembic_version")

    out["extensions"] = sorted([dict(r) for r in await c.fetch(
        "SELECT extname AS name, extversion AS version FROM pg_extension WHERE extname <> 'plpgsql' ORDER BY extname"
    )], key=lambda r: r["name"])

    out["tables"] = sorted([r["table_name"] for r in await c.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
    )])
    out["partitioned_tables"] = sorted([r["relname"] for r in await c.fetch(
        "SELECT c.relname FROM pg_class c WHERE c.relkind='p' AND c.relnamespace='public'::regnamespace ORDER BY c.relname"
    )])

    out["matviews"] = sorted([r["matviewname"] for r in await c.fetch(
        "SELECT matviewname FROM pg_matviews WHERE schemaname='public' ORDER BY matviewname"
    )])

    out["indexes"] = sorted([r["indexname"] for r in await c.fetch(
        "SELECT indexname FROM pg_indexes WHERE schemaname='public' ORDER BY indexname"
    )])

    out["constraints_count_by_type"] = {
        r["constraint_type"]: r["count"]
        for r in await c.fetch(
            "SELECT constraint_type, count(*) FROM information_schema.table_constraints "
            "WHERE table_schema='public' GROUP BY constraint_type ORDER BY constraint_type"
        )
    }

    # Per-table column count (signature for column-level parity)
    rows = await c.fetch(
        "SELECT table_name, count(*) AS n FROM information_schema.columns "
        "WHERE table_schema='public' GROUP BY table_name ORDER BY table_name"
    )
    out["per_table_col_count"] = {r["table_name"]: r["n"] for r in rows}

    # Functions
    out["functions"] = sorted([r["proname"] for r in await c.fetch(
        "SELECT proname FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid "
        "WHERE n.nspname='public' ORDER BY proname"
    )])

    # Enum types
    out["enum_types"] = sorted([r["typname"] for r in await c.fetch(
        "SELECT typname FROM pg_type WHERE typtype='e' "
        "AND typnamespace='public'::regnamespace ORDER BY typname"
    )])

    await c.close()
    return out


def _diff_list(a: list, b: list) -> dict:
    sa, sb = set(a), set(b)
    return {
        "only_local": sorted(sa - sb),
        "only_remote": sorted(sb - sa),
        "both": len(sa & sb),
    }


def _compare(local: dict, remote: dict) -> dict:
    out: dict[str, Any] = {}
    out["pg_version"] = {"local": local["pg_version"], "remote": remote["pg_version"]}
    out["alembic_head"] = {
        "local": local["alembic_head"],
        "remote": remote["alembic_head"],
        "match": local["alembic_head"] == remote["alembic_head"],
    }
    out["extensions"] = {
        "local": local["extensions"],
        "remote": remote["extensions"],
        "diff": _diff_list(
            [e["name"] for e in local["extensions"]],
            [e["name"] for e in remote["extensions"]],
        ),
    }
    out["tables"] = {
        "local_count": len(local["tables"]),
        "remote_count": len(remote["tables"]),
        "diff": _diff_list(local["tables"], remote["tables"]),
    }
    out["partitioned_tables"] = {
        "local": local["partitioned_tables"],
        "remote": remote["partitioned_tables"],
        "diff": _diff_list(local["partitioned_tables"], remote["partitioned_tables"]),
    }
    out["matviews"] = {
        "local_count": len(local["matviews"]),
        "remote_count": len(remote["matviews"]),
        "diff": _diff_list(local["matviews"], remote["matviews"]),
    }
    out["indexes"] = {
        "local_count": len(local["indexes"]),
        "remote_count": len(remote["indexes"]),
        "diff": _diff_list(local["indexes"], remote["indexes"]),
    }
    out["constraints"] = {
        "local": local["constraints_count_by_type"],
        "remote": remote["constraints_count_by_type"],
    }
    out["functions"] = {
        "local_count": len(local["functions"]),
        "remote_count": len(remote["functions"]),
        "diff": _diff_list(local["functions"], remote["functions"]),
    }
    out["enum_types"] = {
        "local": local["enum_types"],
        "remote": remote["enum_types"],
        "diff": _diff_list(local["enum_types"], remote["enum_types"]),
    }
    # Column-count drift per table (only tables that exist in both)
    common_tables = set(local["tables"]) & set(remote["tables"])
    col_diffs = {}
    for t in sorted(common_tables):
        lc = local["per_table_col_count"].get(t, 0)
        rc = remote["per_table_col_count"].get(t, 0)
        if lc != rc:
            col_diffs[t] = {"local": lc, "remote": rc, "delta": lc - rc}
    out["per_table_column_count_drift"] = col_diffs
    return out


async def main() -> None:
    print("Snapshotting LOCAL...")
    local = await snapshot(LOCAL, "local")
    print("Snapshotting REMOTE...")
    remote = await snapshot(REMOTE, "remote")
    cmp = _compare(local, remote)

    print()
    print("=" * 78)
    print("SCHEMA PARITY REPORT")
    print("=" * 78)

    def _fmt(b): return "MATCH" if b else "MISMATCH"

    # Alembic
    a = cmp["alembic_head"]
    print(f"\n  Alembic head        local={a['local']}  remote={a['remote']}  [{_fmt(a['match'])}]")

    # PG version
    print(f"  PG version (local)  {cmp['pg_version']['local']}")
    print(f"  PG version (remote) {cmp['pg_version']['remote']}")

    # Extensions
    e = cmp["extensions"]
    print(f"\n  Extensions          local: {[x['name']+' '+x['version'] for x in e['local']]}")
    print(f"                      remote: {[x['name']+' '+x['version'] for x in e['remote']]}")
    print(f"                      diff: only_local={e['diff']['only_local']} only_remote={e['diff']['only_remote']}")

    # Tables
    t = cmp["tables"]
    print(f"\n  Tables (base)       local={t['local_count']}  remote={t['remote_count']}  both={t['diff']['both']}")
    if t["diff"]["only_local"]:
        print(f"    only_local:  {t['diff']['only_local']}")
    if t["diff"]["only_remote"]:
        print(f"    only_remote: {t['diff']['only_remote']}")

    # Partitioned parents
    pt = cmp["partitioned_tables"]
    print(f"\n  Partitioned parents local={pt['local']}")
    print(f"                      remote={pt['remote']}")
    print(f"                      diff: only_local={pt['diff']['only_local']} only_remote={pt['diff']['only_remote']}")

    # MVs
    mv = cmp["matviews"]
    print(f"\n  Materialized views  local={mv['local_count']}  remote={mv['remote_count']}  both={mv['diff']['both']}")
    if mv["diff"]["only_local"]:
        print(f"    only_local:  {mv['diff']['only_local']}")
    if mv["diff"]["only_remote"]:
        print(f"    only_remote: {mv['diff']['only_remote']}")

    # Indexes (count + name diff)
    i = cmp["indexes"]
    print(f"\n  Indexes             local={i['local_count']}  remote={i['remote_count']}  both={i['diff']['both']}")
    if i["diff"]["only_local"]:
        print(f"    only_local  ({len(i['diff']['only_local'])}): {i['diff']['only_local'][:10]}{'...' if len(i['diff']['only_local'])>10 else ''}")
    if i["diff"]["only_remote"]:
        print(f"    only_remote ({len(i['diff']['only_remote'])}): {i['diff']['only_remote'][:10]}{'...' if len(i['diff']['only_remote'])>10 else ''}")

    # Constraints
    c = cmp["constraints"]
    print(f"\n  Constraints by type")
    all_types = set(c["local"]) | set(c["remote"])
    for ty in sorted(all_types):
        lc = c["local"].get(ty, 0); rc = c["remote"].get(ty, 0)
        marker = "MATCH" if lc == rc else f"DIFF (Δ={lc-rc:+})"
        print(f"    {ty:24s} local={lc:>4} remote={rc:>4}  [{marker}]")

    # Functions
    f = cmp["functions"]
    print(f"\n  Functions (public)  local={f['local_count']}  remote={f['remote_count']}")
    if f["diff"]["only_local"] or f["diff"]["only_remote"]:
        print(f"    only_local:  {f['diff']['only_local']}")
        print(f"    only_remote: {f['diff']['only_remote']}")

    # Enums
    en = cmp["enum_types"]
    print(f"\n  Enum types          local={en['local']}")
    print(f"                      remote={en['remote']}")
    if en["diff"]["only_local"] or en["diff"]["only_remote"]:
        print(f"    only_local:  {en['diff']['only_local']}")
        print(f"    only_remote: {en['diff']['only_remote']}")

    # Per-table column drift
    cd = cmp["per_table_column_count_drift"]
    print(f"\n  Per-table column-count drift (tables present in both): {len(cd)} differing")
    if cd:
        for t, info in cd.items():
            print(f"    {t:35s} local_cols={info['local']:>3} remote_cols={info['remote']:>3} Δ={info['delta']:+}")

    # Verdict
    print()
    print("=" * 78)
    print("PARITY VERDICT")
    print("=" * 78)
    failures = []
    if not cmp["alembic_head"]["match"]:
        failures.append("alembic_head mismatch")
    if cmp["extensions"]["diff"]["only_local"] or cmp["extensions"]["diff"]["only_remote"]:
        failures.append("extensions mismatch")
    if cmp["tables"]["diff"]["only_local"] or cmp["tables"]["diff"]["only_remote"]:
        failures.append("tables mismatch")
    if cmp["matviews"]["diff"]["only_local"] or cmp["matviews"]["diff"]["only_remote"]:
        failures.append("matviews mismatch")
    if cmp["partitioned_tables"]["diff"]["only_local"] or cmp["partitioned_tables"]["diff"]["only_remote"]:
        failures.append("partitioned tables mismatch")
    if cmp["per_table_column_count_drift"]:
        failures.append("per-table column-count drift")
    if cmp["enum_types"]["diff"]["only_local"] or cmp["enum_types"]["diff"]["only_remote"]:
        failures.append("enum types mismatch")
    # Indexes are allowed to differ slightly (partitioned-child auto-indexes)
    # — we report counts but don't fail on it.

    if not failures:
        print("  ★ STATUS: FULL PARITY")
        print("  All structural objects (tables, MVs, partitions, columns, enums, alembic head) match.")
    else:
        print(f"  ★ STATUS: PARTIAL PARITY ({len(failures)} difference(s) flagged)")
        for f_ in failures:
            print(f"    - {f_}")

    import json
    from pathlib import Path
    Path("scripts/local_remote_parity.json").write_text(
        json.dumps({"local": local, "remote": remote, "comparison": cmp}, indent=2, default=str),
        encoding="utf-8",
    )
    print("\n  wrote scripts/local_remote_parity.json")


if __name__ == "__main__":
    asyncio.run(main())
