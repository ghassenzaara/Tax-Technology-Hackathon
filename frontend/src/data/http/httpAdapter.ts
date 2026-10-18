import { AdapterError, type DataAdapter, type CompanyUpdate, type UploadFileInfo, type FactResolution, type MatchFilter, type EmailTemplate, type StoredEmailTemplate, type TemplateHistoryEntry, type SendEmailInput, type ResponseInput } from '../adapter'
import type { Advisor, AuditEntry, CalendarEntry, Company, LawChange, Match, MatchReviewPatch, Outreach, ProfileDraft, UploadRun, Job, Processing } from '../types'

const id = encodeURIComponent

export class HttpAdapter implements DataAdapter {
  private versions = new Map<string, number>()
  private drafts = new Map<string, string>()
  constructor(private baseUrl = '', private fetcher: typeof fetch = globalThis.fetch.bind(globalThis)) {}

  private async request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
    let response: Response
    try {
      response = await this.fetcher(`${this.baseUrl.replace(/\/$/, '')}/api/ui${path}`, {
        method, cache: 'no-store',
        headers: body instanceof FormData || body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
      })
    } catch {
      throw new AdapterError('network_error', 'Der Server ist nicht erreichbar. Bitte die Verbindung prüfen.')
    }
    if (response.status === 204) return undefined as T
    const value = await response.json().catch(() => {
      throw new AdapterError('invalid_response', 'Der Server hat eine ungültige Antwort geliefert.')
    })
    if (!response.ok) {
      const details = value?.details?.map((d: { field?: string; message: string }) => `${d.field ? `${d.field}: ` : ''}${d.message}`).join(' ')
      throw new AdapterError(value?.code ?? 'http_error', [value?.message ?? `Serverfehler (${response.status})`, details].filter(Boolean).join(' '))
    }
    return value as T
  }

  private remember(company: Company): Company {
    if (typeof company.extra?.profileVersion === 'number') this.versions.set(company.id, company.extra.profileVersion)
    return company
  }
  getCurrentAdvisor = () => this.request<Advisor>('/advisor')
  listCompanies = async () => (await this.request<Company[]>('/companies')).map((c) => this.remember(c))
  listDraftCompanies = () => this.request<Company[]>('/companies/drafts')
  getCompany = async (cid: string) => this.remember(await this.request<Company>(`/companies/${id(cid)}`))
  discardDraftCompany = (cid: string) => this.request<void>(`/companies/${id(cid)}`, 'DELETE')
  updateCompany = async (cid: string, update: CompanyUpdate) => {
    const version = update.profileVersion ?? this.versions.get(cid)
    if (version === undefined) throw new AdapterError('stale_profile', 'Bitte das Profil vor der Bearbeitung neu laden.')
    return this.remember(await this.request<Company>(`/companies/${id(cid)}/update`, 'POST', { ...update, profileVersion: version }))
  }
  saveCompanyNote = async (cid: string, notes: string) => this.remember(await this.request<Company>(`/companies/${id(cid)}/notes`, 'POST', { notes }))
  uploadFiles = async (companyId: string | null, files: UploadFileInfo[]) => {
    const form = new FormData()
    for (const item of files) {
      if (!item.file) throw new AdapterError('validation_error', 'Bitte die Originaldateien auswählen.')
      form.append('files', item.file, item.file.name)
      form.append('paths', item.relativePath)
    }
    if (companyId) form.append('companyId', companyId)
    return this.request<UploadRun>('/uploads', 'POST', form)
  }
  getRunProgress = (runId: string) => this.request<UploadRun>(`/runs/${id(runId)}`)
  getProfileDraft = async (cid: string) => {
    const draft = await this.request<ProfileDraft>(`/companies/${id(cid)}/draft`)
    if (draft.draftVersion) this.drafts.set(cid, draft.draftVersion)
    return draft
  }
  confirmProfile = async (cid: string, resolutions: Record<string, FactResolution>, draftVersion?: string) => {
    const version = draftVersion ?? this.drafts.get(cid)
    if (!version) throw new AdapterError('stale_draft', 'Bitte den Entwurf vor der Bestätigung neu laden.')
    return this.remember(await this.request<Company>(`/companies/${id(cid)}/confirm`, 'POST', { resolutions, draftVersion: version }))
  }
  listLawChanges = () => this.request<LawChange[]>('/law-changes')
  getLawChange = (lawId: string) => this.request<LawChange>(`/law-changes/${id(lawId)}`)
  markLawChangeSeen = (lawId: string) => this.request<void>(`/law-changes/${id(lawId)}/seen`, 'POST')
  listMatches = (filter: MatchFilter = {}) => {
    const params = new URLSearchParams(filter)
    return this.request<Match[]>(`/matches${params.size ? `?${params}` : ''}`)
  }
  updateMatchReview = (matchId: string, patch: MatchReviewPatch) => this.request<Match>(`/matches/${id(matchId)}/review`, 'POST', patch)
  getEmailTemplate = (cid: string) => this.request<EmailTemplate>(`/companies/${id(cid)}/email-template`)
  getStoredEmailTemplate = () => this.request<StoredEmailTemplate>('/email-template')
  updateEmailTemplate = (template: EmailTemplate) => this.request<StoredEmailTemplate>('/email-template', 'POST', template)
  resetEmailTemplate = () => this.request<StoredEmailTemplate>('/email-template/reset', 'POST')
  listTemplateHistory = () => this.request<TemplateHistoryEntry[]>('/email-template/history')
  sendClientEmail = (input: SendEmailInput) => this.request<Outreach>('/emails', 'POST', { ...input, confirmed: true })
  listOutreach = (cid: string) => this.request<Outreach[]>(`/companies/${id(cid)}/outreach`)
  listAuditEntries = (cid: string) => this.request<AuditEntry[]>(`/companies/${id(cid)}/audit`)
  resetDemo = () => this.request<void>('/reset', 'POST')
  listCalendar = () => this.request<CalendarEntry[]>('/calendar')
  recordResponse = (outreachId: string, input: ResponseInput) =>
    this.request<CalendarEntry>(`/outreach/${id(outreachId)}/response`, 'POST', input)
  startScreening = () => this.request<Job>('/screening-runs', 'POST')
  getLatestScreening = () => this.request<Job | null>('/screening-runs/latest')
  rerunCompany = (cid: string) => this.request<UploadRun>(`/companies/${id(cid)}/rerun`, 'POST')
  getJob = (jobId: string) => this.request<Job>(`/jobs/${id(jobId)}`)
  retryJob = (jobId: string) => this.request<Job>(`/jobs/${id(jobId)}/retry`, 'POST')
  getProcessing = (cid: string) => this.request<Processing>(`/companies/${id(cid)}/processing`)
  recoverCompany = (cid: string) => this.request<Job | null>(`/companies/${id(cid)}/recover`, 'POST')
}
