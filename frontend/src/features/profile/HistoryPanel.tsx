import { useId, useMemo, useState } from 'react'
import { CheckCircle2, ChevronDown, FilePlus2, Mail, Pencil, Tag, CircleDot } from 'lucide-react'
import type { AuditEntry, Company, LawChange, Match, Outreach } from '../../data/types'
import { useAdvisor, useAuditEntries, useLawChanges, useMatches, useOutreach } from '../../data/queries'
import { Dialog } from '../../components/ui/Dialog'
import { EmptyState, ErrorState, LoadingState } from '../../components/ui/States'
import { formatDateTime, formatScalar, isPlainObject } from '../../lib/format'
import { coreFieldText, emailStatusText, matchLabelText, textFor } from '../../lib/labels'

const icons: Record<string, typeof Pencil> = {
  profile_confirmed: CheckCircle2,
  profile_changed: Pencil,
  match_review_changed: Tag,
  email_sent: Mail,
  documents_added: FilePlus2,
}

function displayValue(field: string | undefined, value: unknown): string {
  if (value === null || value === undefined || value === '') return 'leer'
  if (field === 'label' && typeof value === 'string') return textFor(matchLabelText, value)
  if (field === 'emailStatus' && typeof value === 'string') return textFor(emailStatusText, value)
  if (isPlainObject(value)) return Object.values(value).filter((v) => v !== undefined && v !== '').map((v) => formatScalar(v)).join(', ')
  if (Array.isArray(value)) return `${value.length} Einträge`
  return formatScalar(value)
}

function describe(entry: AuditEntry, matches: Map<string, Match>, laws: Map<string, LawChange>): string {
  const field = entry.field ? textFor(coreFieldText, entry.field) : ''
  switch (entry.kind) {
    case 'profile_confirmed':
      return entry.summary ?? 'Profil bestätigt'
    case 'profile_changed':
      return `${field || 'Profil'} geändert`
    case 'match_review_changed': {
      const lawId = entry.matchId ? matches.get(entry.matchId)?.lawChangeId : undefined
      const title = lawId ? laws.get(lawId)?.title : undefined
      return `${field} geändert${title ? `: ${title}` : ''}`
    }
    case 'email_sent':
      return entry.summary ?? 'E-Mail gesendet'
    default:
      return entry.summary ?? entry.kind
  }
}

/** Audit log. Hidden by default, loaded only when the advisor opens it. */
export function HistoryPanel({ company, open, onClose }: { company: Company; open: boolean; onClose: () => void }) {
  const audit = useAuditEntries(company.id, open)
  const matches = useMatches({ companyId: company.id })
  const lawChanges = useLawChanges()
  const advisor = useAdvisor()
  const outreach = useOutreach(company.id, open)
  const outreachMap = useMemo(() => new Map((outreach.data ?? []).map((o) => [o.id, o])), [outreach.data])
  const matchMap = useMemo(() => new Map((matches.data ?? []).map((m) => [m.id, m])), [matches.data])
  const lawMap = useMemo(() => new Map((lawChanges.data ?? []).map((l) => [l.id, l])), [lawChanges.data])
  const who = (id: string) => (advisor.data?.id === id ? advisor.data.name : id)

  return (
    <Dialog open={open} onClose={onClose} variant="sheet" title="Verlauf" description={`Alle Änderungen für ${company.name}, neueste zuerst.`}>
      {audit.isPending && <LoadingState />}
      {audit.isError && <ErrorState error={audit.error} />}
      {audit.isSuccess && audit.data.length === 0 && <EmptyState>Noch keine Einträge.</EmptyState>}
      {audit.isSuccess && audit.data.length > 0 && (
        <ol className="relative space-y-5 border-l border-line pl-6">
          {audit.data.map((e) => {
            const Icon = icons[e.kind] ?? CircleDot
            const showChange = e.kind === 'profile_changed' || e.kind === 'match_review_changed'
            return (
              <li key={e.id} className="relative">
                <span className="absolute top-0 -left-[2.3rem] grid size-7 place-items-center rounded-full border border-line bg-surface text-ink-muted">
                  <Icon className="size-3.5" aria-hidden />
                </span>
                <p className="text-sm font-medium">{describe(e, matchMap, lawMap)}</p>
                {showChange && (
                  <p className="mt-0.5 text-sm break-words text-ink-muted">
                    <span className="line-through decoration-ink-subtle/60">{displayValue(e.field, e.oldValue)}</span>
                    <span aria-hidden> → </span>
                    <span className="sr-only"> wurde zu </span>
                    <span className="text-ink">{displayValue(e.field, e.newValue)}</span>
                  </p>
                )}
                {showChange && e.summary && <p className="mt-0.5 text-xs text-ink-muted">{e.summary}</p>}
                {e.kind === 'email_sent' && e.outreachId && outreachMap.has(e.outreachId) && (
                  <SentEmail outreach={outreachMap.get(e.outreachId)!} matches={matchMap} laws={lawMap} />
                )}
                <p className="mt-0.5 text-xs text-ink-subtle">
                  {formatDateTime(e.actedAt)} · {who(e.actedBy)}
                </p>
              </li>
            )
          })}
        </ol>
      )}
    </Dialog>
  )
}

/** The exact email as sent (outreach snapshot). Read only: a sent email never changes. */
function SentEmail({ outreach, matches, laws }: { outreach: Outreach; matches: Map<string, Match>; laws: Map<string, LawChange> }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  const titles = outreach.matchIds.map((m) => {
    const lawId = matches.get(m)?.lawChangeId
    return (lawId && laws.get(lawId)?.title) || m
  })
  return (
    <div className="mt-1.5">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={id}
        className="group inline-flex items-center gap-1 text-xs font-medium text-primary link-underline"
      >
        <Mail className="size-3.5" aria-hidden />
        {open ? 'E-Mail ausblenden' : 'E-Mail anzeigen'}
        <ChevronDown className={`size-3.5 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} aria-hidden />
      </button>
      {open && (
        <div id={id} className="menu-pop mt-2 origin-top rounded-lg border border-line bg-canvas text-sm">
          <dl className="grid grid-cols-[4.5rem_1fr] gap-x-2 gap-y-1 border-b border-line px-3 py-2 text-xs">
            <dt className="text-ink-muted">An</dt>
            <dd className="break-all">{outreach.recipient}</dd>
            <dt className="text-ink-muted">Betreff</dt>
            <dd className="font-medium">{outreach.subject}</dd>
            <dt className="text-ink-muted">Gesendet</dt>
            <dd>{formatDateTime(outreach.sentAt)}</dd>
          </dl>
          <p className="max-h-72 overflow-y-auto px-3 py-2 leading-relaxed whitespace-pre-wrap">{outreach.body}</p>
          <div className="border-t border-line px-3 py-2 text-xs text-ink-muted">
            <p className="font-medium text-ink">Als gesendet markiert:</p>
            <ul className="mt-0.5 list-disc pl-4">
              {titles.map((t, i) => (
                <li key={i}>{t}</li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  )
}
