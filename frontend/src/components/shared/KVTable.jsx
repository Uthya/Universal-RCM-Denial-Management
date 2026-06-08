/**
 * Two-column read-only key/value table.
 *
 * Used wherever we need to display a flat dict (Env / Claim header /
 * Prediction header). `rows` is an array of [label, value] tuples OR
 * an object — both accepted.
 *
 * Values can be strings, numbers, booleans, or a render fn.
 */

export default function KVTable({ rows, title }) {
  const entries = Array.isArray(rows) ? rows : Object.entries(rows ?? {});
  return (
    <div className="overflow-hidden border border-slate-300 rounded">
      {title && (
        <div className="px-3 py-1.5 bg-slate-100 border-b border-slate-300 text-xs font-mono font-semibold text-slate-700">
          {title}
        </div>
      )}
      <table className="w-full text-xs">
        <tbody>
          {entries.map(([k, v], i) => (
            <tr key={i} className={i % 2 === 0 ? 'bg-white' : 'bg-slate-50'}>
              <td className="px-3 py-1 text-slate-500 font-mono align-top w-1/3">{k}</td>
              <td className="px-3 py-1 font-mono text-slate-900 break-all">
                {typeof v === 'function'
                  ? v()
                  : v === null || v === undefined
                  ? <span className="text-slate-400">—</span>
                  : typeof v === 'boolean'
                  ? String(v)
                  : String(v)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
