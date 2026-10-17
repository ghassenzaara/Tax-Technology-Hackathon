import { describe, expect, it } from 'vitest'
import type { Fact } from '../../data/types'
import { fromDraft, sameValue, toDraft, type FactDraft } from './factDraft'

const fact = (type: string, value: unknown, extra: Partial<Fact> = {}): Fact => ({ id: 'f', label: 'L', type, value, sources: [], ...extra })
const roundTrip = (f: Fact) => fromDraft(f, toDraft(f))

describe('fact drafts', () => {
  it.each([
    fact('string', 'Kalenderjahr'),
    fact('integer', 85),
    fact('number', 31750000.5),
    fact('boolean', false),
    fact('date', '2011-03-01'),
    fact('enum', 'ja', { enumValues: ['ja', 'nein'] }),
    fact('list', [{ Name: 'A', Betrag: 1000 }]),
    fact('list', ['Partner Services s.r.o. (CZ)']),
    fact('geo_point', { lat: 1, lon: 2 }),
  ])('round trip keeps $type values unchanged', (f) => {
    const r = roundTrip(f)
    expect(r.ok && sameValue(r.value, f.value)).toBe(true)
  })

  it('unknown types are read only', () => {
    expect(toDraft(fact('geo_point', { lat: 1 })).kind).toBe('readonly')
  })

  it('validates numbers', () => {
    const f = fact('integer', 1)
    expect(fromDraft(f, { kind: 'number', text: '1.5', integer: true })).toEqual({ ok: false, error: 'Bitte eine ganze Zahl eingeben.' })
    expect(fromDraft(f, { kind: 'number', text: 'abc', integer: false }).ok).toBe(false)
    expect(fromDraft(f, { kind: 'number', text: '', integer: true })).toEqual({ ok: true, value: null })
  })

  it('keeps number columns numeric in lists and drops empty rows', () => {
    const f = fact('list', [{ Name: 'A', Betrag: 1000 }])
    const draft = toDraft(f) as Extract<FactDraft, { kind: 'rows' }>
    draft.rows.push(['B', '2500.5'], ['', ''])
    expect(fromDraft(f, draft)).toEqual({ ok: true, value: [{ Name: 'A', Betrag: 1000 }, { Name: 'B', Betrag: 2500.5 }] })
    draft.rows[1][1] = 'viel'
    expect(fromDraft(f, draft).ok).toBe(false)
  })

  it('keeps an enum value that is not in the allowed list', () => {
    const d = toDraft(fact('enum', 'teilweise', { enumValues: ['ja', 'nein'] }))
    expect(d).toMatchObject({ kind: 'enum', options: ['ja', 'nein', 'teilweise'] })
  })
})
