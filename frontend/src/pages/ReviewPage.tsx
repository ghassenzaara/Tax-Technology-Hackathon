import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ArrowRight, CheckCircle2, Plus } from 'lucide-react'
import { PageHeader } from '../components/PageHeader'
import { buttonClasses } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { ErrorState, LoadingState } from '../components/ui/States'
import { Stepper } from '../components/ui/Stepper'
import { useConfirmProfile } from '../data/mutations'
import { useCompany, useProfileDraft, useRunProgress } from '../data/queries'
import type { Company } from '../data/types'
import { ReviewForm } from '../features/review/ReviewForm'
import { RunProgress } from '../features/upload/RunProgress'
import { STEP, stepsFor } from '../features/upload/steps'

export function ReviewPage() {
  const { id = '' } = useParams()
  const [params] = useSearchParams()
  const runId = params.get('run')
  const company = useCompany(id)
  const run = useRunProgress(runId)
  const runFinished = !runId || (run.data && run.data.status !== 'running')
  const draft = useProfileDraft(id, !!runFinished && !!company.data)
  const confirm = useConfirmProfile(id)
  // Remember the flow type: after confirming, a new client is no longer a draft.
  const [doneAs, setDoneAs] = useState<{ wasNew: boolean; changed: number } | null>(null)

  if (company.isPending) return <LoadingState />
  if (company.isError) return <ErrorState error={company.error} />
  const c = company.data
  const isNew = doneAs ? doneAs.wasNew : c.status === 'draft'
  const profileHref = `/mandanten/${encodeURIComponent(c.id)}`

  return (
    <>
      <PageHeader
        title={isNew ? 'Prüfung und Profil erstellen' : 'Prüfung der neuen Werte'}
        description={isNew && !doneAs ? `${c.name} (noch nicht angelegt, erst nach Ihrer Bestätigung)` : c.name}
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
          ? `Das Profil von ${company.name} ist gespeichert. Klaris prüft ab jetzt wöchentlich, welche Gesetzesänderungen relevant sind.`
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
