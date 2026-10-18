import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { adapter, isMockData } from '../data'
import { ArrowRight, CheckCircle2, Plus } from 'lucide-react'
import { PageHeader } from '../components/PageHeader'
import { buttonClasses } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { ErrorState, LoadingState } from '../components/ui/States'
import { Stepper } from '../components/ui/Stepper'
import { useConfirmProfile } from '../data/mutations'
import { keys, useCompany, useProfileDraft, useRunProgress } from '../data/queries'
import type { Company } from '../data/types'
import { ReviewForm } from '../features/review/ReviewForm'
import { RunProgress } from '../features/upload/RunProgress'
import { STEP, stepsFor } from '../features/upload/steps'

export function ReviewPage() {
  const { id = '' } = useParams()
  const [params] = useSearchParams()
  const runId = params.get('run')
  return !isMockData && runId ? <AutomaticSave companyId={id} runId={runId} /> : <ManualReview />
}

function AutomaticSave({ companyId, runId }: { companyId: string; runId: string }) {
  const run = useRunProgress(runId)
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    if (run.data?.status !== 'finished') return
    let cancelled = false
    adapter.getCompany(companyId).then((company) => {
      if (cancelled) return
      if (company.status === 'draft') throw new Error('Es konnten keine verwertbaren Profildaten gespeichert werden. Bitte prüfen Sie die Dokumente.')
      qc.setQueryData(keys.company(companyId), company)
      qc.invalidateQueries({ queryKey: keys.companies })
      qc.invalidateQueries({ queryKey: ['draftCompanies'] })
      navigate(`/mandanten/${encodeURIComponent(companyId)}`, { replace: true })
    }).catch((problem) => { if (!cancelled) setError(problem) })
    return () => { cancelled = true }
  }, [run.data?.status, companyId, navigate, qc])
  return <>
    <PageHeader title="Profil wird erstellt" description="Die Angaben werden aus den Dokumenten übernommen und automatisch gespeichert. Fehlende Informationen können Sie anschließend ergänzen." />
    {run.isPending && <LoadingState label="Verarbeitung wird geladen" />}
    {(run.error || error) ? <ErrorState error={run.error ?? error} /> : null}
    {run.data && <RunProgress run={run.data} />}
  </>
}

function ManualReview() {
  const { id = '' } = useParams()
  const [params] = useSearchParams()
  const runId = params.get('run')
  const [doneAs, setDoneAs] = useState<{ wasNew: boolean; changed: number } | null>(null)
  const company = useCompany(id)
  const run = useRunProgress(runId)
  const runFinished = !runId || (run.data && run.data.status !== 'running')
  const draft = useProfileDraft(id, !doneAs && !!runFinished && !!company.data)
  const confirm = useConfirmProfile(id, draft.data?.draftVersion)
  // Remember the flow type: after confirming, a new client is no longer a draft.

  if (company.isPending) return <LoadingState />
  if (company.isError) return <ErrorState error={company.error} />
  const c = company.data
  const isNew = doneAs ? doneAs.wasNew : c.status === 'draft'
  const extractedName = draft.data?.facts.find((fact) => fact.coreField === 'name')?.value
  const displayName = isNew && typeof extractedName === 'string' && extractedName.trim()
    ? extractedName : c.name === c.id && isNew ? 'Neuer Mandant' : c.name
  const profileHref = `/mandanten/${encodeURIComponent(c.id)}`

  return (
    <>
      <PageHeader
        title={isNew ? 'Prüfung und Profil erstellen' : 'Prüfung der neuen Werte'}
        description={isNew && !doneAs ? `${displayName} · Entwurf zur Prüfung` : c.name}
        back={doneAs ? undefined : { to: isNew ? '/mandanten' : profileHref, label: isNew ? 'Später fortsetzen' : 'Zurück zum Mandanten' }}
      />
      <Stepper steps={stepsFor(isNew)} current={doneAs ? STEP.done : STEP.review} />

      {doneAs ? (
        <Done company={c} wasNew={doneAs.wasNew} changed={doneAs.changed} />
      ) : (
        <>
          {runId && run.isPending && <LoadingState label="Verarbeitung wird geladen" />}
          {run.isError && <ErrorState error={run.error} />}
          {run.data && <RunProgress run={run.data} />}

          {runFinished && draft.isPending && <LoadingState label="Profilentwurf wird geladen" />}
          {draft.isError && (
            <div className="mt-6">
              <ErrorState error={draft.error} />
              <Link to={`${profileHref}/upload`} className="mt-3 inline-block text-sm font-medium text-primary link-underline">
                Dokumente hochladen
              </Link>
            </div>
          )}
          {draft.data && (
            <ReviewForm
              draft={draft.data}
              company={c}
              confirming={confirm.isPending}
              error={confirm.error}
              onConfirm={(resolutions) => {
                const wasNew = c.status === 'draft'
                confirm.mutate(resolutions, { onSuccess: () => setDoneAs({ wasNew, changed: draft.data.facts.length }) })
              }}
            />
          )}
        </>
      )}
    </>
  )
}

function Done({ company, wasNew, changed }: { company: Company; wasNew: boolean; changed: number }) {
  const profileHref = `/mandanten/${encodeURIComponent(company.id)}`
  return (
    <Card className="mx-auto max-w-xl px-6 py-10 text-center">
      <span className="mx-auto grid size-14 place-items-center rounded-full bg-success-soft text-success">
        <CheckCircle2 className="size-7" aria-hidden />
      </span>
      <h2 className="mt-4 text-lg font-semibold">{wasNew ? 'Profil bestätigt' : 'Änderungen übernommen'}</h2>
      <p className="mt-1 text-sm text-ink-muted">
        {wasNew
          ? `Das Profil von ${company.name} ist gespeichert. Mit „Screening starten“ prüfen Sie aktuelle und zukünftige Entwicklungen für diesen Mandanten.`
          : changed === 0
            ? 'Es gab keine neuen oder abweichenden Werte.'
            : `${changed} ${changed === 1 ? 'Wert wurde' : 'Werte wurden'} geprüft und ins Profil übernommen.`}
      </p>
      <p className="mt-1 text-xs text-ink-subtle">Die hochgeladenen Dokumente werden nach der Auswertung gelöscht.</p>
      <div className="mt-6 flex flex-wrap justify-center gap-2">
        <Link to={profileHref} className={buttonClasses('primary', 'md', 'group')}>
          Zum Mandantenprofil
          <ArrowRight className="nudge size-4" aria-hidden />
        </Link>
        {wasNew && (
          <Link to="/mandanten/neu" className={buttonClasses('secondary')}>
            <Plus className="size-4" aria-hidden />
            Weiteren Mandanten anlegen
          </Link>
        )}
      </div>
    </Card>
  )
}
