/**
 * Sidebar — left-rail nav.
 *
 * Order matches UI-plan §2: ingest → inspect → analyze → operate.
 * Pages not built yet are visible-but-disabled so the user sees the roadmap.
 */

import { NavLink } from 'react-router-dom';

const NAV = [
  { to: '/',           label: 'Home',         enabled: true,  hint: 'overview · health · recent activity' },
  { to: '/edi',        label: 'EDI Inspector', enabled: true , hint: 'upload + parse traces' },
  { to: '/claims',     label: 'Claims',       enabled: true , hint: 'browse + drill into one' },
  { to: '/telemetry',  label: 'Parsing',      enabled: true , hint: 'drop rates · unhandled segments' },
  { to: '/ml',         label: 'ML Models',    enabled: false, hint: 'per-variant registry' },
  { to: '/predictions', label: 'Predictions', enabled: false, hint: 'inspect + re-run' },
  { to: '/features',   label: 'Features',     enabled: false, hint: 'feature catalog · encoders' },
  { to: '/rag',        label: 'RAG',          enabled: false, hint: 'knowledge · retrieval · gen log' },
  { to: '/db',         label: 'Database',     enabled: true,  hint: 'tables · MVs · SQL console' },
  { to: '/jobs',       label: 'Jobs',         enabled: false, hint: 'arq queue · workers' },
  { to: '/audit',      label: 'Audit',        enabled: false, hint: 'audit_log viewer' },
  { to: '/env',        label: 'Environment',  enabled: true,  hint: '.env · versions · PG settings' },
];

function linkClasses({ isActive }) {
  return [
    'block px-3 py-2 text-sm font-mono rounded',
    isActive
      ? 'bg-slate-700 text-white'
      : 'text-slate-300 hover:bg-slate-800',
  ].join(' ');
}

export default function Sidebar() {
  return (
    <nav className="fixed top-10 left-0 bottom-0 w-56 bg-slate-900 text-slate-300 border-r border-slate-700 overflow-y-auto">
      <ul className="py-3 px-2 space-y-1">
        {NAV.map((item) =>
          item.enabled ? (
            <li key={item.to}>
              <NavLink to={item.to} end={item.to === '/'} className={linkClasses}>
                <div>{item.label}</div>
                <div className="text-[10px] text-slate-500 leading-tight">{item.hint}</div>
              </NavLink>
            </li>
          ) : (
            <li key={item.to}>
              <div
                className="block px-3 py-2 text-sm font-mono rounded text-slate-600 cursor-not-allowed"
                title={`${item.label} — not yet wired in this batch`}
              >
                <div>{item.label}</div>
                <div className="text-[10px] text-slate-700 leading-tight">{item.hint} · soon</div>
              </div>
            </li>
          )
        )}
      </ul>
    </nav>
  );
}
