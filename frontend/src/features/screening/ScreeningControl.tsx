import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ScanSearch } from 'lucide-react'
import { adapter } from '../../data'
import type { Job } from '../../data/types'
import { Button } from '../../components/ui/Button'
import { ErrorState } from '../../components/ui/States'

const running = (job?: Job) => !!job && ['queued', 'running'].includes(job.status)

export function ScreeningControl() {
  const qc = useQueryClient()
  const [job, setJob] = useState<Job>()
  const latest = useQuery({ queryKey: ['screeningJob', 'latest'],
    queryFn: () => adapter.getLatestScreening!(), enabled: !!adapter.getLatestScreening,
    refetchInterval: (q) => running(q.state.data ?? undefined) ? 1500 : false })
  const start = useMutation({ mutationFn: () => adapter.startScreening!(), onSuccess: setJob })
  const retry = useMutation({ mutationFn: () => adapter.retryJob!(current!.id), onSuccess: setJob })
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
  const failed = current && ['failed', 'interrupted'].includes(current.status)
  const result = current?.status === 'succeeded' ? current.result : undefined
  return <div className="mb-6 rounded-xl border border-line bg-surface p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-3">
        <ScanSearch className="size-5 text-primary" aria-hidden />
        <div><p className="text-sm font-semibold">Aktuelle Entwicklungen prüfen</p>
          <p className="mt-0.5 text-xs text-ink-muted" role="status" aria-live="polite">
            {running(current) ? 'Quellen werden geprüft und Mandanten abgeglichen …' : 'Neue Quellen erkennen und alle Mandanten prüfen.'}
          </p>
        </div>
      </div>
      <Button onClick={() => failed ? retry.mutate() : start.mutate()} disabled={running(current) || start.isPending || retry.isPending}>
        {failed ? 'Erneut versuchen' : 'Screening starten'}
      </Button>
    </div>
    {result && <p className="mt-3 text-sm" role="status">
      {Number(result.lawChanges ?? 0)} neue Gesetzesänderungen gefunden · {Array.isArray(result.affectedCompanyIds) ? result.affectedCompanyIds.length : 0} Mandanten betroffen
      {result.complete === false && ' · Weitere Prüfung erforderlich. Bitte Screening erneut starten.'}
    </p>}
    {failed && <p role="alert" className="mt-3 text-sm text-danger">{current.error ?? 'Die Prüfung wurde unterbrochen.'}</p>}
    {(start.error || retry.error || poll.error || latest.error) && <ErrorState error={start.error ?? retry.error ?? poll.error ?? latest.error} />}
  </div>
}
