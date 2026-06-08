/**
 * Page 1 — Home dashboard.
 *
 * Six tiles per UI-plan §3 Page 1:
 *   1. System health      — /env/info + /env/health
 *   2. Recent uploads     — /uploads/recent?limit=10
 *   3. Drop rate (24h)    — /telemetry/drops?days=1&group_by=hour
 *   4. Unhandled segments — /telemetry/unhandled-segments?days=7&top=10
 *   5. Model registry     — /models/registry
 *   6. Job queue          — /jobs/summary
 *
 * Per CR-041: tiles ONLY display data the backend returned. No client-side
 * computation of denial rates, parser metrics, model statistics, etc.
 *
 * Each tile is clickable → drills into its detail page (per UI-plan §3).
 */

import { Link } from 'react-router-dom';
import {
  Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from 'recharts';
import RefreshButton from '../components/shared/RefreshButton';
import StatusBadge from '../components/shared/StatusBadge';
import {
  useEnvInfo,
  useHealth,
  useJobsSummary,
  useModelsRegistry,
  useRecentUploads,
  useTelemetryDrops,
  useTelemetryUnhandledSegments,
} from '../services/queries';


function Tile({ title, to, children, refetch, isFetching, updatedAt }) {
  const body = (
    <div className="bg-white border border-slate-300 rounded overflow-hidden h-full flex flex-col">
      <header className="px-3 py-1.5 bg-slate-100 border-b border-slate-300 flex items-center justify-between">
        <h2 className="text-xs font-mono font-semibold text-slate-700">{title}</h2>
        {to && <span className="text-[10px] font-mono text-blue-700">open →</span>}
      </header>
      <div className="p-3 flex-1 min-h-0">{children}</div>
      {refetch && (
        <footer className="px-3 py-1 border-t border-slate-200 flex items-center justify-end">
          <RefreshButton onRefresh={refetch} isFetching={isFetching} updatedAt={updatedAt} />
        </footer>
      )}
    </div>
  );
  return to ? (
    <Link to={to} className="block focus:outline-none focus:ring-2 focus:ring-blue-300 rounded">
      {body}
    </Link>
  ) : (
    body
  );
}


// ─── Tile 1: System health ──────────────────────────────────────────────

function SystemHealthTile() {
  const env = useEnvInfo();
  const health = useHealth();
  if (env.isLoading || health.isLoading)
    return <Tile title="System health"><div className="text-xs font-mono text-slate-500">…</div></Tile>;
  if (env.isError)
    return <Tile title="System health"><div className="text-xs font-mono text-red-700">{env.error.message}</div></Tile>;

  const dbUp = health.data?.database === 'up';
  const vector = env.data?.extensions?.required?.find((e) => e.name === 'vector');
  const optionalCounts = env.data?.extensions?.optional?.reduce(
    (acc, e) => { acc[e.status] = (acc[e.status] ?? 0) + 1; return acc; }, {},
  );

  return (
    <Tile
      title="System health"
      to="/env"
      refetch={env.refetch}
      updatedAt={env.dataUpdatedAt}
      isFetching={env.isFetching}
    >
      <dl className="space-y-1.5 text-xs font-mono">
        <div className="flex justify-between"><dt className="text-slate-500">DB</dt>
          <dd><StatusBadge value={dbUp ? 'up' : 'down'} /></dd></div>
        <div className="flex justify-between"><dt className="text-slate-500">alembic</dt>
          <dd className="text-slate-900">{env.data?.database?.alembic_head ?? '—'}</dd></div>
        <div className="flex justify-between"><dt className="text-slate-500">size</dt>
          <dd className="text-slate-900">{env.data?.database?.size_pretty ?? '—'}</dd></div>
        <div className="flex justify-between"><dt className="text-slate-500">pgvector</dt>
          <dd><StatusBadge value={vector?.status ?? '?'}
            variant={vector?.status === 'installed' ? 'success' :
                     vector?.status === 'available' ? 'warning' : 'error'} /></dd></div>
        <div className="flex justify-between"><dt className="text-slate-500">opt. ext.</dt>
          <dd className="text-slate-700 text-[10px]">
            {optionalCounts
              ? Object.entries(optionalCounts).map(([s, n]) => `${n} ${s}`).join(' · ')
              : '—'}
          </dd></div>
      </dl>
    </Tile>
  );
}


// ─── Tile 2: Recent uploads ─────────────────────────────────────────────

function RecentUploadsTile() {
  const q = useRecentUploads({ limit: 10 });
  return (
    <Tile
      title="Recent uploads (10)"
      to="/edi"
      refetch={q.refetch}
      updatedAt={q.dataUpdatedAt}
      isFetching={q.isFetching}
    >
      {q.isLoading && <div className="text-xs font-mono text-slate-500">…</div>}
      {q.isError && <div className="text-xs font-mono text-red-700">{q.error.message}</div>}
      {q.data && q.data.items.length === 0 && (
        <div className="text-xs font-mono text-slate-500 text-center py-6">
          no uploads yet
        </div>
      )}
      {q.data && q.data.items.length > 0 && (
        <ul className="text-[11px] font-mono space-y-0.5 max-h-48 overflow-y-auto">
          {q.data.items.map((u) => (
            <li key={u.id} className="flex items-center gap-2">
              <span className="text-slate-500 w-5">#{u.id}</span>
              <span className="flex-1 truncate" title={u.file_name}>{u.file_name}</span>
              <StatusBadge value={u.parse_status} />
              <span className="text-slate-500 w-14 text-right">
                {u.claims_saved ?? '—'}/{u.claims_dropped ?? '—'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Tile>
  );
}


// ─── Tile 3: Drop rate (24h) ────────────────────────────────────────────

function DropRateTile() {
  const q = useTelemetryDrops({ days: 1, group_by: 'hour' });

  // Sum across variants per bucket — backend returned one row per
  // (bucket, variant); we collapse to a single line for the home tile.
  // NOTE: this is reshaping the same numbers, not aggregating new metrics.
  const data = (q.data?.items ?? []).reduce((acc, row) => {
    const bucket = row.bucket.slice(11, 16);  // "HH:MM"
    const found = acc.find((d) => d.bucket === bucket);
    if (found) found.dropped += row.dropped_count;
    else acc.push({ bucket, dropped: row.dropped_count });
    return acc;
  }, []).sort((a, b) => a.bucket.localeCompare(b.bucket));

  const totalDrops = data.reduce((s, d) => s + d.dropped, 0);

  return (
    <Tile
      title="Drop rate (24h)"
      to="/telemetry"
      refetch={q.refetch}
      updatedAt={q.dataUpdatedAt}
      isFetching={q.isFetching}
    >
      {q.isLoading && <div className="text-xs font-mono text-slate-500">…</div>}
      {q.isError && <div className="text-xs font-mono text-red-700">{q.error.message}</div>}
      {q.data && (
        <>
          <div className="text-2xl font-mono font-semibold text-slate-900">
            {totalDrops.toLocaleString()}
            <span className="text-xs font-normal text-slate-500 ml-2">drops</span>
          </div>
          {data.length === 0 ? (
            <div className="text-xs font-mono text-slate-500 mt-3 text-center">
              no drops in last 24h
            </div>
          ) : (
            <div className="h-20 mt-2">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={data}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
                  <XAxis dataKey="bucket" tick={{ fontSize: 9 }} interval="preserveStartEnd" />
                  <YAxis tick={{ fontSize: 9 }} width={24} />
                  <Tooltip wrapperStyle={{ fontSize: 10, fontFamily: 'monospace' }} />
                  <Line type="monotone" dataKey="dropped" stroke="#ef4444" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </>
      )}
    </Tile>
  );
}


// ─── Tile 4: Unhandled segments (7d) ────────────────────────────────────

function UnhandledSegmentsTile() {
  const q = useTelemetryUnhandledSegments({ days: 7, top: 10 });
  return (
    <Tile
      title="Unhandled segments (7d)"
      to="/telemetry"
      refetch={q.refetch}
      updatedAt={q.dataUpdatedAt}
      isFetching={q.isFetching}
    >
      {q.isLoading && <div className="text-xs font-mono text-slate-500">…</div>}
      {q.isError && <div className="text-xs font-mono text-red-700">{q.error.message}</div>}
      {q.data && q.data.items.length === 0 && (
        <div className="text-xs font-mono text-slate-500 text-center py-6">
          no unhandled segments — parser coverage looks complete
        </div>
      )}
      {q.data && q.data.items.length > 0 && (
        <div className="h-40">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={q.data.items} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis type="number" tick={{ fontSize: 9 }} />
              <YAxis type="category" dataKey="segment_name" tick={{ fontSize: 10 }} width={56} />
              <Tooltip wrapperStyle={{ fontSize: 10, fontFamily: 'monospace' }} />
              <Bar dataKey="count" fill="#f59e0b" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </Tile>
  );
}


// ─── Tile 5: Model registry ─────────────────────────────────────────────

function ModelRegistryTile() {
  const q = useModelsRegistry();
  return (
    <Tile
      title="Model registry"
      to="/db"   // /ml not built yet; route to /db so the click is meaningful
      refetch={q.refetch}
      updatedAt={q.dataUpdatedAt}
      isFetching={q.isFetching}
    >
      {q.isLoading && <div className="text-xs font-mono text-slate-500">…</div>}
      {q.isError && <div className="text-xs font-mono text-red-700">{q.error.message}</div>}
      {q.data && (
        <>
          <div className="text-2xl font-mono font-semibold text-slate-900">
            {q.data.trained_count}
            <span className="text-base text-slate-500"> / {q.data.total}</span>
            <span className="text-xs font-normal text-slate-500 ml-2">trained / registered</span>
          </div>
          <ul className="mt-2 text-[10px] font-mono space-y-0.5 max-h-32 overflow-y-auto">
            {q.data.items.map((m) => (
              <li key={`${m.service_variant}|${m.claim_subtype}`}
                  className="flex items-center justify-between">
                <span className="text-slate-700 truncate">
                  {m.service_variant}/{m.claim_subtype}
                </span>
                <span className="flex items-center gap-2">
                  <span className="text-slate-500">{m.feature_count}f</span>
                  <StatusBadge
                    value={m.has_trained_model ? 'trained' : 'untrained'}
                    variant={m.has_trained_model ? 'success' : 'neutral'}
                  />
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Tile>
  );
}


// ─── Tile 6: Job queue ──────────────────────────────────────────────────

function JobQueueTile() {
  const q = useJobsSummary();
  return (
    <Tile
      title="Job queue"
      to="/db"  // /jobs page not built yet
      refetch={q.refetch}
      updatedAt={q.dataUpdatedAt}
      isFetching={q.isFetching}
    >
      {q.isLoading && <div className="text-xs font-mono text-slate-500">…</div>}
      {q.isError && <div className="text-xs font-mono text-red-700">{q.error.message}</div>}
      {q.data && (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <Pill label="queued"   count={q.data.queued} tone="info" />
            <Pill label="running"  count={q.data.running} tone="info" />
            <Pill label="ok (1h)"  count={q.data.succeeded_last_hour} tone="success" />
            <Pill label="fail (1h)" count={q.data.failed_last_hour} tone="error" />
          </div>
          <div className="border-t border-slate-200 pt-2">
            <div className="text-[10px] font-mono text-slate-500 mb-0.5">all-time:</div>
            <ul className="text-[10px] font-mono space-y-0.5">
              {q.data.by_status.map((row) => (
                <li key={row.status} className="flex items-center justify-between">
                  <span><StatusBadge value={row.status} /></span>
                  <span className="text-slate-900">{row.count.toLocaleString()}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Tile>
  );
}

function Pill({ label, count, tone }) {
  const toneClass = {
    info:    'bg-blue-50 border-blue-300',
    success: 'bg-green-50 border-green-300',
    error:   'bg-red-50 border-red-300',
  }[tone] ?? 'bg-slate-50 border-slate-300';
  return (
    <div className={`text-center py-1 rounded border ${toneClass}`}>
      <div className="text-lg font-mono font-semibold">{count.toLocaleString()}</div>
      <div className="text-[10px] font-mono text-slate-600">{label}</div>
    </div>
  );
}


// ─── Page shell ─────────────────────────────────────────────────────────

export default function HomePage() {
  return (
    <div>
      <h1 className="text-xl font-mono font-semibold text-slate-900 mb-3">Home</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        <SystemHealthTile />
        <RecentUploadsTile />
        <DropRateTile />
        <UnhandledSegmentsTile />
        <ModelRegistryTile />
        <JobQueueTile />
      </div>
    </div>
  );
}
