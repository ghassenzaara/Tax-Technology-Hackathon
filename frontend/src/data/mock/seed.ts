// The only place that imports the temporary mock data.
import advisors from '../../mock-data/advisors.json'
import auditLog from '../../mock-data/auditLog.json'
import companies from '../../mock-data/companies.json'
import lawChanges from '../../mock-data/lawChanges.json'
import matches from '../../mock-data/matches.json'
import outreach from '../../mock-data/outreach.json'
import profileDraft from '../../mock-data/profileDraft.json'
import uploadRun from '../../mock-data/uploadRun.json'
import type {
  Advisor,
  AuditEntry,
  Collection,
  Company,
  LawChange,
  Match,
  Outreach,
  ProfileDraft,
  UploadRun,
} from '../types'

export interface SeedData {
  advisors: Advisor[]
  companies: Company[]
  lawChanges: LawChange[]
  matches: Match[]
  outreach: Outreach[]
  audit: AuditEntry[]
  /** Template for every new draft. */
  draftTemplate: ProfileDraft
  /** Known demo documents with fixed outcomes, matched by file name. */
  demoRun: UploadRun
}

export function loadSeed(): SeedData {
  return structuredClone({
    advisors: (advisors as Collection<Advisor>).items,
    companies: (companies as Collection<Company>).items,
    lawChanges: (lawChanges as Collection<LawChange>).items,
    matches: (matches as Collection<Match>).items,
    outreach: (outreach as Collection<Outreach>).items,
    audit: (auditLog as Collection<AuditEntry>).items,
    draftTemplate: profileDraft as ProfileDraft,
    demoRun: uploadRun as UploadRun,
  })
}
