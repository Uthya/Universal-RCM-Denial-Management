/**
 * Color-coded chip for status / severity values. Used everywhere.
 *
 * Variants follow the same vocab the backend uses:
 *   success / installed / ok / handled / paid / parsed / up
 *   warning / available / partial / dropped
 *   error / failed / denied / parse_error / not_available / down
 *   info / pending / queued
 */

const VARIANT_CLASSES = {
  success: 'bg-green-100 text-green-800 border-green-300',
  warning: 'bg-amber-100 text-amber-800 border-amber-300',
  error:   'bg-red-100 text-red-800 border-red-300',
  info:    'bg-blue-100 text-blue-800 border-blue-300',
  neutral: 'bg-slate-100 text-slate-700 border-slate-300',
};

function inferVariant(value) {
  if (value === null || value === undefined) return 'neutral';
  const s = String(value).toLowerCase();
  if (['ok', 'installed', 'up', 'handled', 'paid', 'parsed', 'success', 'succeeded'].includes(s)) {
    return 'success';
  }
  if (['warning', 'partial', 'available', 'pending', 'dropped'].includes(s)) {
    return 'warning';
  }
  if (['error', 'failed', 'denied', 'parse_error', 'not_available', 'down', 'cancelled', 'validator_dropped'].includes(s)) {
    return 'error';
  }
  if (['info', 'queued', 'running', 'segment_skipped'].includes(s)) {
    return 'info';
  }
  return 'neutral';
}

export default function StatusBadge({ value, variant, children }) {
  const v = variant ?? inferVariant(value);
  const cls = VARIANT_CLASSES[v] ?? VARIANT_CLASSES.neutral;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs font-mono rounded border ${cls}`}>
      {children ?? String(value ?? '—')}
    </span>
  );
}
