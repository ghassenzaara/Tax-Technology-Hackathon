import { useQuery } from '@tanstack/react-query'
import { adapter } from './index'
import type { MatchFilter } from './adapter'
import type { Id } from './types'

// Query keys in one place so mutations can invalidate them.
export const keys = {
  advisor: ['advisor'] as const,
  companies: ['companies'] as const,
  company: (id: Id) => ['companies', id] as const,
  lawChanges: ['lawChanges'] as const,
  lawChange: (id: Id) => ['lawChanges', id] as const,
  matches: (filter: MatchFilter = {}) => ['matches', filter] as const,
  audit: (companyId: Id) => ['audit', companyId] as const,
  calendar: ['calendar'] as const,
}

export const useAdvisor = () => useQuery({ queryKey: keys.advisor, queryFn: () => adapter.getCurrentAdvisor() })
export const useCompanies = () => useQuery({ queryKey: keys.companies, queryFn: () => adapter.listCompanies() })
export const useCompany = (id: Id, enabled = true) =>
  useQuery({ queryKey: keys.company(id), queryFn: () => adapter.getCompany(id), enabled: enabled && !!id })
export const useLawChanges = () => useQuery({ queryKey: keys.lawChanges, queryFn: () => adapter.listLawChanges() })
export const useLawChange = (id: Id) => useQuery({ queryKey: keys.lawChange(id), queryFn: () => adapter.getLawChange(id) })
export const useMatches = (filter: MatchFilter = {}) =>
  useQuery({ queryKey: keys.matches(filter), queryFn: () => adapter.listMatches(filter) })
export const useAuditEntries = (companyId: Id, enabled = true) =>
  useQuery({ queryKey: keys.audit(companyId), queryFn: () => adapter.listAuditEntries(companyId), enabled })
export const useEmailTemplate = (companyId: Id, enabled = true) =>
  useQuery({ queryKey: ['emailTemplate', companyId], queryFn: () => adapter.getEmailTemplate(companyId), enabled })
export const useStoredEmailTemplate = () =>
  useQuery({ queryKey: ['storedEmailTemplate'], queryFn: () => adapter.getStoredEmailTemplate() })
export const useTemplateHistory = (enabled = true) =>
  useQuery({ queryKey: ['templateHistory'], queryFn: () => adapter.listTemplateHistory(), enabled })
/** Polls every 1.5 s while the run is active (UI.md 16). */
export const useRunProgress = (runId: Id | null) =>
  useQuery({
    queryKey: ['run', runId],
    queryFn: () => adapter.getRunProgress(runId!),
    enabled: !!runId,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 1500 : false),
  })
export const useProfileDraft = (companyId: Id, enabled = true) =>
  useQuery({ queryKey: ['draft', companyId], queryFn: () => adapter.getProfileDraft(companyId), enabled, retry: false })
export const useDraftCompanies = () => useQuery({ queryKey: ['draftCompanies'], queryFn: () => adapter.listDraftCompanies() })
export const useOutreach = (companyId: Id, enabled = true) =>
  useQuery({ queryKey: ['outreach', companyId], queryFn: () => adapter.listOutreach(companyId), enabled })
export const useCalendar = () =>
  useQuery({ queryKey: keys.calendar, queryFn: () => adapter.listCalendar!(), enabled: !!adapter.listCalendar })
