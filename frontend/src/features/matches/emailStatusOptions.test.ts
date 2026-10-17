import { describe, expect, it } from 'vitest'
import type { Match } from '../../data/types'
import { sortForCompany } from '../../data/matchRules'
import { emailStatusOptions } from './MatchTable'

const match = (review: Partial<Match['review']>, extra: Partial<Match> = {}): Match => ({
  id: 'm',
  companyId: 'c',
  lawChangeId: 'l',
  assessment: { score: 0, confidence: 0, reason: '', evidence: [], lawVersion: 1, screenedAt: '', runId: 'r' },
  review: { label: 'relevant', emailStatus: 'not_sent', ...review },
  ...extra,
})

describe('email status by hand', () => {
  it.each(['not_sent', 'sent', 'discussed'])('offers all three statuses without an email (status %s)', (status) => {
    expect(emailStatusOptions(match({ emailStatus: status }))).toEqual(['not_sent', 'sent', 'discussed'])
  })

  it('after an email: Gesendet can only move forward to Besprochen', () => {
    expect(emailStatusOptions(match({ emailStatus: 'sent', outreachId: 'out-1' }))).toEqual(['sent', 'discussed'])
  })

  it('after an email: Besprochen is final', () => {
    expect(emailStatusOptions(match({ emailStatus: 'discussed', outreachId: 'out-1' }))).toEqual(['discussed'])
  })

  it('never locks a row on Nicht gesendet, even with an old email record', () => {
    expect(emailStatusOptions(match({ emailStatus: 'not_sent', outreachId: 'out-1' }))).toEqual(['not_sent', 'sent', 'discussed'])
  })

  it('keeps an unknown status visible', () => {
    expect(emailStatusOptions(match({ emailStatus: 'bounced' }))).toEqual(['bounced', 'not_sent', 'sent', 'discussed'])
  })
})

describe('order in the client table', () => {
  it('puts irrelevant law changes last, newest first within each part', () => {
    const dates: Record<string, string> = { a: '2026-10-01', b: '2026-09-01', c: '2026-10-10', d: '2026-08-01' }
    const ms = [
      match({ label: 'irrelevant' }, { id: 'A', lawChangeId: 'c' }),
      match({}, { id: 'B', lawChangeId: 'b' }),
      match({ label: 'unlabeled' }, { id: 'C', lawChangeId: 'a' }),
      match({ label: 'irrelevant' }, { id: 'D', lawChangeId: 'd' }),
    ]
    expect(sortForCompany(ms, (id) => dates[id]).map((m) => m.id)).toEqual(['C', 'B', 'A', 'D'])
  })
})
