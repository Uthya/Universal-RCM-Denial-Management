/**
 * Page 3 — Claims Browser.
 *
 * Two surfaces:
 *   - List view: filterable / searchable claims table
 *   - Detail panel (drill-in): 9 tabs per the v2 UI plan §3 Page 3
 *       1. Overview
 *       2. Lines
 *       3. Diagnoses
 *       4. Patient
 *       5. Provider
 *       6. Variant Data    (JSONB)
 *       7. Remits          (835 responses + adjustments count)
 *       8. Parse Events    (reuses /api/dev/edi/files/{file_id}/events)
 *       9. Raw Segments    (reuses /api/dev/edi/files/{file_id}/segments)
 *
 * Per CR-041: every count and join is computed server-side. The page renders
 * only — it does NOT compute denial rates, line totals, or any aggregate.
 */

import { useState } from 'react';
import KVTable from '../components/shared/KVTable';
import DataTable from '../components/shared/DataTable';
import RefreshButton from '../components/shared/RefreshButton';
import StatusBadge from '../components/shared/StatusBadge';
import Tabs from '../components/shared/Tabs';
import {
  useClaimDetail,
  useClaimDiagnoses,
  useClaimLines,
  useClaimRemits,
  useClaims,
  useEdiFileEvents,
  useEdiFileSegments,
} from '../services/queries';


// ─── List view ──────────────────────────────────────────────────────────

const CLAIM_COLUMNS = [
  { accessorKey: 'id', header: 'id' },
  { accessorKey: 'claim_number', header: 'claim#' },
  { accessorKey: 'service_variant', header: 'var' },
  { accessorKey: 'claim_subtype', header: 'subtype' },
  { accessorKey: 'claim_status', header: 'status',
    cell: ({ getValue }) => <StatusBadge value={getValue()} /> },
  { accessorKey: 'total_charge_amount', header: 'charge',
    cell: ({ getValue }) => `$${getValue().toFixed(2)}` },
  { accessorKey: 'service_from_date', header: 'svc date',
    cell: ({ getValue }) => getValue() ?? <span className="text-slate-400">—</span> },
  { accessorKey: 'payer_name', header: 'payer',
    cell: ({ getValue, row }) => getValue() ?? (row.original.payer_id ? `#${row.original.payer_id}` : '—') },
  { accessorKey: 'line_count', header: 'lines' },
  { accessorKey: 'diagnosis_count', header: 'dx' },
  { accessorKey: 'has_remittance', header: 'remit?',
    cell: ({ getValue }) => getValue() ? 'yes' : '' },
];

function ClaimsListView({ onSelect }) {
  const [variant, setVariant] = useState(null);
  const [subtype, setSubtype] = useState(null);
  const [status, setStatus] = useState(null);
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');

  const onSearchChange = (e) => {
    const v = e.target.value;
    setQ(v);
    clearTimeout(onSearchChange._t);
    onSearchChange._t = setTimeout(() => setDebouncedQ(v), 250);
  };

  const list = useClaims({
    variant, subtype, claim_status: status,
    q: debouncedQ || undefined, limit: 100,
  });

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs font-mono flex-wrap">
        <span className="text-slate-500">variant:</span>
        {[null, '837P', '837I', '837D'].map((v) => (
          <button key={v ?? 'all'} type="button"
                  onClick={() => { setVariant(v); setSubtype(null); }}
                  className={chip(variant === v)}>{v ?? 'all'}</button>
        ))}
        <span className="text-slate-500 ml-3">status:</span>
        {[null, 'submitted', 'accepted', 'denied', 'paid', 'partial', 'pending'].map((s) => (
          <button key={s ?? 'all'} type="button" onClick={() => setStatus(s)}
                  className={chip(status === s)}>{s ?? 'all'}</button>
        ))}
        <input
          value={q}
          onChange={onSearchChange}
          placeholder="claim# contains…"
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
        columns={CLAIM_COLUMNS}
        rowKey={(r) => r.id}
        rowOnClick={(r) => onSelect(r.id)}
        emptyMessage={list.isLoading ? 'loading…' : 'no claims match'}
        dense
      />
    </div>
  );
}

const chip = (active) =>
  `px-2 py-0.5 rounded border ${
    active ? 'bg-slate-900 text-white border-slate-900'
           : 'bg-white border-slate-300 hover:bg-slate-100'
  }`;


// ─── Detail panel — 9 tabs ──────────────────────────────────────────────

function ClaimDetailPanel({ claimId, onClose }) {
  const detail = useClaimDetail(claimId);

  return (
    <div className="border border-slate-400 rounded bg-slate-50">
      <div className="flex items-center justify-between px-3 py-2 bg-slate-200 border-b border-slate-400">
        <h3 className="font-mono text-sm font-semibold">
          claim #{claimId} {detail.data?.claim_number ? `· ${detail.data.claim_number}` : ''}
        </h3>
        <button
          type="button"
          onClick={onClose}
          className="text-xs font-mono px-2 py-0.5 rounded border border-slate-400 bg-white hover:bg-slate-100"
        >
          ✕ close
        </button>
      </div>

      {detail.isLoading && <div className="p-3 text-xs font-mono text-slate-500">loading…</div>}
      {detail.isError && <div className="p-3 text-xs font-mono text-red-700">Error: {detail.error.message}</div>}

      {detail.data && (
        <div className="p-2">
          <Tabs tabs={[
            { id: 'overview',  label: 'Overview',
              render: () => <OverviewTab d={detail.data} /> },
            { id: 'lines',     label: `Lines (${detail.data.line_count})`,
              render: () => <LinesTab claimId={claimId} /> },
            { id: 'diag',      label: `Diagnoses (${detail.data.diagnosis_count})`,
              render: () => <DiagnosesTab claimId={claimId} /> },
            { id: 'patient',   label: 'Patient',
              render: () => <PatientTab d={detail.data} /> },
            { id: 'provider',  label: 'Provider',
              render: () => <ProviderTab d={detail.data} /> },
            { id: 'variant',   label: 'Variant Data',
              render: () => <VariantDataTab d={detail.data} /> },
            { id: 'remits',    label: `Remits (${detail.data.remittance_count})`,
              render: () => <RemitsTab claimId={claimId} /> },
            { id: 'events',    label: `Events (${detail.data.parse_event_count})`,
              render: () => <EventsTab fileId={detail.data.edi_file_id}
                                       claimNumber={detail.data.claim_number} /> },
            { id: 'raw',       label: `Raw Segments (${detail.data.raw_segment_count})`,
              render: () => <RawSegmentsTab fileId={detail.data.edi_file_id}
                                            claimId={claimId} /> },
          ]} />
        </div>
      )}
    </div>
  );
}


// ─── Detail sub-tabs ────────────────────────────────────────────────────

function OverviewTab({ d }) {
  return (
    <div className="space-y-3">
      <KVTable title="IDENTITY" rows={[
        ['claim_number',       d.claim_number],
        ['service_variant',    d.service_variant],
        ['claim_subtype',      d.claim_subtype],
        ['claim_status',       d.claim_status],
        ['total_charge',       `$${d.total_charge_amount.toFixed(2)}`],
        ['facility_type_code', d.facility_type_code ?? '—'],
        ['frequency_code',     d.frequency_code ?? '—'],
      ]} />
      <KVTable title="DATES" rows={[
        ['service_from_date',  d.service_from_date ?? '—'],
        ['service_to_date',    d.service_to_date ?? '—'],
        ['submission_date',    d.submission_date],
      ]} />
      <KVTable title="AUTHORIZATIONS" rows={[
        ['authorization_number',           d.authorization_number ?? '—'],
        ['referral_number',                d.referral_number ?? '—'],
        ['previous_payer_claim_control_no', d.previous_payer_claim_control_no ?? '—'],
      ]} />
      <KVTable title="LINKED ROWS" rows={[
        ['edi_file_id',       d.edi_file_id],
        ['payer_id',          d.payer_id ?? '—'],
        ['patient_id',        d.patient_id ?? '—'],
        ['subscriber_id',     d.subscriber_id ?? '—'],
      ]} />
      {d.raw_claim_segment && (
        <div>
          <div className="text-xs font-mono font-semibold text-slate-700 mb-1">RAW CLM SEGMENT</div>
          <pre className="text-[11px] font-mono p-2 bg-white border border-slate-300 rounded whitespace-pre-wrap break-all">
            {d.raw_claim_segment}
          </pre>
        </div>
      )}
    </div>
  );
}

function LinesTab({ claimId }) {
  const q = useClaimLines(claimId);
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorLine err={q.error} />;
  return (
    <DataTable
      data={q.data.items}
      columns={[
        { accessorKey: 'line_number', header: '#' },
        { accessorKey: 'procedure_code', header: 'proc' },
        { id: 'modifiers', header: 'mods',
          cell: ({ row }) => [row.original.modifier1, row.original.modifier2,
                              row.original.modifier3, row.original.modifier4]
                              .filter(Boolean).join(',') || '' },
        { accessorKey: 'billed_amount', header: 'billed',
          cell: ({ getValue }) => getValue() != null ? `$${getValue().toFixed(2)}` : '' },
        { accessorKey: 'units', header: 'units',
          cell: ({ getValue }) => getValue() ?? '' },
        { accessorKey: 'place_of_service', header: 'POS' },
        { accessorKey: 'service_date', header: 'svc date',
          cell: ({ getValue }) => getValue() ?? '' },
        { accessorKey: 'revenue_code', header: 'rev' },
        { accessorKey: 'hipps_code', header: 'hipps' },
        { accessorKey: 'tooth_number', header: 'tooth' },
        { accessorKey: 'ndc_drug_code', header: 'NDC' },
        { id: 'dx_ptrs', header: 'dx ptrs',
          cell: ({ row }) => (row.original.diagnosis_pointers ?? []).join(',') },
      ]}
      rowKey={(r) => r.id}
      emptyMessage="no claim_lines for this claim"
      dense
    />
  );
}

function DiagnosesTab({ claimId }) {
  const q = useClaimDiagnoses(claimId);
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorLine err={q.error} />;
  return (
    <DataTable
      data={q.data.items}
      columns={[
        { accessorKey: 'sequence_number', header: 'seq' },
        { accessorKey: 'diagnosis_code', header: 'code' },
        { accessorKey: 'diagnosis_type', header: 'type' },
        { accessorKey: 'diagnosis_qualifier', header: 'qual' },
        { accessorKey: 'present_on_admission', header: 'POA' },
      ]}
      rowKey={(r) => r.id}
      emptyMessage="no diagnoses recorded"
      dense
    />
  );
}

function PatientTab({ d }) {
  return (
    <KVTable rows={[
      ['patient_id',        d.patient_id ?? '—'],
      ['patient_member_id', d.patient_member_id ?? '—'],
      ['subscriber_id',     d.subscriber_id ?? '—'],
    ]} />
  );
}

function ProviderTab({ d }) {
  return (
    <KVTable rows={[
      ['billing_provider_id',    d.billing_provider_id ?? '—'],
      ['billing_provider_npi',   d.billing_provider_npi ?? '—'],
      ['rendering_provider_id',  d.rendering_provider_id ?? '—'],
      ['rendering_provider_npi', d.rendering_provider_npi ?? '—'],
      ['referring_provider_id',  d.referring_provider_id ?? '—'],
    ]} />
  );
}

function VariantDataTab({ d }) {
  if (!d.variant_data) {
    return (
      <div className="text-xs font-mono text-slate-500 text-center py-6">
        no variant_data JSONB for this claim
      </div>
    );
  }
  return (
    <pre className="text-[11px] font-mono p-3 bg-white border border-slate-300 rounded overflow-auto max-h-[60vh]">
      {JSON.stringify(d.variant_data, null, 2)}
    </pre>
  );
}

function RemitsTab({ claimId }) {
  const q = useClaimRemits(claimId);
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorLine err={q.error} />;
  return (
    <DataTable
      data={q.data.items}
      columns={[
        { accessorKey: 'id', header: 'id' },
        { accessorKey: 'claim_status_code', header: 'CLP02' },
        { accessorKey: 'billed_amount', header: 'billed',
          cell: ({ getValue }) => `$${getValue().toFixed(2)}` },
        { accessorKey: 'paid_amount', header: 'paid',
          cell: ({ getValue }) => `$${getValue().toFixed(2)}` },
        { accessorKey: 'patient_responsibility_amount', header: 'patient resp',
          cell: ({ getValue }) => getValue() != null ? `$${getValue().toFixed(2)}` : '' },
        { accessorKey: 'remittance_date', header: 'remit date',
          cell: ({ getValue }) => getValue() ?? <span className="text-slate-400">—</span> },
        { accessorKey: 'payer_paid_date', header: 'paid date',
          cell: ({ getValue }) => getValue() ?? '' },
        { accessorKey: 'adjustment_count', header: 'adj cnt' },
        { accessorKey: 'edi_file_id', header: '835 file' },
      ]}
      rowKey={(r) => r.id}
      emptyMessage="no remits — claim has not been adjudicated yet"
      dense
    />
  );
}

function EventsTab({ fileId, claimNumber }) {
  // Server endpoint returns ALL events for the file; we filter client-side by
  // claim_number for the per-claim view. Backend hands us the full set so the
  // tab can show envelope-level events too (no claim_number).
  const q = useEdiFileEvents(fileId, { limit: 1000 });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorLine err={q.error} />;
  const items = q.data.items.filter(
    (e) => e.claim_number === claimNumber || e.claim_number == null,
  );
  return (
    <DataTable
      data={items}
      columns={[
        { accessorKey: 'segment_position', header: 'pos',
          cell: ({ getValue }) => getValue() ?? '' },
        { accessorKey: 'event_type', header: 'event',
          cell: ({ getValue }) => <StatusBadge value={getValue()} /> },
        { accessorKey: 'segment_name', header: 'seg',
          cell: ({ getValue }) => getValue() ?? '' },
        { accessorKey: 'claim_number', header: 'claim#',
          cell: ({ getValue }) => getValue() ?? <span className="text-slate-400">(envelope)</span> },
        { accessorKey: 'details', header: 'details',
          cell: ({ getValue }) => (
            <code className="text-[10px] text-slate-700 break-all whitespace-pre-wrap">
              {getValue() ? JSON.stringify(getValue()) : ''}
            </code>
          ) },
      ]}
      rowKey={(r) => r.id}
      emptyMessage="no events"
      dense
    />
  );
}

function RawSegmentsTab({ fileId, claimId }) {
  const q = useEdiFileSegments(fileId, { limit: 1000 });
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorLine err={q.error} />;
  const items = q.data.items.filter((s) => s.claim_id === claimId);
  return (
    <DataTable
      data={items}
      columns={[
        { accessorKey: 'segment_position', header: 'pos' },
        { accessorKey: 'segment_name',     header: 'seg' },
        { accessorKey: 'handler_status',   header: 'status',
          cell: ({ getValue }) => <StatusBadge value={getValue()} /> },
        { accessorKey: 'raw_segment_text', header: 'raw',
          cell: ({ getValue }) => (
            <code className="text-[10px] text-slate-700 break-all whitespace-pre-wrap">
              {getValue()}
            </code>
          ) },
      ]}
      rowKey={(r) => r.id}
      emptyMessage="no raw_segments bound to this claim"
      dense
    />
  );
}


// ─── Helpers ────────────────────────────────────────────────────────────

function Loading() {
  return <div className="text-xs font-mono text-slate-500">loading…</div>;
}
function ErrorLine({ err }) {
  return <div className="text-xs font-mono text-red-700">Error: {err?.message}</div>;
}


// ─── Page shell ─────────────────────────────────────────────────────────

export default function ClaimsPage() {
  const [selectedId, setSelectedId] = useState(null);
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-mono font-semibold text-slate-900">Claims</h1>
      <ClaimsListView onSelect={setSelectedId} />
      {selectedId && (
        <ClaimDetailPanel
          claimId={selectedId}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  );
}
