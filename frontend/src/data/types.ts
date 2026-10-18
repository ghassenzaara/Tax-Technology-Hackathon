// Mirror of the JSON Schemas in screening/contract (version 0.1.0).
// Open vocabularies are typed as known values plus string, so unknown values
// from the backend still type check and render as plain text.

export const SCHEMA_VERSION = '0.1.0'

type Open<T extends string> = T | (string & {})

export type Id = string

export interface Source {
  filename: string
  page: number
  quote?: string
}

export interface ApiError {
  code: string
  message: string
}

export interface ValueWithSources {
  value: unknown
  sources: Source[]
}

export interface Candidate extends ValueWithSources {
  id: Id
  alternatives?: ValueWithSources[]
}

export type FactType = Open<'string' | 'integer' | 'number' | 'boolean' | 'date' | 'enum' | 'list' | 'object'>
export type ReviewStatus = Open<'confirmed' | 'single_source' | 'conflict' | 'missing'>

export interface Fact {
  id: Id
  group?: string
  label: string
  value?: unknown
  type: FactType
  enumValues?: unknown[]
  unit?: string
  code?: string
  /** Company core field this fact fills on confirmation, for example "name" or "contact.email". */
  coreField?: string
  sources: Source[]
  reviewStatus?: ReviewStatus
  needsReview?: boolean
  reviewReason?: string
  reasoning?: string
  candidates?: Candidate[]
  alternatives?: ValueWithSources[]
}

export interface Contact {
  salutation?: string
  name: string
  email?: string
  phone?: string
}

export type CompanyStatus = Open<'draft' | 'active' | 'archived'>

export interface Company {
  id: Id
  advisorId: Id
  name: string
  status: CompanyStatus
  createdAt: string
  updatedAt: string
  confirmedAt?: string
  confirmedBy?: Id
  lastDdAt?: string
  contact: Contact
  customerNumber?: string
  clientSince?: string
  legalForm?: string
  industry?: string
  facts: Fact[]
  extra?: Record<string, unknown>
  notes?: string
}

export type SourceType = Open<'gesetzentwurf' | 'bmf_schreiben' | 'gesetz' | 'urteil'>

export interface LawSection {
  id: Id
  ref: string
  heading?: string
  text: string
}

export interface LawBlock {
  kind: Open<'summary' | 'changes' | 'impact' | 'next_steps'>
  title: string
  text?: string
  items?: string[]
  generated?: boolean
}

export interface LawChange {
  id: Id
  title: string
  sourceType: SourceType
  sourceName: string
  url: string
  publishedAt: string
  effectiveAt?: string
  detectedAt: string
  version: number
  contentHash: string
  tags?: {
    taxTypes?: string[]
    legalForms?: string[]
    industries?: string[]
    norms?: string[]
    thresholds?: Record<string, unknown>[]
  }
  sections: LawSection[]
  blocks: LawBlock[]
  extra?: Record<string, unknown>
}

export interface Evidence {
  factId?: Id
  factLabel: string
  sectionRef: string
  similarity?: number
}

export interface MatchAssessment {
  current?: boolean
  invalidationReason?: string
  score: number
  confidence: number
  reason: string
  evidence: Evidence[]
  lawVersion: number
  screenedAt: string
  runId: Id
}

export type MatchLabel = Open<'unlabeled' | 'relevant' | 'irrelevant'>
export type EmailStatus = Open<'not_sent' | 'sent' | 'discussed'>

export interface MatchReview {
  sendPending?: boolean
  label: MatchLabel
  emailStatus: EmailStatus
  notes?: string
  sentAt?: string
  outreachId?: Id
  discussedAt?: string
  updatedBy?: Id
  updatedAt?: string
}

export interface Match {
  id: Id
  companyId: Id
  lawChangeId: Id
  assessment: MatchAssessment
  review: MatchReview
}

/** Fields the app may change on a match. The assessment part is never writable here. */
export type MatchReviewPatch = Partial<Pick<MatchReview, 'label' | 'emailStatus' | 'notes'>>

export interface Outreach {
  id: Id
  companyId: Id
  matchIds: Id[]
  recipient: string
  subject: string
  body: string
  sentAt: string
  sentBy: Id
  providerMessageId?: string
}

/** The client's reply to a sent email, recorded by hand in the calendar. */
export interface CalendarResponse {
  outcome: 'accepted' | 'rejected'
  receivedOn: string
  /** The Besprechung, only when accepted. */
  meetingAt?: string
  note?: string
  recordedAt: string
}

/** One sent email on the calendar. */
export interface CalendarEntry {
  outreachId: Id
  companyId: Id
  companyName: string
  recipient: string
  subject: string
  sentAt: string
  status: 'awaiting' | 'accepted' | 'rejected'
  response?: CalendarResponse
}

export interface Advisor {
  id: Id
  name: string
  salutation?: string
  title?: string
  firm?: string
  seenLawChangeIds?: Id[]
}

export type AuditKind = Open<
  'profile_confirmed' | 'profile_changed' | 'match_review_changed' | 'email_sent' | 'note_changed' | 'documents_added'
>

export interface AuditEntry {
  id: Id
  companyId: Id
  kind: AuditKind
  actedBy: Id
  actedAt: string
  field?: string
  oldValue?: unknown
  newValue?: unknown
  matchId?: Id
  outreachId?: Id
  summary?: string
}

export interface UploadDocument {
  id: Id
  relativePath: string
  sizeBytes?: number
  fileType?: Open<'text_pdf' | 'scanned_pdf' | 'excel' | 'unsupported'>
  parsingStatus: Open<'ok' | 'partial' | 'failed' | 'unsupported'>
  extractionStatus: Open<'pending' | 'ok' | 'partial' | 'failed' | 'skipped' | 'write_failed'>
  warnings?: string[]
  error?: ApiError
}

export interface UploadRun {
  error?: ApiError
  schemaVersion: string
  id: Id
  companyId: Id
  status: Open<'running' | 'finished' | 'failed'>
  startedAt: string
  finishedAt?: string
  documents: UploadDocument[]
}

export interface ProfileDraft {
  draftVersion?: string
  schemaVersion: string
  companyId: Id
  runId: Id
  createdAt?: string
  facts: Fact[]
}

export interface Collection<T> {
  schemaVersion: string
  runId?: Id
  generatedAt?: string
  items: T[]
}

export interface Job {
  id: Id
  operation: string
  companyId?: Id
  status: Open<'queued' | 'running' | 'succeeded' | 'failed' | 'interrupted'>
  error?: string
  retryOf?: Id
  createdAt: string
  finishedAt?: string
  result?: Record<string, unknown>
}

export interface Processing {
  companyId: Id
  state?: string
  embeddingsStale: boolean
  activeJob?: Job
  latestJob?: Job
  recovery: { available: boolean; action?: string; reason: string }
  screeningPending: boolean
  updatePending: boolean
}
