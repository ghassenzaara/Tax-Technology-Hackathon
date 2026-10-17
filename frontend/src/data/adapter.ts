import type {
  Advisor,
  AuditEntry,
  Company,
  Id,
  LawChange,
  Match,
  MatchReviewPatch,
  Outreach,
  ProfileDraft,
  UploadRun,
} from './types'

/** Error object as the backend sends it: machine code plus a message to show (UI.md 17). */
export class AdapterError extends Error {
  readonly code: string
  constructor(code: string, message: string) {
    super(message)
    this.code = code
    this.name = 'AdapterError'
  }
}

/** Core fields the advisor may edit. Ids, owner and timestamps are not editable. */
export type CompanyCorePatch = Partial<
  Pick<Company, 'name' | 'legalForm' | 'industry' | 'customerNumber' | 'clientSince' | 'contact'>
>

export interface CompanyUpdate {
  core?: CompanyCorePatch
  /** New values for existing facts, by fact id. */
  facts?: { id: Id; value: unknown }[]
}

export interface UploadFileInfo {
  /** Path inside the uploaded folder, kept as is (UI.md 14). */
  relativePath: string
  sizeBytes: number
  /** The file itself. The HTTP adapter sends it; the mock only reads the metadata. */
  file?: File
}

/** The advisor's choice for one draft fact. */
export type FactResolution = { kind: 'value'; value: unknown; candidateId?: Id } | { kind: 'left_empty' }

export interface EmailTemplate {
  subject: string
  body: string
}

/** The one stored template, with placeholders {Anrede} and {Signatur}. */
export interface StoredEmailTemplate extends EmailTemplate {
  isDefault: boolean
  updatedAt?: string
  updatedBy?: Id
}

/** One change of the stored template. Kept for the Verlauf in Einstellungen. */
export interface TemplateHistoryEntry {
  id: Id
  kind: 'template_edited' | 'template_reset' | (string & {})
  actedBy: Id
  actedAt: string
  oldSubject: string
  oldBody: string
  newSubject: string
  newBody: string
}

export interface SendEmailInput {
  companyId: Id
  recipient: string
  subject: string
  body: string
  matchIds: Id[]
}

export type MatchFilter = { companyId: Id } | { lawChangeId: Id } | Record<string, never>

/**
 * The only way the UI reads or writes data. Every method is scoped to the
 * current advisor: another advisor's company behaves as if it did not exist.
 */
export interface DataAdapter {
  getCurrentAdvisor(): Promise<Advisor>

  /** Confirmed clients only. Unfinished new profiles are not clients yet. */
  listCompanies(): Promise<Company[]>
  getCompany(id: Id): Promise<Company>
  /** New profiles started by an upload but not confirmed yet. */
  listDraftCompanies(): Promise<Company[]>
  /** Throws away an unconfirmed new profile, with its draft and runs. */
  discardDraftCompany(id: Id): Promise<void>
  updateCompany(id: Id, update: CompanyUpdate): Promise<Company>
  saveCompanyNote(id: Id, notes: string): Promise<Company>

  /**
   * Starts a processing run. Without a company id a new, unconfirmed client is
   * created and its id is returned in the run (UI.md 14).
   */
  uploadFiles(companyId: Id | null, files: UploadFileInfo[]): Promise<UploadRun>
  getRunProgress(runId: Id): Promise<UploadRun>
  getProfileDraft(companyId: Id): Promise<ProfileDraft>
  confirmProfile(companyId: Id, resolutions: Record<Id, FactResolution>): Promise<Company>

  listLawChanges(): Promise<LawChange[]>
  getLawChange(id: Id): Promise<LawChange>
  markLawChangeSeen(id: Id): Promise<void>

  listMatches(filter?: MatchFilter): Promise<Match[]>
  updateMatchReview(matchId: Id, patch: MatchReviewPatch): Promise<Match>

  /** The template filled in for one client (greeting and signature). */
  getEmailTemplate(companyId: Id): Promise<EmailTemplate>
  /** The stored template with placeholders, for Einstellungen. */
  getStoredEmailTemplate(): Promise<StoredEmailTemplate>
  /** Explicit "edit template" action. Applies to future emails only. */
  updateEmailTemplate(template: EmailTemplate): Promise<StoredEmailTemplate>
  /** Restore the default text. */
  resetEmailTemplate(): Promise<StoredEmailTemplate>
  /** All template changes, newest first. */
  listTemplateHistory(): Promise<TemplateHistoryEntry[]>
  sendClientEmail(input: SendEmailInput): Promise<Outreach>
  /** Snapshots of all emails sent to this client, newest first. */
  listOutreach(companyId: Id): Promise<Outreach[]>

  listAuditEntries(companyId: Id): Promise<AuditEntry[]>

  /** Mock only: restore the seed data. */
  resetDemo?(): Promise<void>
  /** Mock only: file names of the demo DD folder, with every document outcome. */
  getDemoFiles?(): Promise<UploadFileInfo[]>
}
