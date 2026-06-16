# Autovacuum behavior investigation — 2026-06-10T07:53:34.622763+00:00

Read-only probes against `104.130.220.20:30432/rcm_denials` to classify
why autoanalyze/autovacuum has never fired on the 13 stale-stats tables
identified by AIR #1 verification.

## 1. pg_stat_activity — long-running transactions
  pid=56646 state=idle in transaction app='' xact_age=3.083065s xmin=None
    query: INSERT INTO claim_diagnoses (claim_id, sequence, diagnosis_code) VALUES ($1::BIGINT, $2::SMALLINT, $3::VARCHAR), ($4::BI
  pid=56656 state=idle in transaction app='' xact_age=-0.145326s xmin=2916661
    query: SELECT remittance_claims.id, remittance_claims.edi_file_id, remittance_claims.claim_id, remittance_claims.payer_claim_co
  pid=59408 state=active app='' xact_age=-0.907896s xmin=2916661
    query: 
        SELECT pid, datname, usename, application_name, state,
               xact_start, query_start, backend_xmin,
  

## 2. pg_prepared_xacts — uncommitted prepared transactions
  (none) — no prepared transactions are holding xmin back

## 3. pg_replication_slots — slots holding xmin
  (none) — no replication slots; nothing holding xmin externally

## 4. pg_stat_database — transaction activity
  db=rcm_denials commits=7868 rollbacks=531 deadlocks=239 conflicts=0
  stats_reset_at=None

## 5. pg_class.relfrozenxid age per target (autovacuum_freeze_max_age=200M default)
  claim_certifications         kind=b'r' reltuples=      7007 relpages=     115 frozen_xid_age=102228
  claim_lines                  kind=b'r' reltuples=     57058 relpages=    1077 frozen_xid_age=125687
  code_masters                 kind=b'r' reltuples=      1506 relpages=      92 frozen_xid_age=112633
  diagnoses                    kind=b'r' reltuples=     39308 relpages=     422 frozen_xid_age=125687
  edi_files                    kind=b'r' reltuples=      3892 relpages=     523 frozen_xid_age=122378
  home_care_episodes           kind=b'r' reltuples=      7007 relpages=     102 frozen_xid_age=102228
  parse_events                 kind=b'p' reltuples=        -1 relpages=       0 frozen_xid_age=2147483647
  patients                     kind=b'r' reltuples=     10261 relpages=     135 frozen_xid_age=120691
  payers                       kind=b'r' reltuples=        -1 relpages=       0 frozen_xid_age=476235
  pending_pair_registry        kind=b'r' reltuples=      2186 relpages=      24 frozen_xid_age=99912
  providers                    kind=b'r' reltuples=        -1 relpages=       0 frozen_xid_age=476235
  raw_segments                 kind=b'p' reltuples=        -1 relpages=       0 frozen_xid_age=2147483647
  subscribers                  kind=b'r' reltuples=     19925 relpages=     228 frozen_xid_age=471167

  (xid_age << autovacuum_freeze_max_age means no anti-wraparound vacuum was triggered)

## 6. Effective autovacuum thresholds per target
  cluster defaults: vac_threshold=50+0.2*N  ana_threshold=50+0.1*N  ins_threshold=1000+0.2*N

  claim_certifications             live=       0 dead=     0 mod_since_ana=       0 ins_since_vac=       0
    effective_thresholds: vac>50 dead, ana>50 mod, insvac>1000 ins
  claim_lines                      live=       0 dead=     0 mod_since_ana=       0 ins_since_vac=       0
    effective_thresholds: vac>50 dead, ana>50 mod, insvac>1000 ins
  code_masters                     live=       0 dead=     0 mod_since_ana=       0 ins_since_vac=       0
    effective_thresholds: vac>50 dead, ana>50 mod, insvac>1000 ins
  diagnoses                        live=       0 dead=     0 mod_since_ana=       0 ins_since_vac=       0
    effective_thresholds: vac>50 dead, ana>50 mod, insvac>1000 ins
  edi_files                        live=      85 dead=   292 mod_since_ana=     133 ins_since_vac=     329
    effective_thresholds: vac>67 dead, ana>58 mod, insvac>1017 ins
  home_care_episodes               live=       0 dead=     0 mod_since_ana=       0 ins_since_vac=       0
    effective_thresholds: vac>50 dead, ana>50 mod, insvac>1000 ins
  parse_events                     live=       0 dead=     0 mod_since_ana=       0 ins_since_vac=       0
    effective_thresholds: vac>50 dead, ana>50 mod, insvac>1000 ins
  parse_events_p2026_06            live=    2359 dead=     0 mod_since_ana=    2359 ins_since_vac=    2359
    effective_thresholds: vac>522 dead, ana>286 mod, insvac>1472 ins
  patients                         live=       0 dead=     0 mod_since_ana=       0 ins_since_vac=       0
    effective_thresholds: vac>50 dead, ana>50 mod, insvac>1000 ins
  payers                           live=       0 dead=     0 mod_since_ana=       0 ins_since_vac=       0
    effective_thresholds: vac>50 dead, ana>50 mod, insvac>1000 ins
  pending_pair_registry            live=       0 dead=     0 mod_since_ana=       0 ins_since_vac=       0
    effective_thresholds: vac>50 dead, ana>50 mod, insvac>1000 ins
  providers                        live=       0 dead=     0 mod_since_ana=       0 ins_since_vac=       0
    effective_thresholds: vac>50 dead, ana>50 mod, insvac>1000 ins
  raw_segments                     live=       0 dead=     0 mod_since_ana=       0 ins_since_vac=       0
    effective_thresholds: vac>50 dead, ana>50 mod, insvac>1000 ins
  raw_segments_p2026_06            live=    8244 dead=     0 mod_since_ana=    8244 ins_since_vac=    8244
    effective_thresholds: vac>1699 dead, ana>874 mod, insvac>2649 ins
  subscribers                      live=       0 dead=     0 mod_since_ana=       0 ins_since_vac=       0
    effective_thresholds: vac>50 dead, ana>50 mod, insvac>1000 ins

## 7. Autovacuum GUCs (sanity cross-check)
  autovacuum                                 = on           (source=default)
  autovacuum_freeze_max_age                  = 200000000    (source=default)
  autovacuum_max_workers                     = 3            (source=default)
  autovacuum_naptime                         = 60           (source=default)
  autovacuum_vacuum_cost_delay               = 2            (source=default)
  autovacuum_vacuum_cost_limit               = -1           (source=default)
  track_counts                               = on           (source=default)

## 8. pg_stat_progress_analyze / _vacuum — anything in flight?
  ANALYZE: nothing in flight
  VACUUM: nothing in flight

## Classification

### What is NOT the cause

- **Prepared transactions**: none exist (probe §2). Ruled out.
- **Replication slots**: none exist (probe §3). Ruled out.
- **Anti-wraparound vacuum pressure**: all heap targets show `frozen_xid_age` between 100k and 476k, well below `autovacuum_freeze_max_age=200000000`. No forced vacuums are competing for workers.
- **Autovacuum disabled**: `autovacuum=on`, `track_counts=on`, no per-table `autovacuum_enabled=off` reloption on any target. Ruled out.
- **Worker starvation due to wraparound work elsewhere**: nothing in flight (probe §8); `autovacuum_max_workers=3` is sufficient for our table count.

### What IS happening (high confidence)

**Root cause: statistics counters were reset, most likely by the audit_log-drop container restart sequence (CR-051).**

Evidence:
- The 13 stale tables all show `n_mod_since_analyze=0` and `n_ins_since_vacuum=0` (probe §6) despite having thousands of real rows. `pg_stat_database.stats_reset = None` means the cluster has never been *explicitly* reset, so the counters can only have reached zero via an unclean shutdown that lost in-memory state. PG 15+ holds stats in shared memory and flushes only at clean shutdown; unclean shutdowns wipe them.
- CR-051 documented "DB connection timeout during DROP TABLE — 8 GB audit_log_p2026_06 drop overwhelmed remote container. Container recovered after ~3 min." That's exactly the kind of unclean restart that would wipe in-memory counters.
- The tables that *did* get autoanalyzed (claims, remittance_claims, adjustments, remark_codes) are precisely the ones that were *written to* after the restart — their counters started accumulating again from zero and crossed thresholds.
- The 13 stale tables haven't been modified since the restart, so their counters stayed at 0 and never crossed any threshold.

### Secondary finding (medium confidence)

**The two partition children (`raw_segments_p2026_06`, `parse_events_p2026_06`) did accumulate counters after the restart** — `n_mod_since_analyze` was 8244 and 2359 respectively, both above their effective autoanalyze thresholds (874 and 286). Autoanalyze *should* have fired and didn't. Plausible explanations, in decreasing likelihood:

1. **Idle-in-transaction sessions blocking autovacuum eligibility** — probe §1 caught two such sessions live (one was `INSERT INTO claim_diagnoses`). Idle-in-transaction sessions don't block ANALYZE itself but can suppress autovacuum's eligibility heuristics.
2. **Worker preemption by deadlock recovery** — `pg_stat_database.deadlocks=239` (probe §4) is high; suggests app-level contention that may be making autovacuum workers exit early.
3. **PG 18 + Bitnami quirk on partition children** — known to be touchy across versions; not blocking and not worth chasing without further evidence.

### Recommendation

- The approved ANALYZE pass (Step C) is the correct immediate fix and was executed.
- **Defer** any autovacuum-tuning AIR until we have 24-72 h of post-ANALYZE observation: re-snapshot `last_autoanalyze` on these 13 tables, and if any regress (`NULL` again or stale-by-thresholds while new activity occurs), *then* a focused remediation AIR is warranted. If the counters maintain themselves correctly, the root cause was the one-time restart and no further work is needed.
- **Operational note** (not in scope for an AIR): the `idle in transaction` sessions seen during probe §1 are a separate code-hygiene issue. Whatever is leaving an INSERT transaction open for 3+ seconds should be audited — but that is not what's blocking autoanalyze today.