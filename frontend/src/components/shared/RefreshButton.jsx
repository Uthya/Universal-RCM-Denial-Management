/**
 * Manual refetch button with last-refresh timestamp.
 *
 * react-query's `refetch` returns a promise — pass it directly:
 *   <RefreshButton onRefresh={query.refetch} updatedAt={query.dataUpdatedAt} />
 */

import { formatDistanceToNow } from 'date-fns';

export default function RefreshButton({ onRefresh, updatedAt, isFetching }) {
  return (
    <div className="inline-flex items-center gap-2 text-xs text-slate-500">
      {updatedAt && (
        <span title={new Date(updatedAt).toISOString()}>
          updated {formatDistanceToNow(new Date(updatedAt), { addSuffix: true })}
        </span>
      )}
      <button
        type="button"
        onClick={() => onRefresh && onRefresh()}
        disabled={isFetching}
        className="px-2 py-1 rounded border border-slate-300 bg-white hover:bg-slate-100 disabled:opacity-50 font-mono"
      >
        {isFetching ? '…' : '↻ refresh'}
      </button>
    </div>
  );
}
