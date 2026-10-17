// Display formatting only. Values stay plain JSON; formatted strings are never parsed back.

const numberFormat = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 6 })
const YEAR_KEY = /jahr|year/i
const euroFormat = new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR', maximumFractionDigits: 2 })
const dateTimeFormat = new Intl.DateTimeFormat('de-DE', {
  dateStyle: 'medium',
  timeStyle: 'short',
  timeZone: 'Europe/Berlin',
})

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/

/** `key` is the field label or object key; years are shown without grouping (2025, not 2.025). */
export function formatNumber(value: number, unit?: string, key?: string): string {
  if (unit === 'EUR') return euroFormat.format(value)
  if (!unit && key && YEAR_KEY.test(key) && Number.isInteger(value)) return String(value)
  const n = numberFormat.format(value)
  return unit ? `${n} ${unit}` : n
}

/** ISO date (2023-12-31) to 31.12.2023, without time zone shifts. */
export function formatDate(iso: string): string {
  const m = ISO_DATE.exec(iso.slice(0, 10))
  return m ? `${m[3]}.${m[2]}.${m[1]}` : iso
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : dateTimeFormat.format(d)
}

export function isEmptyValue(value: unknown): boolean {
  return value === undefined || value === null || value === '' || (Array.isArray(value) && value.length === 0)
}

/** One line of text for any scalar. Structured values fall back to compact JSON. */
export function formatScalar(value: unknown, type?: string, unit?: string, key?: string): string {
  if (isEmptyValue(value)) return ''
  if (typeof value === 'boolean') return value ? 'Ja' : 'Nein'
  if (typeof value === 'number') return formatNumber(value, unit, key)
  if (typeof value === 'string') {
    if (type === 'date' || ISO_DATE.test(value)) return formatDate(value)
    return unit && (type === 'string' || type === 'enum') ? `${value} ${unit}` : value
  }
  if (Array.isArray(value) && value.every((v) => typeof v !== 'object' || v === null)) {
    return value.map((v) => formatScalar(v)).join(', ')
  }
  return JSON.stringify(value)
}

export function formatPercent(fraction: number): string {
  return `${Math.round(fraction * 100)} %`
}

export function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

const sizeFormat = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 })

/** 2457600 to "2,3 MB". */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${sizeFormat.format(bytes / 1024)} KB`
  return `${sizeFormat.format(bytes / (1024 * 1024))} MB`
}
