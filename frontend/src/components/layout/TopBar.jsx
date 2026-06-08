/**
 * Top bar — always visible.
 *
 * Shows: app title, current environment (LOCAL green / REMOTE orange),
 * alembic head, live indicator (DB connection polled every 5s), PHI toggle.
 */

import { useEnvInfo, useHealth } from '../../services/queries';
import { usePhi } from '../../services/phi';

function envColor(urlRedacted) {
  // Heuristic: remote = orange, localhost = green
  if (!urlRedacted) return 'bg-gray-400';
  if (/localhost|127\.0\.0\.1/.test(urlRedacted)) return 'bg-green-600';
  return 'bg-orange-600';
}

function envLabel(urlRedacted) {
  if (!urlRedacted) return 'unknown';
  if (/localhost|127\.0\.0\.1/.test(urlRedacted)) return 'LOCAL';
  // Pull the host:port/db tail
  const m = urlRedacted.match(/@([^/]+)\/([^?]+)/);
  return m ? `REMOTE ${m[1]}/${m[2]}` : 'REMOTE';
}

export default function TopBar() {
  const env = useEnvInfo();
  const health = useHealth();
  const { showPhi, togglePhi } = usePhi();

  const urlRedacted = env.data?.database?.url_redacted;
  const alembicHead = env.data?.database?.alembic_head ?? '—';
  const dbUp = health.data?.database === 'up';

  return (
    <header className="fixed top-0 left-0 right-0 z-30 bg-slate-900 text-slate-100 border-b border-slate-700">
      <div className="flex items-center justify-between px-4 py-2">
        <div className="flex items-center gap-3">
          <span className="font-mono font-bold text-sm">▣ rcm/v2 dev console</span>
          <span className="text-xs text-slate-400">(developer-only · NOT for clinical use)</span>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span
            className={`inline-flex items-center gap-2 px-2 py-1 rounded ${envColor(urlRedacted)} text-white`}
            title={urlRedacted}
          >
            <span className="font-mono">{envLabel(urlRedacted)}</span>
          </span>
          <span className="font-mono text-slate-300">
            alembic=<span className="text-slate-100">{alembicHead}</span>
          </span>
          <span
            className={`inline-flex items-center gap-1 px-2 py-1 rounded border ${
              dbUp ? 'border-green-500 text-green-300' : 'border-red-500 text-red-300'
            }`}
            title={`DB ${health.data?.database ?? '…'}`}
          >
            <span className={`w-2 h-2 rounded-full ${dbUp ? 'bg-green-400 animate-pulse' : 'bg-red-400'}`}></span>
            live
          </span>
          <button
            type="button"
            onClick={togglePhi}
            className={`px-2 py-1 rounded font-mono border ${
              showPhi
                ? 'bg-red-700 border-red-500 text-white'
                : 'bg-slate-800 border-slate-600 text-slate-300'
            }`}
            title="Toggle PHI visibility (off = patient names / member IDs masked)"
          >
            PHI:{showPhi ? 'ON' : 'OFF'}
          </button>
        </div>
      </div>
    </header>
  );
}
