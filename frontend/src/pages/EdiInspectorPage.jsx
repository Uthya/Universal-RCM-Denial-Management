/**
 * Page 2 — EDI Inspector.
 *
 * Three tabs:
 *   1. Upload      — pick a file → POST /api/dev/edi/upload?confirm=true
 *   2. Files       — searchable / filterable list of edi_files
 *   3. Parse Trace — drill-in panel for a selected file: meta + segments + events
 *
 * Per CR-041: counts and rows come from the backend; this page renders only.
 * The upload mutation invalidates ['edi', 'files'] + ['uploads', 'recent'] so the
 * Home tile and the Files tab refresh together.
 */

import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import DataTable from '../components/shared/DataTable';
import KVTable from '../components/shared/KVTable';
import RefreshButton from '../components/shared/RefreshButton';
import StatusBadge from '../components/shared/StatusBadge';
import Tabs from '../components/shared/Tabs';
import * as api from '../services/api';
import {
  useEdiFileDetail,
  useEdiFileEvents,
  useEdiFileSegments,
  useEdiFiles,
} from '../services/queries';


// ─── Upload tab ─────────────────────────────────────────────────────────

function UploadTab({ onUploadedFileId }) {
  const fileInputRef = useRef(null);
  const qc = useQueryClient();
  const [selected, setSelected] = useState(null);

  const upload = useMutation({
    mutationFn: api.uploadEdi,
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['edi', 'files'] });
      qc.invalidateQueries({ queryKey: ['uploads', 'recent'] });
      if (data.edi_file_id) onUploadedFileId?.(data.edi_file_id);
    },
  });

  const onPick = (e) => {
    const f = e.target.files?.[0];
    if (f) setSelected(f);
  };
  const onSubmit = (e) => {
    e.preventDefault();
    if (selected) upload.mutate(selected);
  };
  const onClear = () => {
    setSelected(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
    upload.reset();
  };

  return (
    <div className="space-y-4 max-w-3xl">
      <form onSubmit={onSubmit} className="border border-slate-300 rounded p-3 bg-slate-50">
        <div className="text-xs font-mono font-semibold text-slate-700 mb-2">
          Upload an EDI file (837P/I/D or 835)
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept=".edi,.txt,.x12,.dat,application/octet-stream,text/plain"
          onChange={onPick}
          className="text-xs font-mono"
        />
        <div className="flex items-center gap-2 mt-2">
          <button
            type="submit"
            disabled={!selected || upload.isPending}
            className="px-3 py-1 text-xs font-mono rounded bg-blue-700 text-white hover:bg-blue-800 disabled:opacity-50"
          >
            {upload.isPending ? 'uploading & parsing…' : 'upload + parse'}
          </button>
          <button
            type="button"
            onClick={onClear}
            className="px-3 py-1 text-xs font-mono rounded border border-slate-300 bg-white hover:bg-slate-100"
          >
            clear
          </button>
          <span className="text-[10px] font-mono text-slate-500">
            Local-only dev endpoint · runs parse_and_save end-to-end · max 50MB
          </span>
        </div>
      </form>

      {upload.isError && (
        <div className="border border-red-300 bg-red-50 rounded p-3">
          <div className="text-xs font-mono font-semibold text-red-800 mb-1">
            Upload failed
          </div>
          <pre className="text-[11px] font-mono text-red-900 whitespace-pre-wrap">
            {upload.error?.message ?? String(upload.error)}
          </pre>
        </div>
      )}

      {upload.isSuccess && <UploadResultPanel result={upload.data} />}
    </div>
  );
}

function UploadResultPanel({ result }) {
  if (result.error) {
    return (
      <div className="border border-amber-400 bg-amber-50 rounded p-3">
        <div className="text-xs font-mono font-semibold text-amber-900 mb-1">
          Parsed with envelope error
        </div>
        <pre className="text-[11px] font-mono text-amber-900 whitespace-pre-wrap">
          {result.error}
        </pre>
      </div>
    );
  }
  return (
    <div className="border border-green-400 bg-green-50 rounded p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-xs font-mono font-semibold text-green-900">
          Parsed successfully · edi_file_id={result.edi_file_id}
        </div>
        <StatusBadge value={result.parse_status} />
      </div>
      <KVTable rows={[
        ['file_name',                 result.file_name],
        ['file_type',                 result.file_type],
        ['service_variant_detected',  result.service_variant_detected ?? '—'],
        ['claim_subtype_detected',    result.claim_subtype_detected ?? '—'],
        ['claims_saved',              result.claims_saved ?? '—'],
        ['claims_dropped',            result.claims_dropped ?? '—'],
        ['parser_version',            result.parser_version],
        ['parse_duration_ms',         result.parse_duration_ms],
      ]} />
    </div>
  );
}


// ─── Files tab ──────────────────────────────────────────────────────────

const FILE_COLUMNS = [
  { accessorKey: 'id', header: 'id' },
  { accessorKey: 'file_name', header: 'file' },
  { accessorKey: 'file_type', header: 'type' },
  { accessorKey: 'service_variant_detected', header: 'variant',
    cell: ({ getValue }) => getValue() ?? <span className="text-slate-400">—</span> },
  { accessorKey: 'claim_subtype_detected', header: 'subtype',
    cell: ({ getValue }) => getValue() ?? <span className="text-slate-400">—</span> },
  { accessorKey: 'parse_status', header: 'status',
    cell: ({ getValue }) => <StatusBadge value={getValue()} /> },
  { accessorKey: 'claims_saved', header: 'saved',
    cell: ({ getValue }) => getValue() ?? '—' },
  { accessorKey: 'claims_dropped', header: 'dropped',
    cell: ({ getValue }) => getValue() ?? '—' },
  { accessorKey: 'raw_size_bytes', header: 'size (B)',
    cell: ({ getValue }) => getValue().toLocaleString() },
  { accessorKey: 'uploaded_at', header: 'uploaded_at',
    cell: ({ getValue }) => getValue()?.replace('T', ' ') },
];

function FilesTab({ onSelect }) {
  const [status, setStatus] = useState(null);
  const [variant, setVariant] = useState(null);
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');

  // Simple debounce so each keystroke doesn't fire a query
  const onSearchChange = (e) => {
    const v = e.target.value;
    setQ(v);
    clearTimeout(onSearchChange._t);
    onSearchChange._t = setTimeout(() => setDebouncedQ(v), 250);
  };

  const list = useEdiFiles({ status, variant, q: debouncedQ || undefined, limit: 100 });

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs font-mono flex-wrap">
        <span className="text-slate-500">status:</span>
        {[null, 'pending', 'parsing', 'parsed', 'partial', 'failed'].map((s) => (
          <button
            key={s ?? 'all'}
            type="button"
            onClick={() => setStatus(s)}
            className={`px-2 py-0.5 rounded border ${
              status === s
                ? 'bg-slate-900 text-white border-slate-900'
                : 'bg-white border-slate-300 hover:bg-slate-100'
            }`}
          >
            {s ?? 'all'}
          </button>
        ))}
        <span className="text-slate-500 ml-3">variant:</span>
        {[null, '837P', '837I', '837D', '835'].map((v) => (
          <button
            key={v ?? 'all'}
            type="button"
            onClick={() => setVariant(v)}
            className={`px-2 py-0.5 rounded border ${
              variant === v
                ? 'bg-slate-900 text-white border-slate-900'
                : 'bg-white border-slate-300 hover:bg-slate-100'
            }`}
          >
            {v ?? 'all'}
          </button>
        ))}
        <input
          value={q}
          onChange={onSearchChange}
          placeholder="filename contains…"
          className="ml-3 px-2 py-0.5 border border-slate-300 rounded text-xs font-mono w-48"
        />
        <span className="ml-auto text-slate-500">
          {list.data?.total ?? '…'} total · click a row to inspect
        </span>
        <RefreshButton onRefresh={list.refetch} updatedAt={list.dataUpdatedAt}
                       isFetching={list.isFetching} />
      </div>

      {list.isError && (
        <div className="text-xs font-mono text-red-700">Error: {list.error.message}</div>
      )}

      <DataTable
        data={list.data?.items}
        columns={FILE_COLUMNS}
        rowKey={(r) => r.id}
        rowOnClick={(r) => onSelect(r.id)}
        emptyMessage={list.isLoading ? 'loading…' : 'no files'}
        dense
      />
    </div>
  );
}


// ─── Parse Trace tab (drill-in panel) ──────────────────────────────────

function ParseTraceTab({ fileId, onClose }) {
  if (!fileId) {
    return (
      <div className="text-xs font-mono text-slate-500 text-center py-10">
        Select a file from the Files tab to inspect its parse trace.
      </div>
    );
  }
  return <ParseTracePanel fileId={fileId} onClose={onClose} />;
}

function ParseTracePanel({ fileId, onClose }) {
  const detail = useEdiFileDetail(fileId);
  const [segHandlerStatus, setSegHandlerStatus] = useState(null);
  const [eventType, setEventType] = useState(null);

  const segs = useEdiFileSegments(fileId, {
    handler_status: segHandlerStatus ?? undefined, limit: 500,
  });
  const events = useEdiFileEvents(fileId, {
    event_type: eventType ?? undefined, limit: 500,
  });

  return (
    <div className="border border-slate-400 rounded bg-slate-50">
      <div className="flex items-center justify-between px-3 py-2 bg-slate-200 border-b border-slate-400">
        <h3 className="font-mono text-sm font-semibold">
          edi_file #{fileId} {detail.data?.file_name ? `· ${detail.data.file_name}` : ''}
        </h3>
        <button
          type="button"
          onClick={onClose}
          className="text-xs font-mono px-2 py-0.5 rounded border border-slate-400 bg-white hover:bg-slate-100"
        >
          ✕ close
        </button>
      </div>

      <div className="p-3 space-y-4">
        {detail.isLoading && <div className="text-xs font-mono text-slate-500">loading detail…</div>}
        {detail.isError && <div className="text-xs font-mono text-red-700">Error: {detail.error.message}</div>}

        {detail.data && (
          <>
            <KVTable
              title="METADATA"
              rows={[
                ['file_type',                detail.data.file_type],
                ['parse_status',             detail.data.parse_status],
                ['parser_version',           detail.data.parser_version],
                ['implementation_guide',     detail.data.implementation_guide ?? '—'],
                ['service_variant_detected', detail.data.service_variant_detected ?? '—'],
                ['claim_subtype_detected',   detail.data.claim_subtype_detected ?? '—'],
                ['sender_id',                detail.data.sender_id ?? '—'],
                ['receiver_id',              detail.data.receiver_id ?? '—'],
                ['interchange_control_no',   detail.data.interchange_control_no ?? '—'],
                ['raw_size_bytes',           detail.data.raw_size_bytes.toLocaleString()],
                ['uploaded_at',              detail.data.uploaded_at?.replace('T', ' ')],
                ['parse_started_at',         detail.data.parse_started_at?.replace('T', ' ') ?? '—'],
                ['parse_completed_at',       detail.data.parse_completed_at?.replace('T', ' ') ?? '—'],
                ['content_hash',             detail.data.content_hash?.slice(0, 12) + '…'],
              ]}
            />

            <KVTable
              title="LIVE COUNTERS (computed from partitioned tables)"
              rows={[
                ['raw_segments_count',              detail.data.raw_segments_count.toLocaleString()],
                ['parse_events_count',              detail.data.parse_events_count.toLocaleString()],
                ['claims_persisted_count',          detail.data.claims_persisted_count.toLocaleString()],
                ['remittance_claims_persisted',     detail.data.remittance_claims_persisted_count.toLocaleString()],
              ]}
            />

            {detail.data.parse_summary && (
              <div>
                <div className="text-xs font-mono font-semibold text-slate-700 mb-1">
                  PARSE_SUMMARY (snapshot at parse time)
                </div>
                <pre className="text-[11px] font-mono p-2 bg-white border border-slate-300 rounded overflow-x-auto">
                  {JSON.stringify(detail.data.parse_summary, null, 2)}
                </pre>
              </div>
            )}
          </>
        )}

        {/* ── Segments ───────────────────────────────────────────── */}
        <div>
          <div className="flex items-center gap-2 mb-1">
            <div className="text-xs font-mono font-semibold text-slate-700">
              RAW_SEGMENTS ({segs.data?.total?.toLocaleString() ?? '…'})
            </div>
            <span className="text-[10px] font-mono text-slate-500">handler_status:</span>
            {[null, 'handled', 'skipped_unhandled', 'parse_error'].map((s) => (
              <button
                key={s ?? 'all'}
                type="button"
                onClick={() => setSegHandlerStatus(s)}
                className={`px-1.5 py-0.5 text-[10px] font-mono rounded border ${
                  segHandlerStatus === s
                    ? 'bg-slate-900 text-white border-slate-900'
                    : 'bg-white border-slate-300 hover:bg-slate-100'
                }`}
              >
                {s ?? 'all'}
              </button>
            ))}
          </div>
          <DataTable
            data={segs.data?.items}
            columns={[
              { accessorKey: 'segment_position', header: 'pos' },
              { accessorKey: 'segment_name',     header: 'seg' },
              { accessorKey: 'handler_status',   header: 'status',
                cell: ({ getValue }) => <StatusBadge value={getValue()} /> },
              { accessorKey: 'claim_id',          header: 'claim',
                cell: ({ getValue }) => getValue() ?? <span className="text-slate-400">—</span> },
              { accessorKey: 'raw_segment_text', header: 'raw',
                cell: ({ getValue }) => (
                  <code className="text-[10px] text-slate-700 break-all whitespace-pre-wrap">
                    {getValue()}
                  </code>
                ),
              },
            ]}
            rowKey={(r) => r.id}
            emptyMessage={segs.isLoading ? 'loading…' : 'no segments match'}
            dense
          />
        </div>

        {/* ── Events ─────────────────────────────────────────────── */}
        <div>
          <div className="flex items-center gap-2 mb-1">
            <div className="text-xs font-mono font-semibold text-slate-700">
              PARSE_EVENTS ({events.data?.total?.toLocaleString() ?? '…'})
            </div>
            <span className="text-[10px] font-mono text-slate-500">event_type:</span>
            {[null, 'segment_handled', 'segment_skipped', 'validator_error',
              'validator_warning', 'claim_dropped', 'parse_error'].map((t) => (
              <button
                key={t ?? 'all'}
                type="button"
                onClick={() => setEventType(t)}
                className={`px-1.5 py-0.5 text-[10px] font-mono rounded border ${
                  eventType === t
                    ? 'bg-slate-900 text-white border-slate-900'
                    : 'bg-white border-slate-300 hover:bg-slate-100'
                }`}
              >
                {t ?? 'all'}
              </button>
            ))}
          </div>
          <DataTable
            data={events.data?.items}
            columns={[
              { accessorKey: 'segment_position', header: 'pos',
                cell: ({ getValue }) => getValue() ?? '' },
              { accessorKey: 'event_type', header: 'event',
                cell: ({ getValue }) => <StatusBadge value={getValue()} /> },
              { accessorKey: 'segment_name', header: 'seg',
                cell: ({ getValue }) => getValue() ?? '' },
              { accessorKey: 'claim_number', header: 'claim#',
                cell: ({ getValue }) => getValue() ?? '' },
              { accessorKey: 'details', header: 'details',
                cell: ({ getValue }) => (
                  <code className="text-[10px] text-slate-700 break-all whitespace-pre-wrap">
                    {getValue() ? JSON.stringify(getValue()) : ''}
                  </code>
                ),
              },
            ]}
            rowKey={(r) => r.id}
            emptyMessage={events.isLoading ? 'loading…' : 'no events match'}
            dense
          />
        </div>
      </div>
    </div>
  );
}


// ─── Page shell ─────────────────────────────────────────────────────────

export default function EdiInspectorPage() {
  const [selectedFileId, setSelectedFileId] = useState(null);
  const [activeTabIndex, setActiveTabIndex] = useState(0);

  // When a row is clicked in Files tab, switch to Parse Trace tab
  const selectAndSwitch = (id) => {
    setSelectedFileId(id);
    setActiveTabIndex(2);
  };

  return (
    <div>
      <h1 className="text-xl font-mono font-semibold text-slate-900 mb-3">EDI Inspector</h1>
      <Tabs
        key={activeTabIndex}     // remount to honour defaultIndex change
        defaultIndex={activeTabIndex}
        tabs={[
          { id: 'upload', label: 'Upload',
            render: () => <UploadTab onUploadedFileId={selectAndSwitch} /> },
          { id: 'files',  label: 'Files',
            render: () => <FilesTab onSelect={selectAndSwitch} /> },
          { id: 'trace',  label: `Parse Trace${selectedFileId ? ` · #${selectedFileId}` : ''}`,
            render: () => (
              <ParseTraceTab
                fileId={selectedFileId}
                onClose={() => setSelectedFileId(null)}
              />
            ) },
        ]}
      />
    </div>
  );
}
