import { Pencil, RotateCcw, CircleDot } from 'lucide-react'
import type { TemplateHistoryEntry } from '../../data/adapter'
import { useAdvisor, useTemplateHistory } from '../../data/queries'
import { Dialog } from '../../components/ui/Dialog'
import { EmptyState, ErrorState, LoadingState } from '../../components/ui/States'
import { formatDateTime } from '../../lib/format'

const titles: Record<string, string> = {
  template_edited: 'Vorlage geändert',
  template_reset: 'Standardvorlage wiederhergestellt',
}

/** Who changed the email template and when. Loaded only when opened. */
export function TemplateHistoryPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const history = useTemplateHistory(open)
  const advisor = useAdvisor()
  const who = (id: string) => (advisor.data?.id === id ? advisor.data.name : id)

  return (
    <Dialog open={open} onClose={onClose} variant="sheet" title="Verlauf der E-Mail Vorlage" description="Alle Änderungen, neueste zuerst.">
      {history.isPending && <LoadingState />}
      {history.isError && <ErrorState error={history.error} />}
      {history.isSuccess && history.data.length === 0 && <EmptyState>Noch keine Änderungen. Die Standardvorlage ist aktiv.</EmptyState>}
      {history.isSuccess && history.data.length > 0 && (
        <ol className="relative space-y-6 border-l border-line pl-6">
          {history.data.map((e) => (
            <Entry key={e.id} entry={e} who={who(e.actedBy)} />
          ))}
        </ol>
      )}
    </Dialog>
  )
}

function Entry({ entry: e, who }: { entry: TemplateHistoryEntry; who: string }) {
  const Icon = e.kind === 'template_reset' ? RotateCcw : e.kind === 'template_edited' ? Pencil : CircleDot
  const subjectChanged = e.oldSubject !== e.newSubject
  const bodyChanged = e.oldBody !== e.newBody
  return (
    <li className="relative">
      <span className="absolute top-0 -left-[2.3rem] grid size-7 place-items-center rounded-full border border-line bg-surface text-ink-muted">
        <Icon className="size-3.5" aria-hidden />
      </span>
      <p className="text-sm font-medium">{titles[e.kind] ?? e.kind}</p>
      <p className="mt-0.5 text-xs text-ink-subtle">
        {formatDateTime(e.actedAt)} · {who}
      </p>

      {subjectChanged && (
        <div className="mt-2 text-sm">
          <p className="text-xs text-ink-muted">Betreff</p>
          <p className="break-words text-ink-muted line-through decoration-ink-subtle/60">{e.oldSubject}</p>
          <p className="break-words">{e.newSubject}</p>
        </div>
      )}

      {bodyChanged && (
        <details className="group mt-2 text-sm">
          <summary className="cursor-pointer text-xs font-medium text-primary link-underline">Text geändert, anzeigen</summary>
          <div className="mt-2 space-y-2">
            <div>
              <p className="text-xs text-ink-muted">Vorher</p>
              <p className="mt-0.5 max-h-48 overflow-y-auto rounded-md bg-danger-soft/60 px-3 py-2 text-xs leading-relaxed whitespace-pre-wrap">{e.oldBody}</p>
            </div>
            <div>
              <p className="text-xs text-ink-muted">Nachher</p>
              <p className="mt-0.5 max-h-48 overflow-y-auto rounded-md bg-success-soft/60 px-3 py-2 text-xs leading-relaxed whitespace-pre-wrap">{e.newBody}</p>
            </div>
          </div>
        </details>
      )}
    </li>
  )
}
