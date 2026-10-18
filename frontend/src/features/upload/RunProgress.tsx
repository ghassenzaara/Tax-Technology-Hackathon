import { AlertTriangle, CheckCircle2, Loader2, XCircle } from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { adapter } from '../../data'
import { Button } from '../../components/ui/Button'
import { ErrorState } from '../../components/ui/States'
import type { UploadDocument, UploadRun } from '../../data/types'
import { Badge } from '../../components/ui/Badge'
import { Card, CardHeader } from '../../components/ui/Card'
import { formatBytes } from '../../lib/format'
import { extractionStatusText, fileTypeText, parsingStatusText, statusTone, textFor, toneFor } from '../../lib/labels'

const isDone = (d: UploadDocument) => d.parsingStatus !== 'pending' && d.extractionStatus !== 'pending'
const isProblem = (d: UploadDocument) =>
  ['failed', 'unsupported', 'partial'].includes(d.parsingStatus) || ['failed', 'skipped', 'partial', 'write_failed'].includes(d.extractionStatus)

/** One row per document with status and warnings. Failed and skipped files stay visible (UI.md 11). */
export function RunProgress({ run }: { run: UploadRun }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const rerun = useMutation({ mutationFn: () => adapter.rerunCompany!(run.companyId),
    onSuccess: (next) => {
      qc.removeQueries({ queryKey: ['draft', run.companyId] })
      navigate(`/mandanten/${encodeURIComponent(run.companyId)}/pruefung?run=${encodeURIComponent(next.id)}`)
    } })
  const total = run.documents.length
  const done = run.documents.filter(isDone).length
  const running = run.status === 'running'
  const problems = run.documents.filter(isProblem).length

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            {running ? (
              <Loader2 className="size-4 animate-spin text-primary" aria-hidden />
            ) : (
              <CheckCircle2 className="size-4 text-success" aria-hidden />
            )}
            {running ? 'Dokumente werden ausgewertet' : 'Auswertung abgeschlossen'}
          </span>
        }
        action={
          <span className="text-sm text-ink-muted tabular-nums" aria-live="polite">
            {done} von {total}
          </span>
        }
      />
      <div className="px-5 pb-4">
        {running && total > 0 && done === total && <p className="mb-3 text-sm text-ink-muted">Angaben werden abgeglichen und gespeichert …</p>}
        {run.status === 'failed' && <div className="mb-3" role="alert">
          <p className="mb-2 text-danger">{run.error?.message ?? 'Die Verarbeitung wurde unterbrochen.'}</p>
          {adapter.rerunCompany && <Button disabled={rerun.isPending} onClick={() => rerun.mutate()}>Erneut verarbeiten</Button>}
          {rerun.error && <ErrorState error={rerun.error} />}
        </div>}
        <div className="h-1.5 overflow-hidden rounded-full bg-neutral-soft" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done} aria-label="Fortschritt der Auswertung">
          <div
            className="h-full origin-left rounded-full bg-primary transition-transform duration-500 ease-[var(--ease-out)]"
            style={{ transform: `scaleX(${total === 0 ? 0 : done / total})` }}
          />
        </div>
        {!running && problems > 0 && (
          <p className="mt-3 flex items-start gap-2 text-sm text-warning">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            {problems === 1 ? 'Ein Dokument wurde' : `${problems} Dokumente wurden`} nicht oder nur teilweise gelesen. Werte daraus fehlen eventuell im Profil.
          </p>
        )}
      </div>
      <ul className="divide-y divide-line border-t border-line">
        {run.documents.map((d) => (
          <DocumentRow key={d.id} doc={d} />
        ))}
      </ul>
    </Card>
  )
}

function DocumentRow({ doc }: { doc: UploadDocument }) {
  const parts = doc.relativePath.split('/')
  const name = parts.pop()
  const failed = doc.parsingStatus === 'failed' || doc.extractionStatus === 'failed' || doc.extractionStatus === 'write_failed'
  return (
    <li className="grid gap-x-4 gap-y-1 px-5 py-3 sm:grid-cols-[minmax(0,1fr)_auto]">
      <div className="min-w-0">
        <p className="flex items-center gap-2 text-sm font-medium">
          {failed && <XCircle className="size-4 shrink-0 text-danger" aria-hidden />}
          <span className="truncate" title={doc.relativePath}>
            {name}
          </span>
        </p>
        <p className="truncate text-xs text-ink-subtle">
          {[parts.join(' / '), doc.fileType && textFor(fileTypeText, doc.fileType), doc.sizeBytes !== undefined && formatBytes(doc.sizeBytes)]
            .filter(Boolean)
            .join(' · ')}
        </p>
        {doc.error && <p className="mt-1 text-xs text-danger">{doc.error.message}</p>}
        {doc.warnings && doc.warnings.length > 0 && (
          <ul className="mt-1 space-y-0.5">
            {doc.warnings.map((w, i) => (
              <li key={i} className="flex items-start gap-1.5 text-xs text-warning">
                <AlertTriangle className="mt-px size-3 shrink-0" aria-hidden />
                {w}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="flex flex-wrap items-start gap-1.5 sm:justify-end">
        <StatusBadge prefix="Lesen" value={doc.parsingStatus} texts={parsingStatusText} />
        <StatusBadge prefix="Auswertung" value={doc.extractionStatus} texts={extractionStatusText} />
      </div>
    </li>
  )
}

function StatusBadge({ prefix, value, texts }: { prefix: string; value: string; texts: Record<string, string> }) {
  return (
    <Badge tone={toneFor(statusTone, value)}>
      {value === 'pending' && <Loader2 className="size-3 animate-spin" aria-hidden />}
      <span className="opacity-70">{prefix}:</span> {textFor(texts, value)}
    </Badge>
  )
}
