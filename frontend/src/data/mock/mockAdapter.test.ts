import { beforeEach, describe, expect, it } from 'vitest'
import { AdapterError } from '../adapter'
import type { Company } from '../types'
import { MockAdapter } from './mockAdapter'
import { MockEmailSender } from './emailSender'
import { loadSeed, type SeedData } from './seed'

const C1 = 'cmp-7f3a01'
const C2 = 'cmp-2b9c44'
const FOREIGN = 'cmp-foreign'

function seedWithForeignCompany(): SeedData {
  const seed = loadSeed()
  const foreign: Company = { ...structuredClone(seed.companies[0]), id: FOREIGN, advisorId: 'adv-999', name: 'Fremde GmbH' }
  seed.companies.push(foreign)
  seed.matches.push({ ...structuredClone(seed.matches[0]), id: 'm-foreign', companyId: FOREIGN })
  return seed
}

let clock: number
let sender: MockEmailSender
let adapter: MockAdapter

beforeEach(() => {
  clock = Date.parse('2026-10-17T10:00:00Z')
  sender = new MockEmailSender()
  adapter = new MockAdapter({ storage: null, delayMs: 0, now: () => clock, seed: seedWithForeignCompany(), emailSender: sender })
})

async function expectCode(promise: Promise<unknown>, code: string) {
  await expect(promise).rejects.toBeInstanceOf(AdapterError)
  await expect(promise).rejects.toMatchObject({ code })
}

describe('advisor filter', () => {
  it('lists only own companies', async () => {
    const companies = await adapter.listCompanies()
    expect(companies).toHaveLength(5)
    expect(companies.every((c) => c.advisorId === 'adv-001')).toBe(true)
  })

  it('treats a foreign company as not found everywhere', async () => {
    await expectCode(adapter.getCompany(FOREIGN), 'not_found')
    await expectCode(adapter.updateCompany(FOREIGN, { core: { name: 'X' } }), 'not_found')
    await expectCode(adapter.listAuditEntries(FOREIGN), 'not_found')
    await expectCode(adapter.getEmailTemplate(FOREIGN), 'not_found')
    await expectCode(adapter.updateMatchReview('m-foreign', { label: 'relevant' }), 'not_found')
  })

  it('never returns foreign matches', async () => {
    const all = await adapter.listMatches()
    expect(all.some((m) => m.companyId === FOREIGN)).toBe(false)
    expect(await adapter.listMatches({ companyId: FOREIGN })).toEqual([])
  })
})

describe('match review', () => {
  it('changes only the review part and keeps the assessment', async () => {
    const [before] = await adapter.listMatches({ companyId: C2 })
    const patch = { label: 'irrelevant', notes: 'Test', assessment: { reason: 'hack' }, review: { label: 'x' } } as never
    const after = await adapter.updateMatchReview(before.id, patch)
    expect(after.assessment).toEqual(before.assessment)
    expect(after.review.label).toBe('irrelevant')
    expect(after.review.notes).toBe('Test')
    expect(after.review.updatedBy).toBe('adv-001')
  })

  it('sets discussedAt when marked discussed by hand', async () => {
    const m = await adapter.updateMatchReview('m-02', { emailStatus: 'discussed' })
    expect(m.review.discussedAt).toBe(new Date(clock).toISOString())
  })

  it('sets sentAt when marked sent by hand, without an outreach record', async () => {
    const m = await adapter.updateMatchReview('m-02', { emailStatus: 'sent' })
    expect(m.review.sentAt).toBe(new Date(clock).toISOString())
    expect(m.review.outreachId).toBeUndefined()
  })

  it('writes one audit entry per changed field', async () => {
    await adapter.updateMatchReview('m-02', { label: 'irrelevant', notes: 'x' })
    const entries = await adapter.listAuditEntries(C2)
    const changes = entries.filter((e) => e.kind === 'match_review_changed' && e.matchId === 'm-02')
    expect(changes.map((e) => e.field).sort()).toEqual(['label', 'notes'])
  })

  it('writes no audit entry when nothing changes', async () => {
    const before = (await adapter.listAuditEntries(C2)).length
    await adapter.updateMatchReview('m-02', { label: 'relevant' })
    expect(await adapter.listAuditEntries(C2)).toHaveLength(before)
  })
})

describe('send flow', () => {
  const base = { companyId: C1, recipient: 'max.mustermann@beispiel-gmbh.example', subject: 'Betreff', body: 'Text' }

  it('marks exactly the ticked matches as sent and stores a snapshot', async () => {
    const outreach = await adapter.sendClientEmail({ ...base, matchIds: ['m-05'] })
    expect(outreach.matchIds).toEqual(['m-05'])
    expect(outreach.body).toBe('Text')
    expect(sender.sent).toHaveLength(1)
    const matches = await adapter.listMatches({ companyId: C1 })
    expect(matches.find((m) => m.id === 'm-05')!.review).toMatchObject({ emailStatus: 'sent', outreachId: outreach.id })
    expect(matches.find((m) => m.id === 'm-06')!.review.emailStatus).toBe('not_sent')
    const audit = await adapter.listAuditEntries(C1)
    expect(audit[0]).toMatchObject({ kind: 'email_sent', outreachId: outreach.id })
  })

  it('keeps the exact sent email for the Verlauf, newest first, own clients only', async () => {
    clock += 60_000
    const sent = await adapter.sendClientEmail({ ...base, body: 'Exakter Text\nZeile 2', matchIds: ['m-05'] })
    const list = await adapter.listOutreach(C1)
    expect(list[0]).toEqual(sent)
    expect(list[0].body).toBe('Exakter Text\nZeile 2')
    expect(list.map((o) => o.id)).toContain('out-004') // seed email stays
    const audit = (await adapter.listAuditEntries(C1)).find((e) => e.kind === 'email_sent')!
    expect(audit.outreachId).toBe(sent.id)
    await expectCode(adapter.listOutreach(FOREIGN), 'not_found')
  })

  it('rejects invalid input and changes nothing', async () => {
    await expectCode(adapter.sendClientEmail({ ...base, recipient: 'kein-mail', matchIds: ['m-05'] }), 'validation_error')
    await expectCode(adapter.sendClientEmail({ ...base, matchIds: [] }), 'validation_error')
    await expectCode(adapter.sendClientEmail({ ...base, matchIds: ['m-02'] }), 'validation_error')
    expect(sender.sent).toHaveLength(0)
    const m05 = (await adapter.listMatches({ companyId: C1 })).find((m) => m.id === 'm-05')!
    expect(m05.review.emailStatus).toBe('not_sent')
  })

  it('blocks law terms even when the dialog check is bypassed', async () => {
    const promise = adapter.sendClientEmail({ ...base, body: 'Die Umsatzsteuer nach § 6a UStG ändert sich.', matchIds: ['m-05'] })
    await expectCode(promise, 'law_terms_found')
    await expect(promise).rejects.toThrow('Die E-Mail enthält gesetzesbezogene Begriffe: Umsatzsteuer, §, UStG')
    expect(sender.sent).toHaveLength(0)
  })

  it('sends the unchanged template', async () => {
    const t = await adapter.getEmailTemplate(C1)
    await expect(adapter.sendClientEmail({ ...base, ...t, matchIds: ['m-05'] })).resolves.toMatchObject({ subject: t.subject })
  })

  it('locks the status after sending: only forward to discussed', async () => {
    await adapter.sendClientEmail({ ...base, matchIds: ['m-05'] })
    await expectCode(adapter.updateMatchReview('m-05', { emailStatus: 'not_sent' }), 'status_locked')
    await expect(adapter.updateMatchReview('m-05', { emailStatus: 'discussed' })).resolves.toMatchObject({ review: { emailStatus: 'discussed' } })
    await expectCode(adapter.updateMatchReview('m-05', { emailStatus: 'sent' }), 'status_locked')
    // Label and notes stay editable.
    await expect(adapter.updateMatchReview('m-05', { notes: 'ok' })).resolves.toMatchObject({ review: { notes: 'ok' } })
  })

  it('changes nothing when the provider fails', async () => {
    const failing = new MockAdapter({
      storage: null,
      delayMs: 0,
      emailSender: { send: () => Promise.reject(new Error('SMTP down')) },
    })
    await expect(failing.sendClientEmail({ ...base, matchIds: ['m-05'] })).rejects.toThrow('SMTP down')
    const m05 = (await failing.listMatches({ companyId: C1 })).find((m) => m.id === 'm-05')!
    expect(m05.review.emailStatus).toBe('not_sent')
  })
})

describe('email template', () => {
  it('fills only greeting and signature', async () => {
    const t = await adapter.getEmailTemplate(C1)
    expect(t.body.startsWith('Sehr geehrter Herr Mustermann,')).toBe(true)
    expect(t.body).toContain('Max Schmidt\nSteuerberater')
  })
})

describe('stored email template', () => {
  const custom = {
    subject: 'Ihr Gesprächstermin',
    body: '{Anrede},\n\ngerne möchten wir mit Ihnen über aktuelle Entwicklungen sprechen.\n\nBeste Grüße\n{Signatur}',
  }

  it('starts with the default and fills drafts from the saved one', async () => {
    expect(await adapter.getStoredEmailTemplate()).toMatchObject({ isDefault: true })
    const saved = await adapter.updateEmailTemplate(custom)
    expect(saved).toMatchObject({ ...custom, isDefault: false, updatedBy: 'adv-001' })
    const draft = await adapter.getEmailTemplate(C1)
    expect(draft.subject).toBe('Ihr Gesprächstermin')
    expect(draft.body.startsWith('Sehr geehrter Herr Mustermann,')).toBe(true)
    expect(draft.body).toContain('Beste Grüße\nMax Schmidt')
  })

  it.each([
    [{ ...custom, body: custom.body.replace('{Anrede}', 'Hallo') }, '{Anrede}'],
    [{ ...custom, body: custom.body.replace('{Signatur}', 'Max') }, '{Signatur}'],
    [{ ...custom, body: custom.body + ' {Firma}' }, 'Unbekannte Platzhalter: {Firma}'],
    [{ ...custom, body: custom.body + ' Siehe § 4h EStG.' }, 'gesetzesbezogene Begriffe'],
    [{ ...custom, subject: 'Neues zur Zinsschranke' }, 'Zinsschranke'],
    [{ ...custom, subject: '  ' }, 'Betreff'],
  ])('rejects an invalid template (%#)', async (t, message) => {
    await expectCode(adapter.updateEmailTemplate(t), 'validation_error')
    await expect(adapter.updateEmailTemplate(t)).rejects.toThrow(message)
    expect(await adapter.getStoredEmailTemplate()).toMatchObject({ isDefault: true })
  })

  it('records who changed the template, newest first, and skips no-op saves', async () => {
    expect(await adapter.listTemplateHistory()).toEqual([])
    await adapter.updateEmailTemplate(custom)
    await adapter.updateEmailTemplate(custom) // same text: no entry
    clock += 60_000
    await adapter.resetEmailTemplate()
    await adapter.resetEmailTemplate() // already default: no entry
    const history = await adapter.listTemplateHistory()
    expect(history.map((h) => h.kind)).toEqual(['template_reset', 'template_edited'])
    expect(history[1]).toMatchObject({ actedBy: 'adv-001', newSubject: custom.subject, oldSubject: 'Gesprächsangebot zu aktuellen regulatorischen Entwicklungen' })
    expect(history[0]).toMatchObject({ oldSubject: custom.subject, newSubject: 'Gesprächsangebot zu aktuellen regulatorischen Entwicklungen' })
  })

  it('does not record a rejected template', async () => {
    await expect(adapter.updateEmailTemplate({ ...custom, body: 'ohne Platzhalter' })).rejects.toThrow()
    expect(await adapter.listTemplateHistory()).toEqual([])
  })

  it('sends with the saved template and resets to the default', async () => {
    await adapter.updateEmailTemplate(custom)
    const t = await adapter.getEmailTemplate(C1)
    await expect(adapter.sendClientEmail({ companyId: C1, recipient: 'a@b.example', ...t, matchIds: ['m-05'] })).resolves.toBeTruthy()
    expect(await adapter.resetEmailTemplate()).toMatchObject({ isDefault: true })
  })
})

describe('company updates', () => {
  it('audits each changed core field and fact', async () => {
    await adapter.updateCompany(C1, { core: { industry: 'Großhandel' }, facts: [{ id: 'c1-f04', value: 90 }] })
    const audit = await adapter.listAuditEntries(C1)
    expect(audit.filter((e) => e.kind === 'profile_changed').map((e) => [e.field, e.oldValue, e.newValue])).toEqual(
      expect.arrayContaining([
        ['industry', 'Handel', 'Großhandel'],
        ['Mitarbeiter (Durchschnitt)', 85, 90],
      ]),
    )
  })

  it('marks a hand-edited fact as entered by the advisor and drops the old source', async () => {
    const c = await adapter.updateCompany(C1, { facts: [{ id: 'c1-f04', value: 90 }] })
    expect(c.facts.find((f) => f.id === 'c1-f04')).toMatchObject({ value: 90, reviewStatus: 'advisor_entered', sources: [] })
  })

  it('validates the contact email', async () => {
    await expectCode(adapter.updateCompany(C1, { core: { contact: { name: 'A', email: 'x' } } }), 'validation_error')
  })
})

describe('new client flow', () => {
  it('discards an unfinished new profile completely', async () => {
    const run = await adapter.uploadFiles(null, [{ relativePath: 'a.pdf', sizeBytes: 1 }])
    await adapter.discardDraftCompany(run.companyId)
    expect(await adapter.listDraftCompanies()).toEqual([])
    await expectCode(adapter.getCompany(run.companyId), 'not_found')
    await expectCode(adapter.discardDraftCompany(C1), 'not_allowed')
  })

  it('rejects an invalid contact email and an empty company name', async () => {
    const run = await adapter.uploadFiles(null, [{ relativePath: 'a.pdf', sizeBytes: 1 }])
    const base = {
      'd-f05': { kind: 'value', value: 85, candidateId: 'd-f05-c1' },
      'd-f06': { kind: 'value', value: 318000 },
      'd-f07': { kind: 'left_empty' },
      'd-f10': { kind: 'left_empty' },
      'd-f12': { kind: 'left_empty' },
      'd-f13': { kind: 'left_empty' },
      'd-f15': { kind: 'left_empty' },
    } as const
    await expect(adapter.confirmProfile(run.companyId, { ...base, 'd-f14': { kind: 'value', value: 'kein-mail' } })).rejects.toThrow('E-Mail')
    await expect(
      adapter.confirmProfile(run.companyId, { ...base, 'd-f14': { kind: 'left_empty' }, 'd-f01': { kind: 'value', value: '' } }),
    ).rejects.toThrow('Name des Unternehmens')
    expect((await adapter.getCompany(run.companyId)).status).toBe('draft')
  })

  // Contact fields of the demo draft are missing; they must be filled or left empty.
  const contact = {
    'd-f12': { kind: 'value', value: 'Herr' },
    'd-f13': { kind: 'value', value: 'Thomas Berger' },
    'd-f14': { kind: 'value', value: 't.berger@alpenblick.example' },
    'd-f15': { kind: 'left_empty' },
  } as const

  it('starts with the upload, is no client until confirmed, then fills the core from the documents', async () => {
    const before = (await adapter.listCompanies()).length
    const run = await adapter.uploadFiles(null, [
      { relativePath: 'DD/Jahresabschluss 2025.pdf', sizeBytes: 1000 },
      { relativePath: 'DD/Besprechungsprotokoll.docx', sizeBytes: 1000 },
      { relativePath: 'DD/Riesig.pdf', sizeBytes: 60 * 1024 * 1024 },
    ])
    expect(run.documents.every((d) => d.parsingStatus === 'pending')).toBe(true)
    const company = await adapter.getCompany(run.companyId)
    expect(company).toMatchObject({ status: 'draft', name: 'Alpenblick Handels GmbH' })
    // Not a client yet: hidden from the list, visible as an unfinished draft.
    expect(await adapter.listCompanies()).toHaveLength(before)
    expect((await adapter.listDraftCompanies()).map((c) => c.id)).toEqual([company.id])

    clock += 60_000
    const done = await adapter.getRunProgress(run.id)
    expect(done.status).toBe('finished')
    expect(done.documents.map((d) => [d.parsingStatus, d.extractionStatus])).toEqual([
      ['ok', 'ok'],
      ['unsupported', 'skipped'],
      ['failed', 'skipped'],
    ])

    const draft = await adapter.getProfileDraft(company.id)
    expect(draft.facts.some((f) => f.reviewStatus === 'conflict')).toBe(true)
    await expectCode(adapter.confirmProfile(company.id, {}), 'unresolved_fields')

    const confirmed = await adapter.confirmProfile(company.id, {
      'd-f05': { kind: 'value', value: 85, candidateId: 'd-f05-c1' },
      'd-f06': { kind: 'value', value: 318000 },
      'd-f07': { kind: 'left_empty' },
      'd-f10': { kind: 'value', value: '2015-05-04' },
      ...contact,
    })
    expect(confirmed.status).toBe('active')
    expect(confirmed).toMatchObject({
      name: 'Alpenblick Handels GmbH',
      legalForm: 'GmbH',
      industry: 'Großhandel',
      contact: { salutation: 'Herr', name: 'Thomas Berger', email: 't.berger@alpenblick.example' },
    })
    expect(confirmed.contact.phone).toBeUndefined()
    // Core values are not duplicated as profile facts.
    expect(confirmed.facts.some((f) => f.coreField)).toBe(false)
    expect(await adapter.listCompanies()).toHaveLength(before + 1)
    expect(await adapter.listDraftCompanies()).toEqual([])
    const employees = confirmed.facts.find((f) => f.id === 'd-f05')!
    expect(employees).toMatchObject({ value: 85, reviewStatus: 'single_source' })
    expect(confirmed.facts.find((f) => f.id === 'd-f10')!.reviewStatus).toBe('advisor_entered')
    // After confirmation only filename and page remain.
    expect(confirmed.facts.flatMap((f) => f.sources).every((s) => s.quote === undefined)).toBe(true)
    expect(confirmed.facts.every((f) => f.candidates === undefined && f.reasoning === undefined)).toBe(true)
    expect((await adapter.listAuditEntries(company.id))[0].kind).toBe('profile_confirmed')
  })

  it('shows only new or different values when documents are added to an active client', async () => {
    await adapter.uploadFiles(C1, [{ relativePath: 'Jahresabschluss 2025.pdf', sizeBytes: 10 }])
    const draft = await adapter.getProfileDraft(C1)
    const labels = draft.facts.map((f) => f.label)
    expect(labels).not.toContain('Innergemeinschaftliche Lieferungen') // same value as the profile
    expect(labels).toContain('Umsatzerlöse 2025') // differs: 12.480.000 vs 12.500.000
    // Not found in the new documents: never offered, so nothing can be erased.
    expect(labels).not.toContain('Umsatzsteuerliche Organschaft')
    expect(labels).not.toContain('Gründungsdatum')
  })

  it('updates an active client only with the reviewed values and logs each change', async () => {
    await adapter.uploadFiles(C1, [{ relativePath: 'Jahresabschluss 2025.pdf', sizeBytes: 10 }])
    const before = await adapter.getCompany(C1)
    const updated = await adapter.confirmProfile(C1, {
      'd-f05': { kind: 'value', value: 92, candidateId: 'd-f05-c2' },
      'd-f06': { kind: 'value', value: 318000 },
    })
    expect(updated.status).toBe('active')
    expect(updated.facts.find((f) => f.label === 'Mitarbeiter (Durchschnitt)')!.value).toBe(92)
    expect(updated.facts.find((f) => f.label === 'Umsatzsteuerliche Organschaft')!.value).toBe(false) // untouched
    expect(updated.facts.length).toBeGreaterThan(before.facts.length) // new facts added
    const changes = (await adapter.listAuditEntries(C1)).filter((e) => e.kind === 'profile_changed')
    expect(changes.map((e) => e.field)).toContain('Mitarbeiter (Durchschnitt)')
    expect(changes.find((e) => e.field === 'Mitarbeiter (Durchschnitt)')).toMatchObject({ oldValue: 85, newValue: 92 })
  })
})

describe('persistence', () => {
  it('survives a reload and resets to the seed', async () => {
    const store = new Map<string, string>()
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v), removeItem: (k: string) => void store.delete(k) }
    const first = new MockAdapter({ storage, delayMs: 0 })
    await first.saveCompanyNote(C1, 'Gespeichert')
    const second = new MockAdapter({ storage, delayMs: 0 })
    expect((await second.getCompany(C1)).notes).toBe('Gespeichert')
    await second.resetDemo()
    expect((await second.getCompany(C1)).notes).toBe('Geschäftsführer bevorzugt Termine am Vormittag.')
  })

  it('returns copies, so callers cannot mutate state', async () => {
    const c = await adapter.getCompany(C1)
    c.name = 'Kaputt'
    expect((await adapter.getCompany(C1)).name).toBe('Beispiel GmbH')
  })
})
