import type { Fact } from '../../data/types'
import { isPlainObject } from '../../lib/format'

// Converts fact values to editable form state and back. Inputs hold raw strings
// (number inputs use a dot), never German formatted text.

export type FactDraft =
  | { kind: 'text'; text: string }
  | { kind: 'number'; text: string; integer: boolean }
  | { kind: 'boolean'; text: '' | 'true' | 'false' }
  | { kind: 'date'; text: string }
  | { kind: 'enum'; text: string; options: string[] }
  | { kind: 'rows'; columns: string[]; numeric: Record<string, boolean>; rows: string[][] }
  | { kind: 'items'; items: string[]; numeric: boolean }
  | { kind: 'readonly' }

export type ParseResult = { ok: true; value: unknown } | { ok: false; error: string }

const str = (v: unknown) => (v === null || v === undefined ? '' : String(v))

export function toDraft(fact: Fact): FactDraft {
  const v = fact.value
  switch (fact.type) {
    case 'string':
      return typeof v === 'string' || v == null ? { kind: 'text', text: str(v) } : { kind: 'readonly' }
    case 'integer':
    case 'number':
      return typeof v === 'number' || v == null ? { kind: 'number', text: str(v), integer: fact.type === 'integer' } : { kind: 'readonly' }
    case 'boolean':
      return { kind: 'boolean', text: v === true ? 'true' : v === false ? 'false' : '' }
    case 'date':
      return { kind: 'date', text: typeof v === 'string' ? v.slice(0, 10) : '' }
    case 'enum': {
      const options = (fact.enumValues ?? []).map(str)
      const text = str(v)
      if (text && !options.includes(text)) options.push(text)
      return { kind: 'enum', text, options }
    }
    case 'list': {
      const list = Array.isArray(v) ? v : []
      if (list.every(isPlainObject)) {
        const rows = list as Record<string, unknown>[]
        const columns = [...new Set(rows.flatMap((r) => Object.keys(r)))]
        const numeric = Object.fromEntries(columns.map((c) => [c, rows.some((r) => typeof r[c] === 'number')]))
        return { kind: 'rows', columns, numeric, rows: rows.map((r) => columns.map((c) => str(r[c]))) }
      }
      if (list.every((x) => typeof x !== 'object' || x === null)) {
        return { kind: 'items', items: list.map(str), numeric: list.length > 0 && list.every((x) => typeof x === 'number') }
      }
      return { kind: 'readonly' }
    }
    default:
      return { kind: 'readonly' }
  }
}

function parseNumber(text: string, integer: boolean): ParseResult {
  const t = text.trim()
  if (!t) return { ok: true, value: null }
  const n = Number(t)
  if (!Number.isFinite(n)) return { ok: false, error: 'Bitte eine Zahl eingeben.' }
  if (integer && !Number.isInteger(n)) return { ok: false, error: 'Bitte eine ganze Zahl eingeben.' }
  return { ok: true, value: n }
}

export function fromDraft(fact: Fact, draft: FactDraft): ParseResult {
  switch (draft.kind) {
    case 'text':
      return { ok: true, value: draft.text.trim() === '' ? null : draft.text }
    case 'number':
      return parseNumber(draft.text, draft.integer)
    case 'boolean':
      return { ok: true, value: draft.text === '' ? null : draft.text === 'true' }
    case 'date':
      return { ok: true, value: draft.text || null }
    case 'enum':
      return { ok: true, value: draft.text || null }
    case 'items': {
      const out: unknown[] = []
      for (const item of draft.items) {
        if (!item.trim()) continue
        if (draft.numeric) {
          const r = parseNumber(item, false)
          if (!r.ok) return r
          out.push(r.value)
        } else out.push(item)
      }
      return { ok: true, value: out }
    }
    case 'rows': {
      const out: Record<string, unknown>[] = []
      for (const row of draft.rows) {
        if (row.every((c) => !c.trim())) continue
        const obj: Record<string, unknown> = {}
        for (const [i, col] of draft.columns.entries()) {
          if (draft.numeric[col]) {
            const r = parseNumber(row[i], false)
            if (!r.ok) return { ok: false, error: `${col}: ${r.error}` }
            obj[col] = r.value
          } else obj[col] = row[i] === '' ? null : row[i]
        }
        out.push(obj)
      }
      return { ok: true, value: out }
    }
    case 'readonly':
      return { ok: true, value: fact.value }
  }
}

export function sameValue(a: unknown, b: unknown): boolean {
  const norm = (v: unknown) => (v === undefined || v === '' ? null : v)
  return JSON.stringify(norm(a)) === JSON.stringify(norm(b))
}
