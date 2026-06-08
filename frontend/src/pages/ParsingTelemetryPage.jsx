/**
 * Page 4 — Parsing Telemetry.
 *
 * Per CR-041: every metric (drop count, unhandled count, validator severity
 * count, CAS stride distribution) comes from the backend. The frontend
 * renders recharts visualizations + DataTables but never sums, averages,
 * or computes rates client-side.
 *
 * Tabs: Drops over time / Drop reasons / Unhandled segments / CAS stride /
 *       Encoding distribution / Validator tiers / Reparse log
 *
 * "Window" selector at the top controls all tabs uniformly (days param
 * shared across hooks via react-query key).
 */

import { useMemo, useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart,
  Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import CodeBlock from '../components/shared/CodeBlock';
import DataTable from '../components/shared/DataTable';
import RefreshButton from '../components/shared/RefreshButton';
import StatusBadge from '../components/shared/StatusBadge';
import Tabs from '../components/shared/Tabs';
import {
  useTelemetryCasStride,
  useTelemetryDropReasons,
  useTelemetryDrops,
  useTelemetryEncoding,
  useTelemetryReparseLog,
  useTelemetryUnhandledSegments,
  useTelemetryValidatorTiers,
} from '../services/queries';


function WindowControl({ days, setDays }) {
  return (
    <div className="flex items-center gap-2 text-xs font-mono">
      <span className="text-slate-500">window:</span>
      {[1, 7, 30, 90].map((n) => (
        <button
          key={n}
          type="button"
          onClick={() => setDays(n)}
          className={`px-2 py-0.5 rounded border ${
            days === n
              ? 'bg-slate-900 text-white border-slate-900'
              : 'bg-white border-slate-300 hover:bg-slate-100'
          }`}
        >
          {n}d
        </button>
      ))}
    </div>
  );
}


// ─── Drops over time ────────────────────────────────────────────────────

function DropsTab({ days }) {
  const q = useTelemetryDrops({ days, group_by: 'day' });

  // Pivot backend rows {bucket, variant, count} into recharts-friendly shape.
  // NOTE: this is presentational reshaping (NOT computing rates) — pure
  // restructuring of the same numbers the backend returned.
  const { data, variants } = useMemo(() => {
    if (!q.data?.items) return { data: [], variants: [] };
    const byBucket = new Map();
    const vs = new Set();
    for (const row of q.data.items) {
      const v = row.service_variant ?? '(unknown)';
      vs.add(v);
      const bucket = row.bucket.slice(0, 10);
      if (!byBucket.has(bucket)) byBucket.set(bucket, { bucket });
      byBucket.get(bucket)[v] = row.dropped_count;
    }
    return {
      data: Array.from(byBucket.values()).sort((a, b) => a.bucket.localeCompare(b.bucket)),
      variants: Array.from(vs).sort(),
    };
  }, [q.data]);

  const palette = ['#ef4444', '#f59e0b', '#10b981', '#3b82f6', '#8b5cf6'];

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-mono text-slate-500">
          {q.data?.items?.length ?? 0} (bucket, variant) rows in last {days} day(s)
        </span>
        <RefreshButton onRefresh={q.refetch} updatedAt={q.dataUpdatedAt} isFetching={q.isFetching} />
      </div>
      {q.isError && <div className="text-xs font-mono text-red-700">Error: {q.error.message}</div>}
      {data.length === 0 ? (
        <div className="text-xs font-mono text-slate-500 border border-dashed border-slate-300 rounded p-6 text-center">
          no claim_dropped events in this window
        </div>
      ) : (
        <div className="h-72 bg-white border border-slate-300 rounded p-2">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="bucket" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip wrapperStyle={{ fontSize: 11, fontFamily: 'monospace' }} />
              <Legend wrapperStyle={{ fontSize: 11, fontFamily: 'monospace' }} />
              {variants.map((v, i) => (
                <Line
                  key={v}
                  type="monotone"
                  dataKey={v}
                  stroke={palette[i % palette.length]}
                  strokeWidth={2}
                  dot={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}


// ─── Drop reasons ───────────────────────────────────────────────────────

function DropReasonsTab({ days }) {
  const q = useTelemetryDropReasons({ days, top: 20 });
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-mono text-slate-500">
          {q.data?.total_errors?.toLocaleString() ?? '…'} total errors · top 20 (segment, field) pairs
        </span>
        <RefreshButton onRefresh={q.refetch} updatedAt={q.dataUpdatedAt} isFetching={q.isFetching} />
      </div>
      {q.isError && <div className="text-xs font-mono text-red-700">Error: {q.error.message}</div>}
      <DataTable
        data={q.data?.items}
        columns={[
          { accessorKey: 'segment', header: 'segment' },
          { accessorKey: 'field', header: 'field' },
          { accessorKey: 'count', header: 'count',
            cell: ({ getValue }) => getValue().toLocaleString() },
        ]}
        rowKey={(r) => `${r.segment}:${r.field}`}
        emptyMessage={q.isLoading ? 'loading…' : 'no validator errors in window'}
        dense
      />
    </div>
  );
}


// ─── Unhandled segments ─────────────────────────────────────────────────

function UnhandledSegmentsTab({ days }) {
  const q = useTelemetryUnhandledSegments({ days, top: 20 });
  const [sampleFor, setSampleFor] = useState(null);
  const sample = q.data?.items?.find((r) => r.segment_name === sampleFor);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-mono text-slate-500">
          {q.data?.items?.length ?? 0} unique unhandled segments seen · click row for sample text
        </span>
        <RefreshButton onRefresh={q.refetch} updatedAt={q.dataUpdatedAt} isFetching={q.isFetching} />
      </div>
      {q.isError && <div className="text-xs font-mono text-red-700">Error: {q.error.message}</div>}
      <DataTable
        data={q.data?.items}
        columns={[
          { accessorKey: 'segment_name', header: 'segment' },
          { accessorKey: 'count', header: 'count',
            cell: ({ getValue }) => getValue().toLocaleString() },
          { accessorKey: 'last_seen_at', header: 'last seen',
            cell: ({ getValue }) => getValue() ?? '—' },
          { accessorKey: 'last_seen_in_file_id', header: 'in edi_file_id',
            cell: ({ getValue }) => getValue() ?? '—' },
        ]}
        rowKey={(r) => r.segment_name}
        rowOnClick={(r) => setSampleFor(r.segment_name)}
        emptyMessage={q.isLoading ? 'loading…' : 'no unhandled segments in window'}
        dense
      />
      {sample && (
        <div className="border border-slate-400 rounded">
          <div className="flex items-center justify-between bg-slate-200 border-b border-slate-400 px-3 py-2">
            <h3 className="font-mono text-sm font-semibold">
              {sample.segment_name} — sample raw segment
            </h3>
            <button onClick={() => setSampleFor(null)}
                    className="text-xs font-mono px-2 py-0.5 rounded border border-slate-400 bg-white hover:bg-slate-100">
              ✕ close
            </button>
          </div>
          <div className="p-3">
            <CodeBlock text={sample.last_seen_text ?? '(no sample available)'} />
            <p className="mt-2 text-[10px] font-mono text-slate-500">
              From edi_file_id={sample.last_seen_in_file_id} at {sample.last_seen_at}.
              To handle this segment, add a handler under <code>src/rcm/parsing/handlers/</code>
              and register it for the appropriate transaction set.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}


// ─── CAS stride ─────────────────────────────────────────────────────────

function CasStrideTab({ days }) {
  const q = useTelemetryCasStride({ days });
  const palette = { 2: '#f59e0b', 3: '#10b981' };
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-mono text-slate-500">
          {q.data?.total_warnings?.toLocaleString() ?? '…'} CAS warnings · stride-2 = compact (non-spec) · stride-3 = spec
        </span>
        <RefreshButton onRefresh={q.refetch} updatedAt={q.dataUpdatedAt} isFetching={q.isFetching} />
      </div>
      {q.isError && <div className="text-xs font-mono text-red-700">Error: {q.error.message}</div>}
      {!q.data?.items?.length ? (
        <div className="text-xs font-mono text-slate-500 border border-dashed border-slate-300 rounded p-6 text-center">
          no CAS stride warnings in this window
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="h-64 bg-white border border-slate-300 rounded p-2">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={q.data.items}
                  dataKey="count"
                  nameKey="stride"
                  outerRadius={80}
                  label={({ stride, count }) => `stride ${stride}: ${count}`}
                >
                  {q.data.items.map((entry) => (
                    <Cell key={entry.stride} fill={palette[entry.stride] || '#94a3b8'} />
                  ))}
                </Pie>
                <Tooltip wrapperStyle={{ fontSize: 11, fontFamily: 'monospace' }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <DataTable
            data={q.data.items}
            columns={[
              { accessorKey: 'stride', header: 'stride',
                cell: ({ getValue }) => (
                  <StatusBadge value={`stride-${getValue()}`}
                    variant={getValue() === 2 ? 'warning' : 'success'} />
                ) },
              { accessorKey: 'count', header: 'count',
                cell: ({ getValue }) => getValue().toLocaleString() },
            ]}
            rowKey={(r) => `s${r.stride}`}
            dense
          />
        </div>
      )}
    </div>
  );
}


// ─── Encoding distribution ──────────────────────────────────────────────

function EncodingTab({ days }) {
  const q = useTelemetryEncoding({ days });
  return (
    <div className="space-y-3">
      <RefreshButton onRefresh={q.refetch} updatedAt={q.dataUpdatedAt} isFetching={q.isFetching} />
      {q.isError && <div className="text-xs font-mono text-red-700">Error: {q.error.message}</div>}
      {q.data?.note && (
        <div className="bg-amber-50 border border-amber-300 rounded p-3 text-xs font-mono text-amber-900">
          <strong>instrumentation pending:</strong> {q.data.note}
        </div>
      )}
      <DataTable
        data={q.data?.items}
        columns={[
          { accessorKey: 'encoding', header: 'encoding' },
          { accessorKey: 'count', header: 'count' },
        ]}
        rowKey={(r) => r.encoding}
        emptyMessage="awaiting backend instrumentation"
        dense
      />
    </div>
  );
}


// ─── Validator tiers ────────────────────────────────────────────────────

function ValidatorTiersTab({ days }) {
  const q = useTelemetryValidatorTiers({ days });
  const palette = { ERROR: '#ef4444', WARNING: '#f59e0b', INFO: '#3b82f6' };

  // Backend returns one row per (variant, validator, severity, count).
  // For the chart, group by validator with ERROR/WARNING stacks — same
  // numbers, different presentation.
  const chartData = useMemo(() => {
    if (!q.data?.items) return [];
    const byValidator = new Map();
    for (const r of q.data.items) {
      if (!byValidator.has(r.validator)) {
        byValidator.set(r.validator, { validator: r.validator, ERROR: 0, WARNING: 0, INFO: 0 });
      }
      byValidator.get(r.validator)[r.severity] += r.count;
    }
    return Array.from(byValidator.values());
  }, [q.data]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-mono text-slate-500">
          {q.data?.items?.length ?? 0} (variant, validator, severity) rows in last {days} day(s)
        </span>
        <RefreshButton onRefresh={q.refetch} updatedAt={q.dataUpdatedAt} isFetching={q.isFetching} />
      </div>
      {q.isError && <div className="text-xs font-mono text-red-700">Error: {q.error.message}</div>}
      {chartData.length === 0 ? (
        <div className="text-xs font-mono text-slate-500 border border-dashed border-slate-300 rounded p-6 text-center">
          no validator events in this window
        </div>
      ) : (
        <>
          <div className="h-64 bg-white border border-slate-300 rounded p-2">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="validator" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} />
                <Tooltip wrapperStyle={{ fontSize: 11, fontFamily: 'monospace' }} />
                <Legend wrapperStyle={{ fontSize: 11, fontFamily: 'monospace' }} />
                <Bar dataKey="ERROR"   stackId="s" fill={palette.ERROR} />
                <Bar dataKey="WARNING" stackId="s" fill={palette.WARNING} />
                <Bar dataKey="INFO"    stackId="s" fill={palette.INFO} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <DataTable
            data={q.data?.items}
            columns={[
              { accessorKey: 'service_variant', header: 'variant',
                cell: ({ getValue }) => getValue() ?? '—' },
              { accessorKey: 'validator', header: 'validator' },
              { accessorKey: 'severity', header: 'severity',
                cell: ({ getValue }) => (
                  <StatusBadge
                    value={getValue()}
                    variant={getValue() === 'ERROR' ? 'error' :
                             getValue() === 'WARNING' ? 'warning' : 'info'} />
                ) },
              { accessorKey: 'count', header: 'count',
                cell: ({ getValue }) => getValue().toLocaleString() },
            ]}
            rowKey={(r) => `${r.service_variant}|${r.validator}|${r.severity}`}
            dense
          />
        </>
      )}
    </div>
  );
}


// ─── Reparse log ────────────────────────────────────────────────────────

function ReparseLogTab({ days }) {
  const q = useTelemetryReparseLog({ days, limit: 50 });
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-mono text-slate-500">
          {q.data?.items?.length ?? 0} reparse events in last {days} day(s)
        </span>
        <RefreshButton onRefresh={q.refetch} updatedAt={q.dataUpdatedAt} isFetching={q.isFetching} />
      </div>
      <DataTable
        data={q.data?.items}
        columns={[
          { accessorKey: 'edi_file_id', header: 'id' },
          { accessorKey: 'file_name', header: 'file' },
          { accessorKey: 'parser_version', header: 'parser' },
          { accessorKey: 'parse_status', header: 'status',
            cell: ({ getValue }) => <StatusBadge value={getValue()} /> },
          { accessorKey: 'parse_completed_at', header: 'completed' },
          { accessorKey: 'claims_saved', header: 'saved',
            cell: ({ getValue }) => getValue() ?? '—' },
          { accessorKey: 'claims_dropped', header: 'dropped',
            cell: ({ getValue }) => getValue() ?? '—' },
        ]}
        rowKey={(r) => r.edi_file_id}
        emptyMessage="no reparse runs in window"
        dense
      />
    </div>
  );
}


// ─── Page shell ─────────────────────────────────────────────────────────

export default function ParsingTelemetryPage() {
  const [days, setDays] = useState(7);
  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h1 className="text-xl font-mono font-semibold text-slate-900">Parsing Telemetry</h1>
        <WindowControl days={days} setDays={setDays} />
      </div>
      <Tabs tabs={[
        { id: 'drops',    label: 'Drops',           render: () => <DropsTab days={days} /> },
        { id: 'reasons',  label: 'Drop reasons',    render: () => <DropReasonsTab days={days} /> },
        { id: 'unh',      label: 'Unhandled seg.',  render: () => <UnhandledSegmentsTab days={days} /> },
        { id: 'cas',      label: 'CAS stride',      render: () => <CasStrideTab days={days} /> },
        { id: 'enc',      label: 'Encoding',        render: () => <EncodingTab days={days} /> },
        { id: 'tiers',    label: 'Validator tiers', render: () => <ValidatorTiersTab days={days} /> },
        { id: 'reparse',  label: 'Reparse log',     render: () => <ReparseLogTab days={days} /> },
      ]} />
    </div>
  );
}
