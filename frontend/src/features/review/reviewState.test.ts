import { describe, expect, it } from 'vitest'
import { loadSeed } from '../../data/mock/seed'
import type { Fact } from '../../data/types'
import { ownDraft, resolve, reviewFacts, reviewOverview, summarize, type Choices } from './reviewState'

const draft = loadSeed().draftTemplate
const fact = (id: string) => draft.facts.find((f) => f.id === id)!
const conflict = fact('d-f05') // three candidates
const ocr = fact('d-f06') // needs review
const missing = fact('d-f07') // boolean, nothing found
const plain = fact('d-f01') // confirmed
const rounding = fact('d-f04') // confirmed with alternatives

describe('review rules', () => {
  it('counts conflicts as found and separates missing values from review work', () => {
    const facts = [plain, conflict, ocr, missing]
    expect(reviewOverview(facts)).toEqual({ total: 4, found: 3, missing: 1, conflicts: 1, review: 3 })
    expect(reviewFacts(facts, 'review')).toEqual([conflict, ocr, missing])
    expect(reviewFacts(facts, 'missing')).toEqual([missing])
    expect(reviewFacts(facts, 'all')).toEqual(facts)
  })

  it('starts with exactly the conflict, OCR and missing fields open', () => {
    const s = summarize(draft.facts, {})
    expect(s.open.sort()).toEqual(['d-f05', 'd-f06', 'd-f07', 'd-f10', 'd-f12', 'd-f13', 'd-f14', 'd-f15'])
    expect(s.canConfirm).toBe(false)
  })

  it('never pre-selects a conflict, and accept does not resolve it', () => {
    expect(resolve(conflict, undefined)).toEqual({ ok: 'open' })
    expect(resolve(conflict, { mode: 'accept' })).toEqual({ ok: 'open' })
    expect(ownDraft(conflict)).toMatchObject({ kind: 'number', text: '' })
  })

  it('resolves a conflict by candidate or own value', () => {
    expect(resolve(conflict, { mode: 'candidate', candidateId: 'd-f05-c2' })).toEqual({
      ok: true,
      resolution: { kind: 'value', value: 92, candidateId: 'd-f05-c2' },
    })
    expect(resolve(conflict, { mode: 'own', draft: { kind: 'number', text: '90', integer: true } })).toEqual({
      ok: true,
      resolution: { kind: 'value', value: 90 },
    })
  })

  it('rejects an empty or invalid own value', () => {
    expect(resolve(conflict, { mode: 'own', draft: { kind: 'number', text: '', integer: true } })).toMatchObject({ ok: false })
    expect(resolve(conflict, { mode: 'own', draft: { kind: 'number', text: '8.5', integer: true } })).toMatchObject({
      ok: false,
      error: 'Bitte eine ganze Zahl eingeben.',
    })
  })

  it('needs an explicit OK for an OCR value', () => {
    expect(resolve(ocr, undefined)).toEqual({ ok: 'open' })
    expect(resolve(ocr, { mode: 'accept' })).toEqual({ ok: true, resolution: { kind: 'value', value: 318000 } })
  })

  it('lets a missing field be filled or left empty on purpose', () => {
    expect(resolve(missing, { mode: 'empty' })).toEqual({ ok: true, resolution: { kind: 'left_empty' } })
    expect(resolve(missing, { mode: 'own', draft: { kind: 'boolean', text: 'false' } })).toEqual({
      ok: true,
      resolution: { kind: 'value', value: false },
    })
  })

  it('keeps normal values without sending a resolution, but allows overriding them', () => {
    expect(resolve(plain, undefined)).toEqual({ ok: true, resolution: null })
    expect(resolve(rounding, undefined)).toEqual({ ok: true, resolution: null })
    expect(resolve(plain, { mode: 'own', draft: { kind: 'text', text: 'Alpenblick GmbH' } })).toEqual({
      ok: true,
      resolution: { kind: 'value', value: 'Alpenblick GmbH' },
    })
  })

  it('can confirm once every open field is handled', () => {
    const choices: Choices = {
      'd-f05': { mode: 'candidate', candidateId: 'd-f05-c1' },
      'd-f06': { mode: 'accept' },
      'd-f07': { mode: 'empty' },
      'd-f10': { mode: 'own', draft: { kind: 'date', text: '2015-05-04' } },
      'd-f12': { mode: 'own', draft: { kind: 'enum', text: 'Herr', options: ['Herr', 'Frau'] } },
      'd-f13': { mode: 'own', draft: { kind: 'text', text: 'Thomas Berger' } },
      'd-f14': { mode: 'empty' },
      'd-f15': { mode: 'empty' },
    }
    const s = summarize(draft.facts, choices)
    expect(s.canConfirm).toBe(true)
    expect(Object.keys(s.resolutions).sort()).toEqual(['d-f05', 'd-f06', 'd-f07', 'd-f10', 'd-f12', 'd-f13', 'd-f14', 'd-f15'])
  })

  it('checks core fields: valid email, company name required', () => {
    const email = fact('d-f14')
    expect(resolve(email, { mode: 'own', draft: { kind: 'text', text: 'kein-mail' } })).toEqual({ ok: false, error: 'Die E-Mail Adresse ist ungültig.' })
    expect(resolve(email, { mode: 'own', draft: { kind: 'text', text: 'a@b.example' } })).toMatchObject({ ok: true })
    const name = { ...plain, reviewStatus: 'missing', value: undefined }
    expect(resolve(name, { mode: 'empty' })).toEqual({ ok: 'open' })
  })

  it('handles unknown types as free text', () => {
    const odd: Fact = { id: 'x', label: 'Koordinaten', type: 'geo_point', sources: [], reviewStatus: 'missing' }
    expect(ownDraft(odd)).toEqual({ kind: 'text', text: '' })
  })
})
