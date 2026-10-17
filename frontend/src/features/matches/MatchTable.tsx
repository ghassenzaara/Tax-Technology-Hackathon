import { useId, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronDown, FileText } from 'lucide-react'
import type { LawChange, Match } from '../../data/types'
import { sortForCompany } from '../../data/matchRules'
import { LabelMenu, StatusControl, useMatchSave } from './ReviewControls'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Textarea } from '../../components/ui/Field'
import { EmptyState } from '../../components/ui/States'
import { cn } from '../../lib/cn'
import { formatDate, formatDateTime, formatPercent } from '../../lib/format'
import { confidenceLevel, sourceTypeText, textFor } from '../../lib/labels'

export { emailStatusOptions } from './ReviewControls'

export function MatchTable({ matches, lawChanges }: { matches: Match[]; lawChanges: LawChange[] }) {
  const laws = useMemo(() => new Map(lawChanges.map((l) => [l.id, l])), [lawChanges])
  const sorted = useMemo(
    () => sortForCompany(matches, (id) => laws.get(id)?.publishedAt ?? ''),
    [matches, laws],
  )
  if (matches.length === 0) return <EmptyState>Für diesen Mandanten wurden noch keine relevanten Gesetzesänderungen gefunden.</EmptyState>

  return (
    <div>
      <div className="hidden grid-cols-[minmax(0,1fr)_9.75rem_10.5rem_5.5rem_2.25rem] gap-3 border-y border-line bg-canvas px-5 py-2 text-xs font-medium text-ink-muted md:grid">
        <span>Gesetzesänderung</span>
        <span>Einstufung</span>
        <span>E-Mail Status</span>
        <span>Aktualisiert</span>
        <span className="sr-only">Details</span>
      </div>
      <ul className="divide-y divide-line border-t border-line md:border-t-0">
        {sorted.map((m) => (
          <MatchRow key={m.id} match={m} law={laws.get(m.lawChangeId)} />
        ))}
      </ul>
    </div>
  )
}

function MatchRow({ match, law }: { match: Match; law?: LawChange }) {
  const [open, setOpen] = useState(false)
  const detailsId = useId()
  const { save, pending } = useMatchSave(match)
  const title = law?.title ?? match.lawChangeId
  const updatedAt = match.review.updatedAt ?? match.assessment.screenedAt
  const irrelevant = match.review.label === 'irrelevant'

  return (
    <li
      className={cn(
        'transition-[background-color,opacity] duration-200',
        open && 'bg-canvas/60',
        irrelevant && !open && 'bg-canvas',
      )}
    >
      <div className="grid grid-cols-2 items-center gap-x-3 gap-y-2 px-5 py-3 md:grid-cols-[minmax(0,1fr)_9.75rem_10.5rem_5.5rem_2.25rem]">
        <div className="col-span-2 min-w-0 md:col-span-1">
          <Link to={`/gesetzesaenderungen/${encodeURIComponent(match.lawChangeId)}`} className={cn('line-clamp-2 text-sm font-medium hover:text-primary', irrelevant ? 'text-ink-subtle line-through decoration-ink-subtle/50' : 'link-underline text-ink')}>
            {title}
          </Link>
          <p className={cn('mt-0.5 text-xs', irrelevant ? 'text-ink-subtle' : 'text-ink-muted')}>
            {law && `${textFor(sourceTypeText, law.sourceType)} · ${formatDate(law.publishedAt)} · `}
            Konfidenz {formatPercent(match.assessment.confidence)}
          </p>
        </div>

        <LabelMenu match={match} context={title} onSave={save} disabled={pending} />
        <StatusControl match={match} context={title} onSave={save} disabled={pending} />

        <span className="text-xs text-ink-muted tabular-nums">{formatDate(updatedAt)}</span>

        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-controls={detailsId}
          aria-label={open ? 'Details ausblenden' : 'Details anzeigen'}
          className="pressable grid size-9 place-items-center justify-self-end rounded-lg text-ink-muted hover:bg-neutral-soft hover:text-ink"
        >
          <ChevronDown className={cn('size-4 transition-transform duration-200', open && 'rotate-180')} aria-hidden />
        </button>
      </div>

      {open && (
        <div id={detailsId} className="grid gap-5 px-5 pb-5 md:grid-cols-2">
          <MatchDetails match={match} />
          <MatchNotes match={match} onSave={(notes) => save({ notes }, 'Notiz gespeichert.')} saving={pending} />
        </div>
      )}
    </li>
  )
}

export function MatchDetails({ match }: { match: Match }) {
  const { assessment: a, review: r } = match
  const level = confidenceLevel(a.confidence)
  return (
    <div className="space-y-3 text-sm">
      <div>
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <h4 className="text-xs font-semibold tracking-wide text-ink-muted uppercase">Begründung</h4>
          <Badge tone={level.tone}>Konfidenz {level.text} ({formatPercent(a.confidence)})</Badge>
        </div>
        <p className="leading-relaxed">{a.reason}</p>
      </div>
      {a.evidence.length > 0 && (
        <div>
          <h4 className="mb-1 text-xs font-semibold tracking-wide text-ink-muted uppercase">Treffer</h4>
          <ul className="space-y-1">
            {a.evidence.map((e, i) => (
              <li key={i} className="flex items-start gap-1.5 text-sm">
                <FileText className="mt-0.5 size-3.5 shrink-0 text-ink-subtle" aria-hidden />
                <span>
                  <span className="font-medium">{e.factLabel}</span>
                  <span className="text-ink-muted"> passt zu {e.sectionRef}</span>
                  {typeof e.similarity === 'number' && <span className="text-ink-subtle"> (Ähnlichkeit {formatPercent(e.similarity)})</span>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-xs text-ink-subtle">
        Geprüft am {formatDateTime(a.screenedAt)}, Fassung {a.lawVersion}
        {r.sentAt && <> · Gesendet am {formatDateTime(r.sentAt)}</>}
        {r.discussedAt && <> · Besprochen am {formatDateTime(r.discussedAt)}</>}
      </p>
    </div>
  )
}

export function MatchNotes({ match, onSave, saving }: { match: Match; onSave: (notes: string) => void; saving: boolean }) {
  const [notes, setNotes] = useState(match.review.notes ?? '')
  const id = useId()
  const dirty = notes !== (match.review.notes ?? '')
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-xs font-semibold tracking-wide text-ink-muted uppercase">
        Notiz zu dieser Gesetzesänderung
      </label>
      <Textarea id={id} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Notiz hinzufügen" className="min-h-20" />
      <div className="mt-2 flex justify-end">
        <Button size="sm" variant="primary" disabled={!dirty || saving} onClick={() => onSave(notes)}>
          Notiz speichern
        </Button>
      </div>
    </div>
  )
}
