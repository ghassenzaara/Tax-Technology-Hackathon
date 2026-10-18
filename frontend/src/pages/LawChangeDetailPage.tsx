import { useEffect, useId, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, ChevronDown, ExternalLink, Users } from 'lucide-react'
import { PageHeader } from '../components/PageHeader'
import { Badge } from '../components/ui/Badge'
import { Card, CardHeader } from '../components/ui/Card'
import { EmptyState, ErrorState, LoadingState } from '../components/ui/States'
import { isMockData } from '../data'
import { relevantCompanyCount } from '../data/derive'
import { useMarkLawChangeSeen } from '../data/mutations'
import { useAdvisor, useCompanies, useLawChange, useMatches } from '../data/queries'
import type { Company, LawChange, Match } from '../data/types'
import { BlockRenderer } from '../features/lawchanges/BlockRenderer'
import { LawChangeIcon } from '../features/lawchanges/LawChangeIcon'
import { MatchDetails, MatchNotes } from '../features/matches/MatchTable'
import { LabelMenu, StatusControl, useMatchSave } from '../features/matches/ReviewControls'
import { FactValue } from '../features/profile/FactValue'
import { cn } from '../lib/cn'
import { formatDate, formatPercent } from '../lib/format'
import { sourceTypeText, textFor } from '../lib/labels'

const back = { to: '/gesetzesaenderungen', label: 'Zurück zu den Gesetzesänderungen' }

export function LawChangeDetailPage() {
  const { id = '' } = useParams()
  const law = useLawChange(id)
  const matches = useMatches({ lawChangeId: id })
  const companies = useCompanies()
  const advisor = useAdvisor()
  const markSeen = useMarkLawChangeSeen()

  // Opening the page marks the change as read for this advisor.
  const seen = advisor.data?.seenLawChangeIds?.includes(id)
  useEffect(() => {
    if (law.isSuccess && advisor.isSuccess && !seen && !markSeen.isPending) markSeen.mutate(id)
  }, [law.isSuccess, advisor.isSuccess, seen, id])

  if (law.isPending) return <LoadingState />
  if (law.isError)
    return (
      <>
        <PageHeader title="Gesetzesänderung" back={back} />
        <ErrorState error={law.error} />
      </>
    )

  const l = law.data
  const affected = matches.data ? relevantCompanyCount(matches.data, l.id) : undefined
  const meta = [
    `Veröffentlicht ${formatDate(l.publishedAt)}`,
    l.effectiveAt && `gilt ab ${formatDate(l.effectiveAt)}`,
    textFor(sourceTypeText, l.sourceType),
    l.sourceName,
    l.version > 1 && `Fassung ${l.version}`,
  ].filter(Boolean)

  return (
    <>
      <Link
        to={back.to}
        className="group mb-3 inline-flex items-center gap-1.5 text-sm text-ink-muted hover:text-ink"
      >
        <ArrowLeft className="nudge-back size-4" aria-hidden />
        {back.label}
      </Link>
      <div className="mb-6 flex flex-wrap items-start gap-5">
        <LawChangeIcon sourceType={l.sourceType} size="lg" />
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-semibold tracking-tight text-balance">{l.title}</h1>
          <p className="mt-1 text-sm text-ink-muted">{meta.join(' · ')}</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {isMockData && <Badge tone="warning">Beispieldaten</Badge>}
            {l.version > 1 && <Badge tone="info">Aktualisiert</Badge>}
            {l.url && <a
              href={l.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-sm font-medium text-primary link-underline"
            >
              Zur Quelle
              <ExternalLink className="size-3.5" aria-hidden />
              <span className="sr-only">(öffnet in neuem Tab)</span>
            </a>}
          </div>
        </div>
        <a
          href="#betroffene-mandanten"
          className="flex items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3 shadow-[0_1px_2px_rgba(15,23,42,0.04)] hover-lift hover:border-line-strong"
        >
          <span className="grid size-10 place-items-center rounded-lg bg-primary-soft text-primary">
            <Users className="size-5" aria-hidden />
          </span>
          <span>
            <span className="block text-xl font-semibold tabular-nums">{affected ?? '–'}</span>
            <span className="block text-xs text-ink-muted">{affected === 1 ? 'Mandant betroffen' : 'Mandanten betroffen'}</span>
          </span>
        </a>
      </div>

      {/* Summary full width, the other blocks packed in two columns. */}
      {l.blocks
        .filter((b) => b.kind === 'summary')
        .map((b, i) => (
          <div key={`s${i}`} className="mb-6">
            <BlockRenderer block={b} />
          </div>
        ))}
      <div className="gap-6 lg:columns-2">
        {l.blocks
          .filter((b) => b.kind !== 'summary')
          .map((b, i) => (
            <div key={i} className="mb-6 break-inside-avoid">
              <BlockRenderer block={b} />
            </div>
          ))}
      </div>

      <Card className="mt-6" id="betroffene-mandanten">
        <CardHeader title="Betroffene Mandanten" />
        {(matches.isPending || companies.isPending) && <LoadingState />}
        {matches.isError && (
          <div className="px-5 pb-5">
            <ErrorState error={matches.error} />
          </div>
        )}
        {matches.data && companies.data && <AffectedClients matches={matches.data} companies={companies.data} />}
      </Card>

      <SectionsCard law={l} />
      <ExtraCard law={l} />
    </>
  )
}

function AffectedClients({ matches, companies }: { matches: Match[]; companies: Company[] }) {
  const byId = useMemo(() => new Map(companies.map((c) => [c.id, c])), [companies])
  const rows = useMemo(
    () =>
      [...matches].sort(
        (a, b) =>
          Number(a.review.label === 'irrelevant') - Number(b.review.label === 'irrelevant') || b.assessment.confidence - a.assessment.confidence,
      ),
    [matches],
  )
  if (rows.length === 0) return <EmptyState>Keiner Ihrer Mandanten ist von dieser Änderung betroffen.</EmptyState>
  return (
    <>
      <div className="hidden grid-cols-[minmax(0,1fr)_9.75rem_10.5rem_2.25rem] gap-3 border-y border-line bg-canvas px-5 py-2 text-xs font-medium text-ink-muted md:grid">
        <span>Mandant und Begründung</span>
        <span>Einstufung</span>
        <span>E-Mail Status</span>
        <span className="sr-only">Details</span>
      </div>
      <ul className="divide-y divide-line border-t border-line md:border-t-0">
        {rows.map((m) => (
          <AffectedRow key={m.id} match={m} company={byId.get(m.companyId)} />
        ))}
      </ul>
    </>
  )
}

function AffectedRow({ match, company }: { match: Match; company?: Company }) {
  const [open, setOpen] = useState(false)
  const detailsId = useId()
  const { save, pending } = useMatchSave(match)
  const name = company?.name ?? match.companyId
  const irrelevant = match.review.label === 'irrelevant'

  return (
    <li className={cn(open && 'bg-canvas/60', irrelevant && !open && 'bg-canvas')}>
      <div className="grid grid-cols-2 items-center gap-x-3 gap-y-2 px-5 py-3 md:grid-cols-[minmax(0,1fr)_9.75rem_10.5rem_2.25rem]">
        <div className="col-span-2 min-w-0 md:col-span-1">
          <Link
            to={`/mandanten/${encodeURIComponent(match.companyId)}`}
            className={cn('text-sm font-medium hover:text-primary', irrelevant ? 'text-ink-subtle line-through decoration-ink-subtle/50' : 'link-underline text-ink')}
          >
            {name}
          </Link>
          <p className={cn('mt-0.5 line-clamp-2 text-xs', irrelevant ? 'text-ink-subtle' : 'text-ink-muted')}>
            {match.assessment.reason} <span className="whitespace-nowrap">(Konfidenz {formatPercent(match.assessment.confidence)})</span>
          </p>
        </div>
        <LabelMenu match={match} context={name} onSave={save} disabled={pending} />
        <StatusControl match={match} context={name} onSave={save} disabled={pending} />
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-controls={detailsId}
          aria-label={open ? 'Details ausblenden' : 'Details anzeigen'}
          className="pressable col-start-2 grid size-9 place-items-center justify-self-end rounded-lg text-ink-muted hover:bg-neutral-soft hover:text-ink md:col-start-auto"
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

function SectionsCard({ law }: { law: LawChange }) {
  if (law.sections.length === 0) return null
  return (
    <Card className="mt-6">
      <CardHeader title="Abschnitte der Quelle" />
      <ul className="divide-y divide-line border-t border-line">
        {law.sections.map((s) => (
          <li key={s.id} className="px-5 py-3">
            <details className="group">
              <summary className="flex cursor-pointer list-none items-center gap-3 text-sm">
                <span className="shrink-0 rounded-md bg-neutral-soft px-2 py-0.5 text-xs font-medium text-ink-muted">{s.ref}</span>
                <span className="flex-1 font-medium">{s.heading ?? s.ref}</span>
                <ChevronDown className="size-4 text-ink-subtle transition-transform duration-200 group-open:rotate-180" aria-hidden />
              </summary>
              <p className="mt-2 pl-1 text-sm leading-relaxed text-ink-muted">{s.text}</p>
            </details>
          </li>
        ))}
      </ul>
    </Card>
  )
}

function ExtraCard({ law }: { law: LawChange }) {
  const tags = law.tags ?? {}
  const tagRows: [string, string[] | undefined][] = [
    ['Steuerarten', tags.taxTypes],
    ['Normen', tags.norms],
    ['Rechtsformen', tags.legalForms],
    ['Branchen', tags.industries],
  ]
  const shownTags = tagRows.filter(([, v]) => v && v.length > 0) as [string, string[]][]
  const extra = Object.entries(law.extra ?? {})
  if (shownTags.length === 0 && extra.length === 0) return null
  return (
    <Card className="mt-6">
      <CardHeader title="Weitere Angaben" />
      <dl className="grid gap-x-6 gap-y-3 px-5 pb-5 text-sm sm:grid-cols-2">
        {shownTags.map(([label, values]) => (
          <div key={label}>
            <dt className="text-xs text-ink-muted">{label}</dt>
            <dd className="mt-1 flex flex-wrap gap-1.5">
              {values.map((v) => (
                <Badge key={v}>{v}</Badge>
              ))}
            </dd>
          </div>
        ))}
        {extra.map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs text-ink-muted">{k}</dt>
            <dd className="mt-0.5">
              <FactValue value={v} label={k} />
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  )
}
