import { useMutation, useQueryClient } from '@tanstack/react-query'
import { adapter } from './index'
import type { CompanyUpdate, EmailTemplate, FactResolution, SendEmailInput, StoredEmailTemplate, UploadFileInfo } from './adapter'
import type { Id, MatchReviewPatch, ProfileDraft } from './types'
import { keys } from './queries'

export function useUpdateMatchReview() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ matchId, patch }: { matchId: Id; patch: MatchReviewPatch }) => adapter.updateMatchReview(matchId, patch),
    onSuccess: (match) => {
      qc.invalidateQueries({ queryKey: ['matches'] })
      qc.invalidateQueries({ queryKey: keys.audit(match.companyId) })
    },
  })
}

export function useUpdateCompany(companyId: Id) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (update: CompanyUpdate) => adapter.updateCompany(companyId, update),
    onSuccess: (company) => {
      qc.setQueryData(keys.company(companyId), company)
      qc.invalidateQueries({ queryKey: keys.companies, exact: true })
      qc.invalidateQueries({ queryKey: ['emailTemplate', companyId] })
      qc.invalidateQueries({ queryKey: keys.audit(companyId) })
      qc.invalidateQueries({ queryKey: ['processing', companyId] })
      qc.invalidateQueries({ queryKey: ['matches'] })
    },
  })
}

export function useSaveCompanyNote(companyId: Id) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (notes: string) => adapter.saveCompanyNote(companyId, notes),
    onSuccess: (company) => qc.setQueryData(keys.company(companyId), company),
  })
}

export function useSendClientEmail() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: SendEmailInput) => adapter.sendClientEmail(input),
    onSuccess: (outreach) => {
      qc.invalidateQueries({ queryKey: ['matches'] })
      qc.invalidateQueries({ queryKey: keys.audit(outreach.companyId) })
      qc.invalidateQueries({ queryKey: ['outreach', outreach.companyId] })
    },
  })
}

/** Saving or resetting the template refreshes every filled draft. */
export function useSaveEmailTemplate() {
  const qc = useQueryClient()
  const refresh = (stored: StoredEmailTemplate) => {
    qc.setQueryData(['storedEmailTemplate'], stored)
    qc.invalidateQueries({ queryKey: ['emailTemplate'] })
    qc.invalidateQueries({ queryKey: ['templateHistory'] })
  }
  return {
    update: useMutation({ mutationFn: (t: EmailTemplate) => adapter.updateEmailTemplate(t), onSuccess: refresh }),
    reset: useMutation({ mutationFn: () => adapter.resetEmailTemplate(), onSuccess: refresh }),
  }
}

export function useMarkLawChangeSeen() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: Id) => adapter.markLawChangeSeen(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.advisor }),
  })
}

/** Throws away an unfinished new profile. */
export function useDiscardDraftCompany() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: Id) => adapter.discardDraftCompany(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['draftCompanies'] }),
  })
}

/** `companyId` null starts a new client: the adapter creates it and returns its id in the run. */
export function useUploadFiles(companyId: Id | null) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (files: UploadFileInfo[]) => adapter.uploadFiles(companyId, files),
    onSuccess: (run) => {
      qc.setQueryData(['run', run.id], run)
      qc.removeQueries({ queryKey: ['draft', run.companyId] })
      qc.invalidateQueries({ queryKey: keys.company(run.companyId) })
      qc.invalidateQueries({ queryKey: keys.audit(run.companyId) })
      qc.invalidateQueries({ queryKey: ['draftCompanies'] })
    },
  })
}

export function useConfirmProfile(companyId: Id, draftVersion?: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (resolutions: Record<Id, FactResolution>) => adapter.confirmProfile(companyId, resolutions, draftVersion),
    onSuccess: (company) => {
      qc.setQueryData(keys.company(companyId), company)
      qc.invalidateQueries({ queryKey: keys.companies, exact: true })
      qc.invalidateQueries({ queryKey: keys.audit(companyId) })
      // Clear review evidence without recreating an enabled query after confirmation.
      qc.setQueryData<ProfileDraft>(['draft', companyId], (draft) => draft ? { ...draft, facts: [] } : undefined)
      qc.invalidateQueries({ queryKey: ['emailTemplate', companyId] })
      qc.invalidateQueries({ queryKey: ['draftCompanies'] })
      qc.invalidateQueries({ queryKey: ['processing', companyId] })
    },
  })
}
