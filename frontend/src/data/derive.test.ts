import { describe, expect, it } from 'vitest'
import { loadSeed } from './mock/seed'
import { greetingFor, isOpenTask, missingCompanyFacts, newThisMonth, relevantCompanyCount, relevantUpdateCount, unreadLawChangeIds } from './derive'
import { filterCompanies } from '../features/clients/ClientTable'

const seed = loadSeed()

describe('derived counts on the seed data', () => {
  it('flags only missing information, including core fields, without duplicates', () => {
    const base = seed.companies[0]
    const missing = { ...base.facts[0], id: 'contact-email', reviewStatus: 'missing', value: null }
    const saved = { ...base.facts[0], id: 'saved', reviewStatus: 'confirmed', value: false }
    const company = { ...base, facts: [missing, saved], extra: { coreFacts: [missing] } }
    expect(missingCompanyFacts(company)).toEqual([missing])
  })
  it('keeps old assessments out of actionable counts', () => {
    const match = { ...seed.matches[1], assessment: { ...seed.matches[1].assessment, current: false } }
    expect(isOpenTask(match)).toBe(false)
    expect(relevantCompanyCount([match], match.lawChangeId)).toBe(0)
    expect(relevantUpdateCount([match], match.companyId)).toBe(0)
    expect(unreadLawChangeIds([match], seed.advisors[0])).toEqual([])
  })
  it('unread law changes', () => {
    expect(unreadLawChangeIds(seed.matches, seed.advisors[0]).sort()).toEqual(['law-001', 'law-002', 'law-003'])
  })

  it('open tasks: unlabeled plus relevant and not sent', () => {
    expect(seed.matches.filter(isOpenTask).map((m) => m.id).sort()).toEqual(
      ['m-02', 'm-03', 'm-05', 'm-06', 'm-09', 'm-10', 'm-12', 'm-14'].sort(),
    )
  })

  it('relevance per law excludes irrelevant labels', () => {
    expect(relevantCompanyCount(seed.matches, 'law-003')).toBe(4)
    expect(relevantCompanyCount(seed.matches, 'law-005')).toBe(0)
  })

  it('relevant updates per client exclude irrelevant and discussed', () => {
    expect(relevantUpdateCount(seed.matches, 'cmp-7f3a01')).toBe(2)
    expect(relevantUpdateCount(seed.matches, 'cmp-e5506b')).toBe(0)
  })
})

describe('dashboard helpers', () => {
  it('counts clients created this month', () => {
    const created = seed.companies.map((c) => c.createdAt)
    expect(newThisMonth(created, new Date('2026-10-17T12:00:00Z'))).toBe(2)
    expect(newThisMonth(created, new Date('2027-01-05T12:00:00Z'))).toBe(0)
  })

  it.each([
    ['2026-10-17T06:00:00Z', 'Guten Morgen'],
    ['2026-10-17T12:00:00Z', 'Guten Tag'],
    ['2026-10-17T18:30:00Z', 'Guten Abend'],
  ])('greets by Berlin time at %s', (iso, expected) => {
    expect(greetingFor(new Date(iso))).toBe(expected)
  })

  it('filters clients by name, industry or contact', () => {
    expect(filterCompanies(seed.companies, 'bau').map((c) => c.name)).toEqual(['Schneider Bau GmbH & Co. KG'])
    expect(filterCompanies(seed.companies, 'larsen').map((c) => c.name)).toEqual(['Nordlicht Immobilien AG'])
    expect(filterCompanies(seed.companies, '  ')).toHaveLength(5)
  })

  it('finds a client by its project without creating another client entry', () => {
    const company = { ...seed.companies[0], name: 'Nordhafen Logistik GmbH', extra: { projekt: 'Hafenkante' } }
    expect(filterCompanies([company], 'hafenkante')).toEqual([company])
    expect(filterCompanies([company], 'nordhafen')).toEqual([company])
  })
})
