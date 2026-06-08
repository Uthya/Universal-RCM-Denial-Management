/**
 * Single big-number card with a label + optional sublabel.
 * Used on the Home dashboard.
 */

export default function MetricTile({ label, value, sublabel, tone = 'default', isLoading }) {
  const toneClass = {
    default: 'border-slate-300 bg-white',
    success: 'border-green-300 bg-green-50',
    warning: 'border-amber-300 bg-amber-50',
    error:   'border-red-300 bg-red-50',
  }[tone] ?? 'border-slate-300 bg-white';

  return (
    <div className={`rounded border ${toneClass} px-4 py-3`}>
      <div className="text-[10px] font-mono uppercase text-slate-500">{label}</div>
      <div className="text-2xl font-mono font-semibold text-slate-900 mt-1">
        {isLoading ? <span className="text-slate-300">…</span> : value ?? '—'}
      </div>
      {sublabel && (
        <div className="text-[11px] text-slate-500 mt-1 font-mono">{sublabel}</div>
      )}
    </div>
  );
}
