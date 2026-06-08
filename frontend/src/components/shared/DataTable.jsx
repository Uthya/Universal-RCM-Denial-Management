/**
 * Generic data table built on @tanstack/react-table.
 *
 * Backend is the source of truth for sort + pagination (server-side); this
 * table just renders. The exception is small in-memory tables (function
 * inventory, MV list) where client-side sort is fine — pass
 * `serverPagination={false}` for those.
 *
 * Per CR-041: this component renders ONLY. It does not transform values,
 * compute aggregates, or apply business logic.
 */

import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table';

export default function DataTable({
  data,
  columns,
  emptyMessage = 'no rows',
  rowOnClick,
  rowKey,
  dense = false,
}) {
  const table = useReactTable({
    data: data ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const padding = dense ? 'py-1' : 'py-2';

  if (!data || data.length === 0) {
    return (
      <div className="text-xs font-mono text-slate-500 px-3 py-4 text-center border border-dashed border-slate-300 rounded">
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto border border-slate-300 rounded">
      <table className="w-full text-xs font-mono">
        <thead className="bg-slate-100 border-b border-slate-300 text-slate-700">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => (
                <th
                  key={h.id}
                  className={`text-left px-3 ${padding} font-normal cursor-pointer select-none whitespace-nowrap`}
                  onClick={h.column.getToggleSortingHandler()}
                >
                  {flexRender(h.column.columnDef.header, h.getContext())}
                  {{
                    asc: ' ▲',
                    desc: ' ▼',
                  }[h.column.getIsSorted()] ?? null}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row, i) => (
            <tr
              key={rowKey ? rowKey(row.original) : row.id}
              className={`${i % 2 === 0 ? 'bg-white' : 'bg-slate-50'} ${
                rowOnClick ? 'cursor-pointer hover:bg-blue-50' : ''
              }`}
              onClick={() => rowOnClick && rowOnClick(row.original)}
            >
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className={`px-3 ${padding} align-top whitespace-nowrap`}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
