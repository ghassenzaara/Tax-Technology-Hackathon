import { useState } from 'react'
import { Link } from 'react-router-dom'
import { FileClock, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { buttonClasses } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/Dialog'
import { useDiscardDraftCompany } from '../../data/mutations'
import { useDraftCompanies } from '../../data/queries'
import type { Company } from '../../data/types'
import { formatDateTime } from '../../lib/format'

/** Started but unconfirmed new profiles. They are no clients yet, so they only show here. */
export function DraftNotice() {
  const drafts = useDraftCompanies()
  const discard = useDiscardDraftCompany()
  const [asking, setAsking] = useState<Company | null>(null)
  if (!drafts.data || drafts.data.length === 0) return null

  return (
    <section className="mb-6 rounded-xl border border-warning/30 bg-warning-soft/50 px-5 py-4">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
        <FileClock className="size-4 text-warning" aria-hidden />
        {drafts.data.length === 1 ? 'Begonnene Profilerstellung' : `${drafts.data.length} begonnene Profilerstellungen`}
      </h2>
      <p className="mt-0.5 text-xs text-ink-muted">Diese Mandanten werden erst angelegt, wenn Sie das Profil bestätigen.</p>
      <ul className="mt-3 space-y-2">
        {drafts.data.map((c) => (
          <li key={c.id} className="flex flex-wrap items-center gap-3 rounded-lg bg-surface px-3 py-2">
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">{c.name}</span>
              <span className="block text-xs text-ink-muted">Begonnen am {formatDateTime(c.createdAt)}</span>
            </span>
            <Link to={`/mandanten/${encodeURIComponent(c.id)}/pruefung`} className={buttonClasses('primary', 'sm')}>
              Prüfung fortsetzen
            </Link>
            <button
              type="button"
              onClick={() => setAsking(c)}
              className="pressable inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-ink-muted hover:bg-danger-soft hover:text-danger"
            >
              <Trash2 className="size-3.5" aria-hidden />
              Verwerfen
            </button>
          </li>
        ))}
      </ul>
      <ConfirmDialog
        open={asking !== null}
        title="Sind Sie sicher?"
        confirmLabel="Ja, verwerfen"
        tone="danger"
        busy={discard.isPending}
        onCancel={() => setAsking(null)}
        onConfirm={() =>
          asking &&
          discard.mutate(asking.id, {
            onSuccess: () => {
              toast.success('Begonnene Profilerstellung verworfen.')
              setAsking(null)
            },
          })
        }
      >
        Der Entwurf für „{asking?.name}“ und die Auswertung der hochgeladenen Dokumente werden gelöscht.
      </ConfirmDialog>
    </section>
  )
}
