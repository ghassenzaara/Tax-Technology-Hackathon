import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, ScanSearch } from 'lucide-react'
import { adapter } from '../../data'
import type { Job } from '../../data/types'
import { Button } from '../../components/ui/Button'
import { ErrorState } from '../../components/ui/States'
import { useActivities } from '../activity/ActivityProvider'

const running = (job?: Job) => !!job && ['queued', 'running'].includes(job.status)

interface ScreenedDocument { filename: string; title: string; new: boolean }

const screenedDocuments = (result?: Record<string, unknown>) =>
  Array.isArray(result?.documents) ? (result.documents as ScreenedDocument[]) : []

export function ScreeningControl() {
  const qc = useQueryClient()
  const [job, setJob] = useState<Job>()
  const latest = useQuery({ queryKey: ['screeningJob', 'latest'],
    queryFn: () => adapter.getLatestScreening!(), enabled: !!adapter.getLatestScreening,
    refetchInterval: (q) => running(q.state.data ?? undefined) ? 1500 : false })
  const { activities, track } = useActivities()
  const started = (next: Job) => {
    setJob(next)
    track({ id: next.id, kind: 'screening', label: 'Screening' })
  }
  const start = useMutation({ mutationFn: () => adapter.startScreening!(), onSuccess: started })
  const retry = useMutation({ mutationFn: () => adapter.retryJob!(current!.id), onSuccess: started })
  const poll = useQuery({
    queryKey: ['screeningJob', job?.id], queryFn: () => adapter.getJob!(job!.id),
    enabled: !!job && !!adapter.getJob,
    refetchInterval: (q) => running(q.state.data ?? job) ? 1500 : false,
    retry: false,
  })
  const current = poll.data ?? job ?? latest.data ?? undefined
  useEffect(() => {
    if (current?.status === 'succeeded') {
      qc.invalidateQueries({ queryKey: ['lawChanges'] })
      qc.invalidateQueries({ queryKey: ['matches'] })
      qc.invalidateQueries({ queryKey: ['processing'] })
    }
  }, [current?.id, current?.status, qc])
  if (!adapter.startScreening) return null
  // The top bar keeps a quick screening "running" for a moment; the card follows it.
  const busy = running(current) || activities.some((a) => a.id === current?.id && a.state === 'running')
  const failed = current && ['failed', 'interrupted'].includes(current.status)
  const result = current?.status === 'succeeded' && !busy ? current.result : undefined
  return <div className="mb-6 rounded-xl border border-line bg-surface p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-3">
        <ScanSearch className="size-5 text-primary" aria-hidden />
        <div><p className="text-sm font-semibold">Aktuelle und zukünftige Entwicklungen prüfen</p>
          <p className="mt-0.5 text-xs text-ink-muted" role="status" aria-live="polite">
            {busy ? 'Quellen werden geprüft und Mandanten abgeglichen …' : 'Neue Quellen erkennen und alle Mandanten prüfen.'}
          </p>
        </div>
      </div>
      <Button onClick={() => failed ? retry.mutate() : start.mutate()} disabled={busy || start.isPending || retry.isPending}>
        {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
        {failed ? 'Erneut versuchen' : busy ? 'Prüfung läuft' : 'Screening starten'}
      </Button>
    </div>
    {result && <div className="mt-3 text-sm" role="status">
      <p>
        {Number(result.lawChanges ?? 0) === 0
          ? 'Sie sind auf dem neuesten Stand. Keine neuen Dokumente gefunden.'
          : `${Number(result.lawChanges)} neue Gesetzesänderungen gefunden · ${Array.isArray(result.affectedCompanyIds) ? result.affectedCompanyIds.length : 0} Mandanten betroffen`}
        {result.complete === false && ' · Weitere Prüfung erforderlich. Bitte Screening erneut starten.'}
      </p>
      {screenedDocuments(result).length > 0 && <ul className="mt-2 space-y-1 text-xs text-ink-muted">
        {screenedDocuments(result).map((d) => <li key={d.filename} className="flex gap-2">
          <span className={d.new ? 'font-semibold text-primary' : ''}>{d.new ? 'Neu' : 'Bereits geprüft'}</span>
          <span className="min-w-0 truncate" title={d.filename}>{d.title || d.filename}</span>
        </li>)}
      </ul>}
    </div>}
    {failed && <p role="alert" className="mt-3 text-sm text-danger">{current.error ?? 'Die Prüfung wurde unterbrochen.'}</p>}
    {(start.error || retry.error || poll.error || latest.error) && <ErrorState error={start.error ?? retry.error ?? poll.error ?? latest.error} />}
  </div>
}
