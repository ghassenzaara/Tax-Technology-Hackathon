import {
  AdapterError,
  type CompanyUpdate,
  type DataAdapter,
  type EmailTemplate,
  type StoredEmailTemplate,
  type TemplateHistoryEntry,
  type FactResolution,
  type MatchFilter,
  type SendEmailInput,
  type UploadFileInfo,
} from '../adapter'
import {
  SCHEMA_VERSION,
  type AuditEntry,
  type Company,
  type Fact,
  type Id,
  type Match,
  type MatchReviewPatch,
  type Outreach,
  type ProfileDraft,
  type UploadDocument,
  type UploadRun,
} from '../types'
import { DEFAULT_TEMPLATE, fillTemplate } from '../../features/email/template'
import { checkEmail, templateProblems, termsForClient, uniqueTerms } from '../../features/email/lawTermCheck'
import { MockEmailSender, type EmailSender } from './emailSender'
import { loadSeed, type SeedData } from './seed'
import { allowedEmailStatuses } from '../matchRules'
import { needsDecision } from '../draftRules'
import { EMAIL_PATTERN, coreValueProblem, feedsCore, getCoreValue, isCoreField, setCoreValue, type CoreField } from '../coreFields'

const STORAGE_KEY = 'klaris-demo-state-v1'
const MAX_FILE_BYTES = 50 * 1024 * 1024
/** Simulated processing time per document. */
const DOC_STEP_MS = 500

interface RunState {
  run: UploadRun
  startedAtMs: number
  /** Final document outcomes, revealed step by step. */
  finalDocs: UploadDocument[]
}

interface State extends Omit<SeedData, 'draftTemplate' | 'demoRun'> {
  /** Null while the default template is used. */
  emailTemplate: { subject: string; body: string; updatedAt: string; updatedBy: Id } | null
  templateHistory: TemplateHistoryEntry[]
  runs: Record<Id, RunState>
  drafts: Record<Id, ProfileDraft>
  counter: number
}

export interface MockAdapterOptions {
  advisorId?: Id
  /** Pass null to disable persistence (tests). */
  storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null
  delayMs?: number
  now?: () => number
  seed?: SeedData
  emailSender?: EmailSender
}

function defaultStorage() {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null
  } catch {
    return null
  }
}

export class MockAdapter implements DataAdapter {
  private state: State
  private readonly seed: SeedData
  private readonly advisorId: Id
  private readonly storage: MockAdapterOptions['storage']
  private readonly delayMs: number
  private readonly now: () => number
  private readonly emailSender: EmailSender

  constructor(options: MockAdapterOptions = {}) {
    this.seed = options.seed ?? loadSeed()
    this.advisorId = options.advisorId ?? 'adv-001'
    this.storage = options.storage === undefined ? defaultStorage() : options.storage
    this.delayMs = options.delayMs ?? 250
    this.now = options.now ?? (() => Date.now())
    this.emailSender = options.emailSender ?? new MockEmailSender()
    this.state = this.restore() ?? this.fresh()
  }

  // ---------- infrastructure ----------

  private fresh(): State {
    const s = structuredClone(this.seed)
    return {
      advisors: s.advisors,
      companies: s.companies,
      lawChanges: s.lawChanges,
      matches: s.matches,
      outreach: s.outreach,
      audit: s.audit,
      emailTemplate: null,
      templateHistory: [],
      runs: {},
      drafts: {},
      counter: 0,
    }
  }

  private restore(): State | null {
    try {
      const raw = this.storage?.getItem(STORAGE_KEY)
      if (!raw) return null
      const state = JSON.parse(raw) as State
      // Saved before the template setting existed.
      state.emailTemplate ??= null
      state.templateHistory ??= []
      return state
    } catch {
      return null
    }
  }

  private persist() {
    try {
      this.storage?.setItem(STORAGE_KEY, JSON.stringify(this.state))
    } catch {
      // Storage full or blocked: the demo keeps working in memory.
    }
  }

  private async respond<T>(fn: () => T): Promise<T> {
    if (this.delayMs > 0) await new Promise((r) => setTimeout(r, this.delayMs))
    return structuredClone(fn())
  }

  private nowIso() {
    return new Date(this.now()).toISOString()
  }

  private nextId(prefix: string) {
    this.state.counter += 1
    return `${prefix}-${this.now().toString(36)}${this.state.counter}`
  }

  private audit(entry: Omit<AuditEntry, 'id' | 'actedBy' | 'actedAt'>) {
    this.state.audit.push({ id: this.nextId('aud'), actedBy: this.advisorId, actedAt: this.nowIso(), ...entry })
  }

  /** Advisor filter: another advisor's company is reported as not found. */
  private ownCompany(id: Id): Company {
    const company = this.state.companies.find((c) => c.id === id && c.advisorId === this.advisorId)
    if (!company) throw new AdapterError('not_found', 'Mandant wurde nicht gefunden.')
    return company
  }

  private ownCompanyIds() {
    return new Set(this.state.companies.filter((c) => c.advisorId === this.advisorId).map((c) => c.id))
  }

  private ownMatch(id: Id): Match {
    const own = this.ownCompanyIds()
    const match = this.state.matches.find((m) => m.id === id && own.has(m.companyId))
    if (!match) throw new AdapterError('not_found', 'Treffer wurde nicht gefunden.')
    return match
  }

  // ---------- advisor ----------

  getCurrentAdvisor() {
    return this.respond(() => {
      const advisor = this.state.advisors.find((a) => a.id === this.advisorId)
      if (!advisor) throw new AdapterError('not_found', 'Berater wurde nicht gefunden.')
      return advisor
    })
  }

  // ---------- companies ----------

  listCompanies() {
    return this.respond(() => this.state.companies.filter((c) => c.advisorId === this.advisorId && c.status !== 'draft'))
  }

  listDraftCompanies() {
    return this.respond(() => this.state.companies.filter((c) => c.advisorId === this.advisorId && c.status === 'draft'))
  }

  discardDraftCompany(id: Id) {
    return this.respond(() => {
      const company = this.ownCompany(id)
      if (company.status !== 'draft') throw new AdapterError('not_allowed', 'Nur nicht bestätigte Profile können verworfen werden.')
      this.state.companies = this.state.companies.filter((c) => c.id !== id)
      delete this.state.drafts[id]
      for (const [runId, r] of Object.entries(this.state.runs)) if (r.run.companyId === id) delete this.state.runs[runId]
      this.state.audit = this.state.audit.filter((a) => a.companyId !== id)
      this.persist()
    })
  }

  getCompany(id: Id) {
    return this.respond(() => this.ownCompany(id))
  }

  updateCompany(id: Id, update: CompanyUpdate) {
    return this.respond(() => {
      const company = this.ownCompany(id)
      for (const [key, value] of Object.entries(update.core ?? {})) {
        const field = key as keyof typeof update.core
        const old = company[field as keyof Company]
        if (JSON.stringify(old) === JSON.stringify(value)) continue
        if (field === 'name' && !String(value ?? '').trim())
          throw new AdapterError('validation_error', 'Der Name darf nicht leer sein.')
        if (field === 'contact') {
          const contact = value as Company['contact']
          if (contact.email && !EMAIL_PATTERN.test(contact.email))
            throw new AdapterError('validation_error', 'Die E-Mail Adresse ist ungültig.')
        }
        ;(company as unknown as Record<string, unknown>)[field] = value
        this.audit({ companyId: id, kind: 'profile_changed', field, oldValue: old ?? null, newValue: value ?? null })
      }
      for (const change of update.facts ?? []) {
        const fact = company.facts.find((f) => f.id === change.id)
        if (!fact) throw new AdapterError('not_found', 'Feld wurde nicht gefunden.')
        if (JSON.stringify(fact.value) === JSON.stringify(change.value)) continue
        this.audit({ companyId: id, kind: 'profile_changed', field: fact.label, oldValue: fact.value ?? null, newValue: change.value ?? null })
        fact.value = change.value
        // The document source no longer backs a hand-entered value.
        fact.sources = []
        fact.reviewStatus = 'advisor_entered'
      }
      company.updatedAt = this.nowIso()
      this.persist()
      return company
    })
  }

  saveCompanyNote(id: Id, notes: string) {
    return this.respond(() => {
      const company = this.ownCompany(id)
      company.notes = notes
      company.updatedAt = this.nowIso()
      this.persist()
      return company
    })
  }

  // ---------- upload and review ----------

  uploadFiles(companyId: Id | null, files: UploadFileInfo[]) {
    return this.respond(() => {
      if (files.length === 0) throw new AdapterError('validation_error', 'Bitte mindestens eine Datei auswählen.')
      const company = companyId ? this.ownCompany(companyId) : this.newDraftCompany()
      companyId = company.id
      const runId = this.nextId('run')
      const finalDocs = files.map((f, i) => this.outcomeFor(f, `${runId}-d${i + 1}`))
      const run: UploadRun = {
        schemaVersion: SCHEMA_VERSION,
        id: runId,
        companyId,
        status: 'running',
        startedAt: this.nowIso(),
        documents: finalDocs.map((d) => ({ ...d, parsingStatus: 'pending', extractionStatus: 'pending', warnings: [], error: undefined })),
      }
      this.state.runs[runId] = { run, startedAtMs: this.now(), finalDocs }
      this.state.drafts[companyId] = this.buildDraft(company, runId)
      // Until confirmation the draft name comes from the extracted company name, if any.
      if (company.status === 'draft') {
        const nameFact = this.state.drafts[companyId].facts.find((f) => f.coreField === 'name' && typeof f.value === 'string')
        company.name = (nameFact?.value as string | undefined) ?? 'Neuer Mandant'
      }
      if (company.status !== 'draft')
        this.audit({ companyId, kind: 'documents_added', summary: `${files.length} Dokumente hochgeladen` })
      this.persist()
      return run
    })
  }

  /** An unconfirmed client, created by the first upload. Hidden from the client list. */
  private newDraftCompany(): Company {
    const at = this.nowIso()
    const company: Company = {
      id: this.nextId('cmp'),
      advisorId: this.advisorId,
      name: 'Neuer Mandant',
      status: 'draft',
      createdAt: at,
      updatedAt: at,
      contact: { name: '' },
      facts: [],
      extra: {},
      notes: '',
    }
    this.state.companies.push(company)
    return company
  }

  /** Known demo file names get their fixed outcome; everything else is classified by extension. */
  private outcomeFor(file: UploadFileInfo, id: Id): UploadDocument {
    const name = file.relativePath.split('/').pop() ?? file.relativePath
    const known = this.seed.demoRun.documents.find((d) => d.relativePath.split('/').pop() === name)
    if (known) return { ...structuredClone(known), id, relativePath: file.relativePath, sizeBytes: file.sizeBytes }
    const ext = name.toLowerCase().split('.').pop() ?? ''
    const base = { id, relativePath: file.relativePath, sizeBytes: file.sizeBytes, warnings: [] as string[] }
    if (file.sizeBytes > MAX_FILE_BYTES)
      return { ...base, fileType: ext === 'pdf' ? 'text_pdf' : 'unsupported', parsingStatus: 'failed', extractionStatus: 'skipped', error: { code: 'file_too_large', message: 'Die Datei ist größer als 50 MB.' } }
    if (ext === 'pdf') return { ...base, fileType: 'text_pdf', parsingStatus: 'ok', extractionStatus: 'ok' }
    if (ext === 'xlsx' || ext === 'xlsm') return { ...base, fileType: 'excel', parsingStatus: 'ok', extractionStatus: 'ok' }
    return { ...base, fileType: 'unsupported', parsingStatus: 'unsupported', extractionStatus: 'skipped', warnings: ['Dateityp wird nicht unterstützt'] }
  }

  /**
   * New client: the full demo draft. Existing client: only facts that are new
   * or differ from the confirmed profile.
   */
  private buildDraft(company: Company, runId: Id): ProfileDraft {
    const template = structuredClone(this.seed.draftTemplate)
    let facts = template.facts
    if (company.status !== 'draft') {
      const byLabel = new Map(company.facts.map((f) => [f.label, f]))
      facts = facts.filter((f) => {
        // A value the new documents do not mention changes nothing in the profile.
        if (f.reviewStatus === 'missing') return false
        if (f.reviewStatus === 'conflict') return true
        const existing = isCoreField(f.coreField) ? getCoreValue(company, f.coreField) : byLabel.get(f.label)?.value
        return existing === undefined || JSON.stringify(existing) !== JSON.stringify(f.value)
      })
    }
    return { schemaVersion: SCHEMA_VERSION, companyId: company.id, runId, createdAt: this.nowIso(), facts }
  }

  getRunProgress(runId: Id) {
    return this.respond(() => {
      const entry = this.state.runs[runId]
      if (!entry) throw new AdapterError('not_found', 'Verarbeitung wurde nicht gefunden.')
      this.ownCompany(entry.run.companyId)
      const steps = Math.floor((this.now() - entry.startedAtMs) / DOC_STEP_MS)
      entry.run.documents = entry.finalDocs.map((final, i) => {
        if (steps > i * 2 + 1) return final
        if (steps > i * 2) return { ...final, extractionStatus: 'pending', warnings: [] }
        return { ...final, parsingStatus: 'pending', extractionStatus: 'pending', warnings: [], error: undefined }
      })
      if (steps > entry.finalDocs.length * 2) {
        entry.run.status = 'finished'
        entry.run.finishedAt ??= this.nowIso()
      }
      this.persist()
      return entry.run
    })
  }

  getProfileDraft(companyId: Id) {
    return this.respond(() => {
      this.ownCompany(companyId)
      const draft = this.state.drafts[companyId]
      if (!draft) throw new AdapterError('not_found', 'Für diesen Mandanten liegt kein Entwurf vor.')
      return draft
    })
  }

  confirmProfile(companyId: Id, resolutions: Record<Id, FactResolution>) {
    return this.respond(() => {
      const company = this.ownCompany(companyId)
      const draft = this.state.drafts[companyId]
      if (!draft) throw new AdapterError('not_found', 'Für diesen Mandanten liegt kein Entwurf vor.')
      const open = draft.facts.filter((f) => needsDecision(f) && !resolutions[f.id])
      if (open.length > 0)
        throw new AdapterError('unresolved_fields', `Noch ${open.length} offene Felder: ${open.map((f) => f.label).join(', ')}`)

      const confirmed = draft.facts.map((f) => toConfirmedFact(f, resolutions[f.id]))
      for (const fact of confirmed) {
        const problem = coreValueProblem(fact.coreField, fact.value)
        if (problem) throw new AdapterError('validation_error', problem)
      }
      const isUpdate = company.status !== 'draft'
      const coreFacts = confirmed.filter(feedsCore)
      const profileFacts = confirmed.filter((f) => !feedsCore(f))
      for (const fact of coreFacts) {
        const field = fact.coreField as CoreField
        const old = setCoreValue(company, field, fact.value)
        if (isUpdate && (old ?? '') !== (getCoreValue(company, field) ?? ''))
          this.audit({ companyId, kind: 'profile_changed', field, oldValue: old ?? null, newValue: getCoreValue(company, field) ?? null, summary: 'Aus neuen Dokumenten übernommen' })
      }
      if (isUpdate) {
        for (const fact of profileFacts) {
          const idx = company.facts.findIndex((f) => f.label === fact.label)
          const old = idx >= 0 ? company.facts[idx].value : undefined
          if (idx >= 0) company.facts[idx] = { ...fact, id: company.facts[idx].id }
          else company.facts.push(fact)
          this.audit({ companyId, kind: 'profile_changed', field: fact.label, oldValue: old ?? null, newValue: fact.value ?? null, summary: 'Aus neuen Dokumenten übernommen' })
        }
      } else {
        company.facts = profileFacts
        company.status = 'active'
        this.audit({ companyId, kind: 'profile_confirmed', summary: 'Profil bestätigt' })
      }
      const at = this.nowIso()
      company.confirmedAt = at
      company.confirmedBy = this.advisorId
      company.updatedAt = at
      delete this.state.drafts[companyId]
      this.persist()
      return company
    })
  }

  // ---------- law changes ----------

  listLawChanges() {
    return this.respond(() => [...this.state.lawChanges].sort((a, b) => b.publishedAt.localeCompare(a.publishedAt)))
  }

  getLawChange(id: Id) {
    return this.respond(() => {
      const law = this.state.lawChanges.find((l) => l.id === id)
      if (!law) throw new AdapterError('not_found', 'Gesetzesänderung wurde nicht gefunden.')
      return law
    })
  }

  markLawChangeSeen(id: Id) {
    return this.respond(() => {
      const advisor = this.state.advisors.find((a) => a.id === this.advisorId)
      if (!advisor) return
      advisor.seenLawChangeIds ??= []
      if (!advisor.seenLawChangeIds.includes(id)) {
        advisor.seenLawChangeIds.push(id)
        this.persist()
      }
    })
  }

  // ---------- matches ----------

  listMatches(filter: MatchFilter = {}) {
    return this.respond(() => {
      const own = this.ownCompanyIds()
      return this.state.matches.filter(
        (m) =>
          own.has(m.companyId) &&
          (!('companyId' in filter) || m.companyId === filter.companyId) &&
          (!('lawChangeId' in filter) || m.lawChangeId === filter.lawChangeId),
      )
    })
  }

  updateMatchReview(matchId: Id, patch: MatchReviewPatch) {
    return this.respond(() => {
      const match = this.ownMatch(matchId)
      const allowed: (keyof MatchReviewPatch)[] = ['label', 'emailStatus', 'notes']
      if (patch.emailStatus !== undefined && patch.emailStatus !== match.review.emailStatus && !allowedEmailStatuses(match.review).includes(patch.emailStatus))
        throw new AdapterError('status_locked', 'Nach dem Versand per E-Mail kann der Status nur noch auf „Besprochen“ gesetzt werden.')
      const at = this.nowIso()
      let changed = false
      for (const key of allowed) {
        if (!(key in patch) || patch[key] === match.review[key]) continue
        const old = match.review[key]
        match.review[key] = patch[key] as never
        changed = true
        if (key === 'emailStatus' && patch.emailStatus === 'discussed') match.review.discussedAt = at
        // Marked as sent by hand (for example sent outside the app): no outreach record.
        if (key === 'emailStatus' && patch.emailStatus === 'sent') match.review.sentAt ??= at
        this.audit({ companyId: match.companyId, kind: 'match_review_changed', matchId, field: key, oldValue: old ?? null, newValue: patch[key] ?? null })
      }
      if (changed) {
        match.review.updatedBy = this.advisorId
        match.review.updatedAt = at
        this.persist()
      }
      return match
    })
  }

  // ---------- email ----------

  getEmailTemplate(companyId: Id) {
    return this.respond(() => {
      const company = this.ownCompany(companyId)
      const advisor = this.state.advisors.find((a) => a.id === this.advisorId)!
      return fillTemplate(company.contact, advisor, this.state.emailTemplate ?? DEFAULT_TEMPLATE)
    })
  }

  getStoredEmailTemplate() {
    return this.respond(() => this.storedTemplate())
  }

  updateEmailTemplate(template: EmailTemplate) {
    return this.respond(() => {
      const { messages } = templateProblems(template.subject, template.body, this.state.lawChanges)
      if (messages.length > 0) throw new AdapterError('validation_error', messages.join(' '))
      const old = this.storedTemplate()
      if (old.subject === template.subject && old.body === template.body) return old
      this.state.emailTemplate = { subject: template.subject, body: template.body, updatedAt: this.nowIso(), updatedBy: this.advisorId }
      this.recordTemplateChange('template_edited', old, template)
      this.persist()
      return this.storedTemplate()
    })
  }

  resetEmailTemplate() {
    return this.respond(() => {
      const old = this.storedTemplate()
      if (old.isDefault) return old
      this.state.emailTemplate = null
      this.recordTemplateChange('template_reset', old, DEFAULT_TEMPLATE)
      this.persist()
      return this.storedTemplate()
    })
  }

  listTemplateHistory() {
    return this.respond(() => [...this.state.templateHistory].sort((a, b) => b.actedAt.localeCompare(a.actedAt)))
  }

  private recordTemplateChange(kind: TemplateHistoryEntry['kind'], old: EmailTemplate, next: EmailTemplate) {
    this.state.templateHistory.push({
      id: this.nextId('tpl'),
      kind,
      actedBy: this.advisorId,
      actedAt: this.nowIso(),
      oldSubject: old.subject,
      oldBody: old.body,
      newSubject: next.subject,
      newBody: next.body,
    })
  }

  private storedTemplate(): StoredEmailTemplate {
    const t = this.state.emailTemplate
    return t ? { ...t, isDefault: false } : { ...DEFAULT_TEMPLATE, isDefault: true }
  }

  async sendClientEmail(input: SendEmailInput): Promise<Outreach> {
    if (this.delayMs > 0) await new Promise((r) => setTimeout(r, this.delayMs))
    const company = this.ownCompany(input.companyId)
    const recipient = input.recipient.trim()
    if (!EMAIL_PATTERN.test(recipient)) throw new AdapterError('validation_error', 'Die E-Mail Adresse ist ungültig.')
    if (!input.subject.trim() || !input.body.trim()) throw new AdapterError('validation_error', 'Betreff und Text dürfen nicht leer sein.')
    if (input.matchIds.length === 0) throw new AdapterError('validation_error', 'Bitte mindestens eine Gesetzesänderung auswählen.')
    const matches = input.matchIds.map((id) => this.ownMatch(id))
    if (matches.some((m) => m.companyId !== company.id))
      throw new AdapterError('validation_error', 'Die Auswahl gehört nicht zu diesem Mandanten.')

    // Same check as in the dialog, repeated here like a server would.
    const lawIds = new Set(this.state.matches.filter((m) => m.companyId === company.id).map((m) => m.lawChangeId))
    const advisor = this.state.advisors.find((a) => a.id === this.advisorId)!
    const terms = termsForClient(company, this.state.lawChanges.filter((l) => lawIds.has(l.id)), advisor)
    const hits = uniqueTerms(checkEmail(input.subject, input.body, terms))
    if (hits.length > 0)
      throw new AdapterError('law_terms_found', `Die E-Mail enthält gesetzesbezogene Begriffe: ${hits.join(', ')}`)

    // Nothing changes before the provider accepted the email.
    const { providerMessageId } = await this.emailSender.send({ recipient, subject: input.subject, body: input.body })

    const at = this.nowIso()
    const outreach: Outreach = {
      id: this.nextId('out'),
      companyId: company.id,
      matchIds: [...input.matchIds],
      recipient,
      subject: input.subject,
      body: input.body,
      sentAt: at,
      sentBy: this.advisorId,
      providerMessageId,
    }
    this.state.outreach.push(outreach)
    for (const m of matches) {
      m.review.emailStatus = 'sent'
      m.review.sentAt = at
      m.review.outreachId = outreach.id
      m.review.updatedBy = this.advisorId
      m.review.updatedAt = at
    }
    this.audit({ companyId: company.id, kind: 'email_sent', outreachId: outreach.id, summary: `E-Mail an ${recipient} gesendet` })
    this.persist()
    return structuredClone(outreach)
  }

  listOutreach(companyId: Id) {
    return this.respond(() => {
      this.ownCompany(companyId)
      return this.state.outreach.filter((o) => o.companyId === companyId).sort((a, b) => b.sentAt.localeCompare(a.sentAt))
    })
  }

  // ---------- history ----------

  listAuditEntries(companyId: Id) {
    return this.respond(() => {
      this.ownCompany(companyId)
      return this.state.audit
        .filter((a) => a.companyId === companyId)
        .sort((a, b) => b.actedAt.localeCompare(a.actedAt))
    })
  }

  getDemoFiles() {
    return this.respond(() =>
      this.seed.demoRun.documents.map((d) => ({ relativePath: d.relativePath, sizeBytes: d.sizeBytes ?? 0 })),
    )
  }

  resetDemo() {
    return this.respond(() => {
      this.storage?.removeItem(STORAGE_KEY)
      this.state = this.fresh()
    })
  }
}


/** After confirmation only filename and page remain; quotes, candidates and reasoning are dropped. */
function toConfirmedFact(fact: Fact, resolution: FactResolution | undefined): Fact {
  const stripQuotes = (sources: Fact['sources']) => sources.map(({ filename, page }) => ({ filename, page }))
  const base: Fact = {
    id: fact.id,
    group: fact.group,
    label: fact.label,
    type: fact.type,
    enumValues: fact.enumValues,
    unit: fact.unit,
    code: fact.code,
    coreField: fact.coreField,
    value: fact.value,
    sources: stripQuotes(fact.sources),
    reviewStatus: fact.reviewStatus,
  }
  if (!resolution) return base
  if (resolution.kind === 'left_empty') return { ...base, value: null, sources: [], reviewStatus: 'missing' }
  const candidate = fact.candidates?.find((c) => c.id === resolution.candidateId)
  // A picked candidate is backed only by its own documents (UI.md 12: confirmed = two or more agree).
  if (candidate)
    return {
      ...base,
      value: candidate.value,
      sources: stripQuotes(candidate.sources),
      reviewStatus: candidate.sources.length > 1 ? 'confirmed' : 'single_source',
    }
  const unchanged = JSON.stringify(resolution.value) === JSON.stringify(fact.value)
  // Accepting a value keeps its source status (single_source stays single_source).
  return unchanged ? base : { ...base, value: resolution.value, sources: [], reviewStatus: 'advisor_entered' }
}
