/**
 * Monospace block with optional copy button. Used for SQL definitions,
 * raw EDI segments, etc.
 */

import { useState } from 'react';

export default function CodeBlock({ text, language = 'text', className = '' }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(text ?? '');
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* ignore */
    }
  };
  return (
    <div className={`relative bg-slate-900 text-slate-100 rounded text-xs ${className}`}>
      {text ? (
        <pre className="overflow-x-auto p-3 font-mono whitespace-pre-wrap break-all">{text}</pre>
      ) : (
        <div className="px-3 py-2 text-slate-500 font-mono">—</div>
      )}
      {text && (
        <button
          type="button"
          onClick={onCopy}
          className="absolute top-2 right-2 text-[10px] px-2 py-0.5 rounded bg-slate-700 text-slate-200 hover:bg-slate-600"
          title="Copy to clipboard"
        >
          {copied ? '✓ copied' : 'copy'}
        </button>
      )}
    </div>
  );
}
