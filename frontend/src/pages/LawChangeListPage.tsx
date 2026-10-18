import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Search } from 'lucide-react'
import { PageHeader } from '../components/PageHeader'
import { Badge } from '../components/ui/Badge'
import { Card } from '../components/ui/Card'
import { EmptyState, ErrorState, LoadingState } from '../components/ui/States'
import { isMockData } from '../data'
import { relevantCompanyCount, unreadLawChangeIds } from '../data/derive'
import { useAdvisor, useLawChanges, useMatches } from '../data/queries'
import { LawChangeRow } from '../features/lawchanges/LawChangeRow'
import { ScreeningControl } from '../features/screening/ScreeningControl'
import { cn } from '../lib/cn'
import { sourceTypeText, textFor } from '../lib/labels'

const ALL = 'alle'

export function LawChangeListPage() {
  const lawChanges = useLawChanges()
  const matches = useMatches()
  const advisor = useAdvisor()
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const type = params.get('art') ?? ALL
  const onlyRelevant = params.get('relevant') === '1'

  function setParam(key: string, value: string | null) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  // Filter chips come from the data, so new source types appear by themselves.
  const types = useMemo(() => [...new Set((lawChanges.data ?? []).map((l) => l.sourceType))], [lawChanges.data])
  const unread = useMemo(
    () => new Set(matches.data && advisor.data ? unreadLawChangeIds(matches.data, advisor.data) : []),
    [matches.data, advisor.data],
  )

  const rows = useMemo(() => {
    if (!lawChanges.data || !matches.data) return []
    const needle = q.trim().toLowerCase()
    return lawChanges.data
      .map((l) => ({ law: l, count: relevantCompanyCount(matches.data, l.id) }))
      .filter(({ law, count }) => (type === ALL || law.sourceType === type) && (!onlyRelevant || count > 0))
      .filter(({ law }) => !needle || [law.title, law.sourceName, ...(law.tags?.taxTypes ?? [])].some((v) => v.toLowerCase().includes(needle)))
  }, [lawChanges.data, matches.data, q, type, onlyRelevant])

  return (
    <>
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-3">
            Gesetzesänderungen
            {isMockData && <Badge tone="warning">Beispieldaten</Badge>}
          </span>
        }
        description="Neue und geänderte Regelungen aus der letzten Prüfung, neueste zuerst."
      />
      {(lawChanges.error || matches.error) && <ErrorState error={lawChanges.error ?? matches.error} />}
      <ScreeningControl />

      <Card>
        <div className="flex flex-wrap items-center gap-3 p-4">
          <div className="relative w-full max-w-xs">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-ink-subtle" aria-hidden />
            <input
              type="search"
              value={q}
              onChange={(e) => setParam('q', e.target.value || null)}
              placeholder="Titel oder Steuerart suchen"
              aria-label="Gesetzesänderungen suchen"
              className="h-10 w-full rounded-lg border border-line bg-canvas pr-3 pl-9 text-sm placeholder:text-ink-subtle focus:border-primary focus:bg-surface focus:outline-none"
            />
          </div>
          <div role="group" aria-label="Nach Art filtern" className="flex flex-wrap gap-1.5">
            {[ALL, ...types].map((t) => (
              <Chip key={t} active={type === t} onClick={() => setParam('art', t === ALL ? null : t)}>
                {t === ALL ? 'Alle' : textFor(sourceTypeText, t)}
              </Chip>
            ))}
          </div>
          <label className="ml-auto flex cursor-pointer items-center gap-2 text-sm text-ink-muted">
            <input
              type="checkbox"
              checked={onlyRelevant}
              onChange={(e) => setParam('relevant', e.target.checked ? '1' : null)}
              className="size-4 accent-primary"
            />
            Nur für meine Mandanten relevant
          </label>
        </div>

        {(lawChanges.isPending || matches.isPending) && <LoadingState />}
        {lawChanges.isSuccess && matches.isSuccess && rows.length === 0 && <EmptyState>Keine Gesetzesänderungen für diese Auswahl.</EmptyState>}
        {rows.length > 0 && (
          <ul className="divide-y divide-line border-t border-line">
            {rows.map(({ law, count }) => (
              <LawChangeRow key={law.id} law={law} relevantCount={count} unread={unread.has(law.id)} showMeta />
            ))}
          </ul>
        )}
      </Card>
    </>
  )
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'pressable h-8 rounded-full border px-3 text-xs font-medium',
        active ? 'border-primary bg-primary-soft text-primary' : 'border-line bg-surface text-ink-muted hover:border-line-strong hover:text-ink',
      )}
    >
      {children}
    </button>
  )
}
