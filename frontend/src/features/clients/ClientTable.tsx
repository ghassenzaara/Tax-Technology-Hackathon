import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import type { Company, Match } from '../../data/types'
import { relevantUpdateCount } from '../../data/derive'
import { Badge } from '../../components/ui/Badge'
import { EmptyState } from '../../components/ui/States'
import { formatDate } from '../../lib/format'
import { companyStatusText, companyStatusTone, textFor, toneFor } from '../../lib/labels'

export function filterCompanies(companies: Company[], query: string): Company[] {
  const q = query.trim().toLowerCase()
  if (!q) return companies
  return companies.filter((c) =>
    [c.name, c.industry, c.legalForm, c.customerNumber, c.contact.name].some((v) => v?.toLowerCase().includes(q)),
  )
}

/** Client overview table. Name and actions always visible; other columns from wider screens. */
export function ClientTable({ companies, matches, query = '' }: { companies: Company[]; matches: Match[]; query?: string }) {
  const rows = useMemo(
    () => [...filterCompanies(companies, query)].sort((a, b) => a.name.localeCompare(b.name, 'de')),
    [companies, query],
  )
  if (rows.length === 0) return <EmptyState>{query ? `Keine Mandanten für „${query}“ gefunden.` : 'Noch keine Mandanten angelegt.'}</EmptyState>

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="border-y border-line bg-canvas text-xs text-ink-muted">
          <tr>
            <th scope="col" className="px-5 py-2 font-medium">Name</th>
            <th scope="col" className="hidden px-3 py-2 font-medium md:table-cell">Branche</th>
            <th scope="col" className="hidden px-3 py-2 font-medium sm:table-cell">Status</th>
            <th scope="col" className="hidden px-3 py-2 font-medium lg:table-cell">Letzte DD</th>
            <th scope="col" className="px-3 py-2 font-medium"><span className="sm:hidden">Updates</span><span className="hidden sm:inline">Relevante Updates</span></th>
            <th scope="col" className="py-2 pr-5 pl-2 text-right font-medium"><span className="sr-only">Aktionen</span></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((c) => {
            const updates = relevantUpdateCount(matches, c.id)
            const href = `/mandanten/${encodeURIComponent(c.id)}`
            return (
              <tr key={c.id} className="group transition-colors duration-150 hover:bg-canvas">
                <td className="px-5 py-3">
                  <Link to={href} className="font-medium text-ink hover:text-primary link-underline">
                    {c.name}
                  </Link>
                  <span className="block text-xs text-ink-muted md:hidden">{c.industry}</span>
                </td>
                <td className="hidden px-3 py-3 text-ink-muted md:table-cell">{c.industry ?? ''}</td>
                <td className="hidden px-3 py-3 sm:table-cell">
                  <Badge tone={toneFor(companyStatusTone, c.status)}>{textFor(companyStatusText, c.status)}</Badge>
                </td>
                <td className="hidden px-3 py-3 text-ink-muted tabular-nums lg:table-cell">{c.lastDdAt ? formatDate(c.lastDdAt) : ''}</td>
                <td className="px-3 py-3">
                  {updates > 0 ? (
                    <span
                      className="inline-grid min-w-6 place-items-center rounded-full bg-danger-soft px-1.5 py-0.5 text-xs font-semibold text-danger tabular-nums"
                      aria-label={`${updates} relevante Updates`}
                    >
                      {updates}
                    </span>
                  ) : (
                    <span className="text-ink-subtle" aria-label="Keine relevanten Updates">–</span>
                  )}
                </td>
                <td className="py-3 pr-5 pl-2 text-right">
                  <Link
                    to={href}
                    aria-label={`Profil von ${c.name} öffnen`}
                    className="pressable inline-flex h-8 items-center gap-1 rounded-lg border border-line px-2 text-xs font-medium whitespace-nowrap text-ink hover:border-line-strong hover:bg-surface sm:px-3"
                  >
                    <span className="hidden sm:inline">Profil öffnen</span>
                    <ChevronRight className="nudge size-3.5" aria-hidden />
                  </Link>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
