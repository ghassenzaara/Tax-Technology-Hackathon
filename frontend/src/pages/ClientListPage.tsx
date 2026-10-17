import { Link, useSearchParams } from 'react-router-dom'
import { Plus, Search } from 'lucide-react'
import { PageHeader } from '../components/PageHeader'
import { buttonClasses } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { ErrorState, LoadingState } from '../components/ui/States'
import { useCompanies, useMatches } from '../data/queries'
import { ClientTable } from '../features/clients/ClientTable'
import { DraftNotice } from '../features/upload/DraftNotice'

export function ClientListPage() {
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const companies = useCompanies()
  const matches = useMatches()

  return (
    <>
      <PageHeader
        title="Mandanten"
        description={companies.data ? `${companies.data.length} Mandanten` : undefined}
        actions={<NewClientLink />}
      />
      <DraftNotice />
      <Card>
        <div className="p-4">
          <ClientSearch value={q} onChange={(v) => setParams(v ? { q: v } : {}, { replace: true })} />
        </div>
        {(companies.isPending || matches.isPending) && <LoadingState />}
        {companies.isError && (
          <div className="px-5 pb-5">
            <ErrorState error={companies.error} />
          </div>
        )}
        {companies.isSuccess && matches.isSuccess && <ClientTable companies={companies.data} matches={matches.data} query={q} />}
      </Card>
    </>
  )
}

export function NewClientLink() {
  return (
    <Link
      to="/mandanten/neu"
      className={buttonClasses('primary')}
    >
      <Plus className="size-4" aria-hidden />
      Neuer Mandant
    </Link>
  )
}

export function ClientSearch({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="relative max-w-sm">
      <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-ink-subtle" aria-hidden />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Mandanten suchen"
        aria-label="Mandanten suchen"
        className="h-10 w-full rounded-lg border border-line bg-canvas pr-3 pl-9 text-sm placeholder:text-ink-subtle focus:border-primary focus:bg-surface focus:outline-none"
      />
    </div>
  )
}
