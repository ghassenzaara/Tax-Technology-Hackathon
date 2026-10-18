import { formatScalar, isEmptyValue, isPlainObject } from '../../lib/format'

// Structured facts stay structured. Text is always plain text, never HTML.

export function EmptyValue() {
  return <span className="text-warning">Eingabe erforderlich</span>
}

export function FactValue({ value, type, unit, label }: { value: unknown; type?: string; unit?: string; label?: string }) {
  if (isEmptyValue(value)) return <EmptyValue />

  if (Array.isArray(value)) {
    if (value.every(isPlainObject)) {
      return value.length === 1 ? <RecordDetails value={value[0]} /> : <RowTable rows={value} />
    }
    return <ul className="space-y-1">
      {value.map((item, i) => <li key={i} className="flex items-start gap-2 text-sm">
        <span className="mt-2 size-1 shrink-0 rounded-full bg-ink-subtle" aria-hidden />
        <FactValue value={item} label={label} />
      </li>)}
    </ul>
  }

  if (isPlainObject(value)) {
    return <RecordDetails value={value} />
  }

  return <span className="whitespace-pre-line break-words leading-relaxed">{formatScalar(value, type, unit, label)}</span>
}

function RecordDetails({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value).filter(([, v]) => !isEmptyValue(v))
  if (entries.length === 0) return <EmptyValue />
  return <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
    {entries.map(([key, cell]) => <div key={key} className={typeof cell === 'string' && cell.length > 100 ? 'min-w-0 sm:col-span-2' : 'min-w-0'}>
      <dt className="mb-0.5 text-xs font-normal text-ink-muted">{key}</dt>
      <dd className="text-sm font-normal text-ink"><FactValue value={cell} label={key} /></dd>
    </div>)}
  </dl>
}

function RowTable({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = [...new Set(rows.flatMap((r) => Object.keys(r)))].filter((c) => rows.some((r) => !isEmptyValue(r[c])))
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
                <td key={c} className="min-w-24 max-w-md px-3 py-2 align-top">
                  {isEmptyValue(row[c]) ? <span className="text-ink-subtle">—</span> : <FactValue value={row[c]} label={c} />}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
