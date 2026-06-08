/**
 * Page 9 — Database State.
 *
 * Tabbed inventory: Overview / Tables / Materialized Views / Functions /
 * Indexes / Migrations / SQL Console.
 *
 * Per CR-041: this page DISPLAYS backend data only. No client-side aggregation
 * of denial rates / parser metrics / model metrics / etc. The tabs surface the
 * same pg_* catalog data scripts/verify_db_connection.py reports, just as JSON.
 *
 * Future-compatible pattern (reusable for Model Registry, Prediction Inspector,
 * Feature Inspector, Drift Monitoring, RAG Retrieval Inspector, Agent Traces):
 *
 *     Tabs → DataTable → drill-in detail panel
 *     |              |__ row click → setSelected(row)
 *     |__ list each independent slice as its own tab
 */

import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import CodeBlock from '../components/shared/CodeBlock';
import DataTable from '../components/shared/DataTable';
import KVTable from '../components/shared/KVTable';
import RefreshButton from '../components/shared/RefreshButton';
import StatusBadge from '../components/shared/StatusBadge';
import Tabs from '../components/shared/Tabs';
import * as api from '../services/api';
import {
  useDbFunctions,
  useDbIndexes,
  useDbMaterializedViews,
  useDbMigrations,
  useDbOverview,
  useDbTableDetail,
  useDbTables,
} from '../services/queries';


// ─── Overview tab ───────────────────────────────────────────────────────

function OverviewTab() {
  const q = useDbOverview();
  if (q.isLoading) return <div className="text-xs font-mono text-slate-500">loading…</div>;
  if (q.isError) return <div className="text-xs font-mono text-red-700">Error: {q.error.message}</div>;
  const d = q.data;
  return (
    <div className="space-y-3">
      <RefreshButton onRefresh={q.refetch} updatedAt={q.dataUpdatedAt} isFetching={q.isFetching} />
      <KVTable rows={[
        ['alembic_head',         d.alembic_head ?? '(none)'],
        ['base_tables',          d.base_tables],
        ['partitioned_parents',  d.partitioned_parents],
        ['partition_children',   d.partition_children],
        ['materialized_views',   d.materialized_views],
        ['enum_types',           d.enum_types],
        ['pl_pgsql_functions',   d.pl_pgsql_functions],
        ['foreign_keys',         d.foreign_keys],
        ['indexes',              d.indexes],
        ['unique_constraints',   d.unique_constraints],
        ['database_size',        `${d.database_size_pretty} (${d.database_size_bytes.toLocaleString()} bytes)`],
      ]} />
    </div>
  );
}


// ─── Tables tab ─────────────────────────────────────────────────────────

const TABLE_COLUMNS = [
  { accessorKey: 'kind',
    header: 'kind',
    cell: ({ getValue }) => (
      <StatusBadge
        value={getValue()}
        variant={getValue() === 'partitioned_parent' ? 'info' :
                 getValue() === 'partition_child' ? 'neutral' : 'success'}
      />
    ),
  },
  { accessorKey: 'name', header: 'name' },
  { accessorKey: 'parent', header: 'parent',
    cell: ({ getValue }) => getValue() ?? <span className="text-slate-400">—</span> },
  { accessorKey: 'row_count_estimate', header: 'rows (est.)',
    cell: ({ getValue }) => getValue().toLocaleString() },
  { accessorKey: 'total_size_pretty', header: 'size' },
  { accessorKey: 'index_count', header: 'idx' },
];

function TablesTab() {
  const [kind, setKind] = useState(null);
  const [selected, setSelected] = useState(null);
  const tables = useDbTables({ kind, sort: 'name', limit: 500 });
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs font-mono">
        <span className="text-slate-500">filter kind:</span>
        {['all', 'regular', 'partitioned_parent', 'partition_child'].map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => setKind(k === 'all' ? null : k)}
            className={`px-2 py-0.5 rounded border ${
              (kind ?? 'all') === k
                ? 'bg-slate-900 text-white border-slate-900'
                : 'bg-white border-slate-300 hover:bg-slate-100'
            }`}
          >
            {k}
          </button>
        ))}
        <span className="ml-auto text-slate-500">
          {tables.data?.total ?? '…'} total · click a row to inspect
        </span>
        <RefreshButton onRefresh={tables.refetch} updatedAt={tables.dataUpdatedAt}
                       isFetching={tables.isFetching} />
      </div>

      {tables.isError && (
        <div className="text-xs font-mono text-red-700">Error: {tables.error.message}</div>
      )}

      <DataTable
        data={tables.data?.items}
        columns={TABLE_COLUMNS}
        rowKey={(r) => r.name}
        rowOnClick={(r) => setSelected(r.name)}
        emptyMessage={tables.isLoading ? 'loading…' : 'no tables'}
        dense
      />

      {selected && (
        <TableDetailPanel name={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}


// ─── Table detail (drill-in pattern) ────────────────────────────────────

function TableDetailPanel({ name, onClose }) {
  const q = useDbTableDetail(name);
  return (
    <div className="mt-4 border border-slate-400 rounded bg-slate-50">
      <div className="flex items-center justify-between px-3 py-2 bg-slate-200 border-b border-slate-400">
        <h3 className="font-mono text-sm font-semibold">{name}</h3>
        <button
          type="button"
          onClick={onClose}
          className="text-xs font-mono px-2 py-0.5 rounded border border-slate-400 bg-white hover:bg-slate-100"
        >
          ✕ close
        </button>
      </div>
      <div className="p-3 space-y-3">
        {q.isLoading && <div className="text-xs font-mono text-slate-500">loading detail…</div>}
        {q.isError && <div className="text-xs font-mono text-red-700">Error: {q.error.message}</div>}
        {q.data && (
          <>
            <KVTable rows={[
              ['schema',              q.data.schema],
              ['kind',                q.data.kind],
              ['rows (est.)',         q.data.row_count_estimate.toLocaleString()],
              ['total size',          q.data.total_size_pretty],
              ['column count',        q.data.columns.length],
              ['index count',         q.data.indexes.length],
              ['FK out',              q.data.foreign_keys_out.length],
              ['FK in',               q.data.foreign_keys_in.length],
              ['partition children',  q.data.partition_children.length],
              ['partition bounds',    q.data.partition_bounds],
            ]} title="STRUCTURE" />

            <div>
              <div className="text-xs font-mono font-semibold text-slate-700 mb-1">COLUMNS ({q.data.columns.length})</div>
              <DataTable
                data={q.data.columns}
                columns={[
                  { accessorKey: 'name', header: 'name' },
                  { accessorKey: 'data_type', header: 'type' },
                  { accessorKey: 'is_nullable', header: 'null?',
                    cell: ({ getValue }) => (getValue() ? 'yes' : 'no') },
                  { accessorKey: 'column_default', header: 'default',
                    cell: ({ getValue }) => getValue() ?? <span className="text-slate-400">—</span> },
                ]}
                rowKey={(r) => r.name}
                dense
              />
            </div>

            <div>
              <div className="text-xs font-mono font-semibold text-slate-700 mb-1">INDEXES ({q.data.indexes.length})</div>
              <DataTable
                data={q.data.indexes}
                columns={[
                  { accessorKey: 'name', header: 'name' },
                  { accessorKey: 'is_unique', header: 'unique?',
                    cell: ({ getValue }) => getValue() ? 'yes' : '' },
                  { accessorKey: 'is_partial', header: 'partial?',
                    cell: ({ getValue }) => getValue() ? 'yes' : '' },
                  { accessorKey: 'size_pretty', header: 'size' },
                  { accessorKey: 'definition', header: 'definition',
                    cell: ({ getValue }) => (
                      <code className="text-[10px] text-slate-600 whitespace-pre-wrap">
                        {getValue()}
                      </code>
                    ),
                  },
                ]}
                rowKey={(r) => r.name}
                dense
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <div className="text-xs font-mono font-semibold text-slate-700 mb-1">FK OUT ({q.data.foreign_keys_out.length})</div>
                <DataTable
                  data={q.data.foreign_keys_out}
                  columns={[
                    { accessorKey: 'column', header: 'col' },
                    { accessorKey: 'referenced_table', header: '→ table' },
                    { accessorKey: 'referenced_column', header: '→ col' },
                    { accessorKey: 'on_delete', header: 'ondel' },
                  ]}
                  rowKey={(r) => r.name}
                  dense
                  emptyMessage="(none)"
                />
              </div>
              <div>
                <div className="text-xs font-mono font-semibold text-slate-700 mb-1">FK IN ({q.data.foreign_keys_in.length})</div>
                <DataTable
                  data={q.data.foreign_keys_in}
                  columns={[
                    { accessorKey: 'column', header: 'col' },
                    { accessorKey: 'referenced_table', header: 'from table' },
                    { accessorKey: 'referenced_column', header: 'from col' },
                    { accessorKey: 'on_delete', header: 'ondel' },
                  ]}
                  rowKey={(r) => r.name}
                  dense
                  emptyMessage="(none)"
                />
              </div>
            </div>

            {q.data.kind === 'partitioned_parent' && (
              <div>
                <div className="text-xs font-mono font-semibold text-slate-700 mb-1">PARTITION CHILDREN</div>
                <ul className="text-xs font-mono space-y-0.5">
                  {q.data.partition_children.map((c) => (
                    <li key={c} className="text-slate-700">{c}</li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}


// ─── Materialized Views tab ─────────────────────────────────────────────

function MaterializedViewsTab() {
  const q = useDbMaterializedViews();
  const qc = useQueryClient();
  const refresh = useMutation({
    mutationFn: api.refreshMaterializedView,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['db', 'materialized-views'] });
      qc.invalidateQueries({ queryKey: ['db', 'overview'] });
    },
  });

  if (q.isLoading) return <div className="text-xs font-mono text-slate-500">loading…</div>;
  if (q.isError) return <div className="text-xs font-mono text-red-700">Error: {q.error.message}</div>;

  const handleRefresh = (name) => {
    const ok = window.confirm(
      `Refresh materialized view "${name}"?\n\n` +
      `This is a developer operation. Runs REFRESH MATERIALIZED VIEW ` +
      `${'CONCURRENTLY'} when available. Confirm to proceed.`
    );
    if (ok) refresh.mutate(name);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-mono text-slate-500">
          {q.data.total} materialized views
        </span>
        <RefreshButton onRefresh={q.refetch} updatedAt={q.dataUpdatedAt}
                       isFetching={q.isFetching} />
      </div>

      <DataTable
        data={q.data.items}
        columns={[
          { accessorKey: 'name', header: 'name' },
          { accessorKey: 'is_populated', header: 'populated?',
            cell: ({ getValue }) => (
              <StatusBadge value={getValue() ? 'populated' : 'empty'}
                            variant={getValue() ? 'success' : 'warning'} />
            ) },
          { accessorKey: 'row_count_estimate', header: 'rows (est.)',
            cell: ({ getValue }) => getValue().toLocaleString() },
          { accessorKey: 'total_size_pretty', header: 'size' },
          { accessorKey: 'has_unique_index', header: 'concurrent-able?',
            cell: ({ getValue }) => getValue() ? 'yes' : 'no' },
          { id: 'actions', header: '',
            cell: ({ row }) => (
              <button
                type="button"
                onClick={() => handleRefresh(row.original.name)}
                disabled={refresh.isPending}
                className="px-2 py-0.5 text-[10px] font-mono rounded border border-amber-400 bg-amber-50 hover:bg-amber-100 disabled:opacity-50"
                title="Refresh this MV (requires confirmation)"
              >
                ↻ refresh
              </button>
            ),
          },
        ]}
        rowKey={(r) => r.name}
        dense
      />

      {refresh.isError && (
        <div className="text-xs font-mono text-red-700">
          Refresh failed: {refresh.error?.message ?? String(refresh.error)}
        </div>
      )}
      {refresh.isSuccess && (
        <div className="text-xs font-mono text-green-800">
          ✓ refreshed {refresh.data.name} in {refresh.data.duration_ms}ms ·
          rows after: {refresh.data.row_count_estimate_after.toLocaleString()}
        </div>
      )}
    </div>
  );
}


// ─── Functions tab ──────────────────────────────────────────────────────

function FunctionsTab() {
  const q = useDbFunctions();
  if (q.isLoading) return <div className="text-xs font-mono text-slate-500">loading…</div>;
  if (q.isError) return <div className="text-xs font-mono text-red-700">Error: {q.error.message}</div>;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-mono text-slate-500">{q.data.total} functions</span>
        <RefreshButton onRefresh={q.refetch} updatedAt={q.dataUpdatedAt} isFetching={q.isFetching} />
      </div>
      <DataTable
        data={q.data.items}
        columns={[
          { accessorKey: 'name', header: 'name' },
          { accessorKey: 'language', header: 'language' },
          { accessorKey: 'return_type', header: 'returns' },
          { accessorKey: 'argument_signature', header: 'args',
            cell: ({ getValue }) => <code className="text-[10px]">{getValue() || '()'}</code> },
          { accessorKey: 'volatility', header: 'volatility' },
        ]}
        rowKey={(r) => r.name}
        dense
      />
    </div>
  );
}


// ─── Indexes tab ────────────────────────────────────────────────────────

function IndexesTab() {
  const q = useDbIndexes({ limit: 500 });
  if (q.isLoading) return <div className="text-xs font-mono text-slate-500">loading…</div>;
  if (q.isError) return <div className="text-xs font-mono text-red-700">Error: {q.error.message}</div>;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-mono text-slate-500">
          {q.data.total} indexes (showing first {q.data.items.length})
        </span>
        <RefreshButton onRefresh={q.refetch} updatedAt={q.dataUpdatedAt} isFetching={q.isFetching} />
      </div>
      <DataTable
        data={q.data.items}
        columns={[
          { accessorKey: 'table_name', header: 'table' },
          { accessorKey: 'name', header: 'name' },
          { accessorKey: 'is_unique', header: 'unique?',
            cell: ({ getValue }) => getValue() ? 'yes' : '' },
          { accessorKey: 'is_partial', header: 'partial?',
            cell: ({ getValue }) => getValue() ? 'yes' : '' },
          { accessorKey: 'size_bytes', header: 'size (bytes)',
            cell: ({ getValue }) => getValue().toLocaleString() },
        ]}
        rowKey={(r) => r.name}
        dense
      />
    </div>
  );
}


// ─── Migrations tab ─────────────────────────────────────────────────────

function MigrationsTab() {
  const q = useDbMigrations();
  if (q.isLoading) return <div className="text-xs font-mono text-slate-500">loading…</div>;
  if (q.isError) return <div className="text-xs font-mono text-red-700">Error: {q.error.message}</div>;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs font-mono text-slate-500">
        current head: <span className="text-slate-900 font-semibold">{q.data.current_head ?? '(none)'}</span>
        <span>·</span>
        <span>{q.data.total_revisions} revisions total</span>
        <RefreshButton onRefresh={q.refetch} updatedAt={q.dataUpdatedAt} isFetching={q.isFetching} />
      </div>
      <DataTable
        data={q.data.items}
        columns={[
          { accessorKey: 'is_current', header: '★',
            cell: ({ getValue }) => getValue() ? '★' : '' },
          { accessorKey: 'revision', header: 'revision' },
          { accessorKey: 'down_revision', header: 'down rev',
            cell: ({ getValue }) => getValue() ?? <span className="text-slate-400">(root)</span> },
          { accessorKey: 'title', header: 'title' },
        ]}
        rowKey={(r) => r.revision}
        dense
      />
    </div>
  );
}


// ─── SQL Console tab ────────────────────────────────────────────────────

function SqlConsoleTab() {
  const [sql, setSql] = useState('SELECT count(*) FROM claims WHERE service_variant = \'837P\';');
  const mutation = useMutation({ mutationFn: api.runSqlQuery });

  const run = (e) => {
    e?.preventDefault?.();
    mutation.mutate(sql);
  };

  return (
    <div className="space-y-3">
      <form onSubmit={run} className="space-y-2">
        <textarea
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          spellCheck={false}
          rows={8}
          className="w-full font-mono text-xs p-3 border border-slate-400 rounded bg-slate-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-300"
          placeholder="SELECT ..."
        />
        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={mutation.isPending || !sql.trim()}
            className="px-3 py-1 text-xs font-mono rounded bg-blue-700 text-white hover:bg-blue-800 disabled:opacity-50"
          >
            {mutation.isPending ? 'running…' : 'run (Ctrl+Enter)'}
          </button>
          <span className="text-[10px] font-mono text-slate-500">
            SELECT-only · max 1000 rows · 30s statement_timeout · single statement
          </span>
        </div>
      </form>

      {mutation.isError && (
        <div className="border border-red-300 bg-red-50 rounded p-2 text-xs font-mono text-red-800">
          {mutation.error?.message ?? String(mutation.error)}
        </div>
      )}

      {mutation.isSuccess && (
        <SqlResults result={mutation.data} />
      )}
    </div>
  );
}

function SqlResults({ result }) {
  return (
    <div className="space-y-2">
      <div className="text-xs font-mono text-slate-600">
        {result.row_count} rows in {result.duration_ms}ms
        {result.truncated && (
          <span className="ml-2 text-amber-700">
            · truncated at {result.row_limit} rows
          </span>
        )}
      </div>
      <div className="overflow-x-auto border border-slate-300 rounded max-h-[60vh] overflow-y-auto">
        <table className="w-full text-xs font-mono">
          <thead className="bg-slate-100 border-b border-slate-300 text-slate-700 sticky top-0">
            <tr>
              {result.columns.map((c) => (
                <th key={c} className="text-left px-3 py-1 font-normal whitespace-nowrap">{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, i) => (
              <tr key={i} className={i % 2 === 0 ? 'bg-white' : 'bg-slate-50'}>
                {row.map((v, j) => (
                  <td key={j} className="px-3 py-1 align-top whitespace-pre-wrap break-all">
                    {v === null ? <span className="text-slate-400">null</span> : String(v)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


// ─── Page shell ─────────────────────────────────────────────────────────

export default function DatabasePage() {
  return (
    <div>
      <h1 className="text-xl font-mono font-semibold text-slate-900 mb-3">Database</h1>
      <Tabs tabs={[
        { id: 'overview',  label: 'Overview',  render: () => <OverviewTab /> },
        { id: 'tables',    label: 'Tables',    render: () => <TablesTab /> },
        { id: 'mvs',       label: 'Mat. Views', render: () => <MaterializedViewsTab /> },
        { id: 'fns',       label: 'Functions', render: () => <FunctionsTab /> },
        { id: 'idx',       label: 'Indexes',   render: () => <IndexesTab /> },
        { id: 'migr',      label: 'Migrations', render: () => <MigrationsTab /> },
        { id: 'sql',       label: 'SQL Console', render: () => <SqlConsoleTab /> },
      ]} />
    </div>
  );
}
