/**
 * Page 12 — Environment & Settings.
 *
 * Read-only operational sanity-check. Mirrors what scripts/verify_db_connection.py
 * reports. Five panels (per UI-plan §3 Page 12):
 *   1. Database connection (DSN redacted, version, size, alembic head)
 *   2. Extensions (required + optional with installation status)
 *   3. PG cluster settings (shared_buffers, max_wal_size, etc.)
 *   4. Feature flags + versions
 *   5. Pinned library versions
 *
 * The page issues one GET /api/dev/env/info call. Single endpoint = single
 * source of truth = no UI inconsistency between panels.
 */

import KVTable from '../components/shared/KVTable';
import RefreshButton from '../components/shared/RefreshButton';
import StatusBadge from '../components/shared/StatusBadge';
import { useEnvInfo } from '../services/queries';

function ExtensionRow({ ext }) {
  return (
    <div className="flex items-center justify-between px-3 py-1.5 text-xs font-mono border-b border-slate-200 last:border-b-0">
      <div>
        <div className="text-slate-900">{ext.name}</div>
        <div className="text-slate-500 text-[10px]">
          installed={ext.installed_version ?? '—'} · available={ext.available_version ?? '—'}
        </div>
      </div>
      <StatusBadge
        value={ext.status}
        variant={
          ext.status === 'installed' ? 'success' :
          ext.status === 'available' ? 'warning' :
          ext.status === 'not_available' ? 'error' :
          'neutral'
        }
      />
    </div>
  );
}

function Panel({ title, children, className = '' }) {
  return (
    <section className={`bg-white border border-slate-300 rounded overflow-hidden ${className}`}>
      <header className="px-3 py-2 bg-slate-100 border-b border-slate-300 text-xs font-mono font-semibold text-slate-700">
        {title}
      </header>
      <div>{children}</div>
    </section>
  );
}

export default function EnvPage() {
  const env = useEnvInfo();

  if (env.isLoading) return <div className="text-sm text-slate-500 font-mono">loading env info…</div>;
  if (env.isError) {
    return (
      <div className="text-sm text-red-700 font-mono">
        Error: {env.error?.message || String(env.error)}
      </div>
    );
  }

  const d = env.data;
  const required = d.extensions?.required ?? [];
  const optional = d.extensions?.optional ?? [];

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-mono font-semibold text-slate-900">Environment</h1>
        <RefreshButton
          onRefresh={env.refetch}
          updatedAt={env.dataUpdatedAt}
          isFetching={env.isFetching}
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Panel title="DATABASE">
          <KVTable rows={[
            ['DSN (redacted)',     d.database?.url_redacted],
            ['PG version',         d.database?.version],
            ['Current database',   d.database?.current_database],
            ['Size',               d.database?.size_pretty],
            ['Size (bytes)',       d.database?.size_bytes],
            ['alembic head',       d.database?.alembic_head],
          ]} />
        </Panel>

        <Panel title="VERSIONS">
          <KVTable rows={[
            ['Python',                       d.versions?.python],
            ['FastAPI',                      d.versions?.fastapi],
            ['SQLAlchemy',                   d.versions?.sqlalchemy],
            ['asyncpg',                      d.versions?.asyncpg],
            ['pgvector (Python)',            d.versions?.pgvector],
            ['parser version (settings)',    d.parser_version],
            ['feature engineering version',  d.feature_engineering_version],
          ]} />
        </Panel>

        <Panel title="EXTENSIONS (required)">
          {required.length === 0
            ? <div className="text-xs text-slate-500 px-3 py-2 font-mono">none required</div>
            : required.map((e) => <ExtensionRow key={e.name} ext={e} />)}
        </Panel>

        <Panel title="EXTENSIONS (optional)">
          {optional.length === 0
            ? <div className="text-xs text-slate-500 px-3 py-2 font-mono">none</div>
            : optional.map((e) => <ExtensionRow key={e.name} ext={e} />)}
        </Panel>

        <Panel title="PG SETTINGS" className="md:col-span-2">
          <KVTable rows={Object.entries(d.pg_settings ?? {})} />
        </Panel>

        <Panel title="FEATURE FLAGS">
          <KVTable rows={[
            ['DEBUG',         String(d.feature_flags?.DEBUG)],
            ['RAG_ENABLED',   String(d.feature_flags?.RAG_ENABLED)],
            ['LLM_PROVIDER',  d.feature_flags?.LLM_PROVIDER ?? '(none)'],
          ]} />
        </Panel>

        <Panel title="ML CONSTANTS">
          <KVTable rows={[
            ['MIN_TRAINING_SIZE',  d.min_training_size],
            ['PRECISION_FLOOR',    d.precision_floor],
            ['LOW_PROB_CUTOFF',    d.low_prob_cutoff],
          ]} />
        </Panel>
      </div>
    </div>
  );
}
