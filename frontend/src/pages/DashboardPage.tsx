import { useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, BookOpen, ClipboardList, Users } from 'lucide-react'
import { PageHeader } from '../components/PageHeader'
import { Card, CardHeader } from '../components/ui/Card'
import { EmptyState, ErrorState, LoadingState } from '../components/ui/States'
import { greetingFor, isOpenTask, newThisMonth, relevantCompanyCount, unreadLawChangeIds } from '../data/derive'
import { useAdvisor, useCompanies, useLawChanges, useMatches } from '../data/queries'
import { ClientTable } from '../features/clients/ClientTable'
import { LawChangeRow } from '../features/lawchanges/LawChangeRow'
import { DraftNotice } from '../features/upload/DraftNotice'
import { cn } from '../lib/cn'
import { ClientSearch, NewClientLink } from './ClientListPage'

const RECENT_LAW_COUNT = 3

export function DashboardPage() {
  const advisor = useAdvisor()
  const companies = useCompanies()
  const matches = useMatches()
  const lawChanges = useLawChanges()
  const [query, setQuery] = useState('')
  const now = useMemo(() => new Date(), [])

  const a = advisor.data
  const lastName = a?.name.split(' ').pop()
  const title = `${greetingFor(now)}${a ? `, ${[a.salutation, lastName].filter(Boolean).join(' ')}` : ''}`

  const stats = useMemo(() => {
    if (!companies.data || !matches.data || !a) return null
    return {
      clients: companies.data.length,
      newClients: newThisMonth(companies.data.map((c) => c.createdAt), now),
      openTasks: matches.data.filter(isOpenTask).length,
      unread: new Set(unreadLawChangeIds(matches.data, a)),
    }
  }, [companies.data, matches.data, a, now])

  const error = companies.error ?? matches.error ?? lawChanges.error ?? advisor.error

  return (
    <>
      <PageHeader title={title} description="Hier finden Sie die wichtigsten Informationen zu Ihren Mandanten und relevanten Gesetzesänderungen." />
      {error && <ErrorState error={error} />}

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          to="/mandanten"
          icon={<Users className="size-5" aria-hidden />}
          tone="bg-primary-soft text-primary"
          label="Mandanten insgesamt"
          value={stats?.clients}
          hint={stats && stats.newClients > 0 ? <span className="text-success">+ {stats.newClients} neu in diesem Monat</span> : 'Keine neuen in diesem Monat'}
        />
        <StatCard
          icon={<ClipboardList className="size-5" aria-hidden />}
          tone="bg-warning-soft text-warning"
          label="Offene Aufgaben"
          value={stats?.openTasks}
          hint="Einstufen oder Mandanten informieren"
        />
        <StatCard
          to="/gesetzesaenderungen"
          icon={<BookOpen className="size-5" aria-hidden />}
          tone="bg-danger-soft text-danger"
          label="Ungelesene Updates"
          value={stats?.unread.size}
          hint="neue Gesetzesänderungen"
        />
      </div>

      <Card className="mt-6">
        <CardHeader
          title="Aktuelle Gesetzesänderungen"
          action={
            <Link to="/gesetzesaenderungen" className="group inline-flex shrink-0 items-center gap-1 text-sm font-medium whitespace-nowrap text-primary link-underline">
              Alle anzeigen
              <ArrowRight className="nudge size-4" aria-hidden />
            </Link>
          }
        />
        {lawChanges.isPending && <LoadingState />}
        {lawChanges.data && lawChanges.data.length === 0 && <EmptyState>Noch keine Gesetzesänderungen erfasst.</EmptyState>}
        {lawChanges.data && matches.data && stats && (
          <ul className="divide-y divide-line border-t border-line">
            {lawChanges.data.slice(0, RECENT_LAW_COUNT).map((l) => (
              <LawChangeRow key={l.id} law={l} relevantCount={relevantCompanyCount(matches.data, l.id)} unread={stats.unread.has(l.id)} />
            ))}
          </ul>
        )}
      </Card>

      <div className="mt-6">
        <DraftNotice />
      </div>
      <Card>
        <CardHeader title="Ihre Mandanten" action={<NewClientLink />} />
        <div className="px-5 pb-4">
          <ClientSearch value={query} onChange={setQuery} />
        </div>
        {(companies.isPending || matches.isPending) && <LoadingState />}
        {companies.data && matches.data && <ClientTable companies={companies.data} matches={matches.data} query={query} />}
      </Card>
    </>
  )
}

function StatCard({ icon, tone, label, value, hint, to }: { icon: ReactNode; tone: string; label: string; value?: number; hint: ReactNode; to?: string }) {
  const body = (
    <>
      <span className={cn('grid size-11 shrink-0 place-items-center rounded-xl', tone)}>{icon}</span>
      <span className="min-w-0">
        <span className="block text-sm text-ink-muted">{label}</span>
        <span className="block text-2xl font-semibold tracking-tight text-ink tabular-nums">{value ?? '–'}</span>
        <span className="block text-xs text-ink-muted">{hint}</span>
      </span>
    </>
  )
  const className = 'flex items-start gap-4 rounded-xl border border-line bg-surface p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]'
  return to ? (
    <Link to={to} className={cn(className, 'hover-lift hover:border-line-strong')}>
      {body}
    </Link>
  ) : (
    <div className={className}>{body}</div>
  )
}
