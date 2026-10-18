import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { adapter } from '../../data'
import { Button } from '../../components/ui/Button'
import { ErrorState } from '../../components/ui/States'

export function ProcessingStatus({ companyId }: { companyId: string }) {
  const qc = useQueryClient()
  const status = useQuery({
    queryKey: ['processing', companyId], queryFn: () => adapter.getProcessing!(companyId),
    enabled: !!adapter.getProcessing,
    refetchInterval: (q) => q.state.data?.activeJob ? 1500 : false,
  })
  const action = useMutation({
    mutationFn: async () => {
      const failed = status.data?.latestJob
      if (failed && ['failed', 'interrupted'].includes(failed.status)) return adapter.retryJob!(failed.id)
      return adapter.recoverCompany!(companyId)
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['processing', companyId] }),
  })
  const data = status.data
  useEffect(() => {
    if (data && !data.activeJob && !data.embeddingsStale) {
      qc.invalidateQueries({ queryKey: ['companies', companyId] })
      qc.invalidateQueries({ queryKey: ['matches'] })
    }
  }, [data?.activeJob?.id, data?.embeddingsStale, qc, companyId])
  if (!adapter.getProcessing) return null
  if (status.error) return <ErrorState error={status.error} />
  if (!data) return null
  const failed = data.latestJob && ['failed', 'interrupted'].includes(data.latestJob.status)
  if (!data.activeJob && !failed && !data.recovery.available && !data.updatePending && !data.screeningPending) return null
  return <div className="mb-5 rounded-lg border border-line bg-canvas p-4 text-sm" role="status">
    {data.updatePending ? <Link to={`/mandanten/${encodeURIComponent(companyId)}/pruefung`} className="font-medium text-primary underline">Neue Dokumente prüfen und Änderungen übernehmen</Link>
      : data.activeJob ? <p>Profilverarbeitung läuft …</p>
      : <>
        {failed && <p className="mb-2">{data.latestJob?.error ?? 'Die Verarbeitung wurde unterbrochen.'}</p>}
        {(failed || data.recovery.available) && <Button disabled={action.isPending} onClick={() => action.mutate()}>{failed ? 'Erneut versuchen' : 'Verarbeitung abschließen'}</Button>}
        {data.screeningPending && <p className="mt-2 text-ink-muted">Screening ausstehend. Unter Gesetzesänderungen die Prüfung starten.</p>}
      </>}
    {action.error && <ErrorState error={action.error} />}
  </div>
}
