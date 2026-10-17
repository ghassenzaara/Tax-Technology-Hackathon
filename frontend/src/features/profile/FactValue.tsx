import { formatScalar, isEmptyValue, isPlainObject } from '../../lib/format'

// Renders any value generically. Text is always rendered as plain text, never HTML.

export function EmptyValue() {
  return <span className="text-ink-subtle italic">Nicht angegeben</span>
}

export function FactValue({ value, type, unit, label }: { value: unknown; type?: string; unit?: string; label?: string }) {
  if (isEmptyValue(value)) return <EmptyValue />

  if (Array.isArray(value) && value.length > 0 && value.every(isPlainObject)) {
    return <RowTable rows={value as Record<string, unknown>[]} />
  }

  if (isPlainObject(value)) {
    return (
      <dl className="mt-0.5 space-y-1 border-l-2 border-line pl-3">
        {Object.entries(value).map(([k, v]) => (
          <div key={k} className="text-sm">
            <dt className="inline text-ink-muted">{k}: </dt>
            <dd className="inline">
              <FactValue value={v} label={k} />
            </dd>
          </div>
        ))}
      </dl>
    )
  }

  return <span className="break-words">{formatScalar(value, type, unit, label)}</span>
}

function RowTable({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = [...new Set(rows.flatMap((r) => Object.keys(r)))]
  return (
    <div className="mt-1 overflow-x-auto rounded-lg border border-line">
      <table className="w-full text-left text-xs">
        <thead className="bg-canvas text-ink-muted">
          <tr>
            {columns.map((c) => (
              <th key={c} scope="col" className="px-2.5 py-1.5 font-medium whitespace-nowrap">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c} className={typeof row[c] === 'number' ? 'px-2.5 py-1.5 text-right tabular-nums' : 'px-2.5 py-1.5'}>
                  {isPlainObject(row[c]) || Array.isArray(row[c]) ? <FactValue value={row[c]} label={c} /> : formatScalar(row[c], undefined, undefined, c)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
