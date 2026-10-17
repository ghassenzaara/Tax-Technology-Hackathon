import { Link } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import type { LawChange } from '../../data/types'
import { Badge } from '../../components/ui/Badge'
import { formatDate } from '../../lib/format'
import { sourceTypeText, textFor } from '../../lib/labels'
import { LawChangeIcon } from './LawChangeIcon'

export function relevanceText(count: number): string {
  if (count === 0) return 'Für keinen Ihrer Mandanten relevant'
  return count === 1 ? 'Relevanz für einen Ihrer Mandanten' : `Relevanz für ${count} Ihrer Mandanten`
}

/** One law change in a list. The whole row links to the detail page. */
export function LawChangeRow({ law, relevantCount, unread, showMeta = false }: { law: LawChange; relevantCount: number; unread: boolean; showMeta?: boolean }) {
  return (
    <li>
      <Link
        to={`/gesetzesaenderungen/${encodeURIComponent(law.id)}`}
        className="group flex items-center gap-4 px-5 py-3.5 transition-colors duration-150 hover:bg-canvas"
      >
        <LawChangeIcon sourceType={law.sourceType} />
        <div className="min-w-0 flex-1">
          <p className={unread ? 'font-semibold text-ink' : 'font-medium text-ink'}>
            {unread && (
              <span className="mr-2 inline-block size-2 rounded-full bg-primary align-middle" title="Ungelesen">
                <span className="sr-only">Ungelesen: </span>
              </span>
            )}
            {law.title}
            {law.version > 1 && (
              <Badge tone="info" className="ml-2 align-middle">
                Aktualisiert, Fassung {law.version}
              </Badge>
            )}
          </p>
          <p className="mt-0.5 text-xs text-ink-muted">
            {showMeta && `${textFor(sourceTypeText, law.sourceType)} · ${law.sourceName} · `}
            <span className={relevantCount > 0 ? 'font-medium text-ink' : undefined}>{relevanceText(relevantCount)}</span>
          </p>
        </div>
        <div className="hidden shrink-0 text-right text-xs text-ink-muted sm:block">
          <p className="tabular-nums">{formatDate(law.publishedAt)}</p>
          {showMeta && law.effectiveAt && <p className="tabular-nums">gilt ab {formatDate(law.effectiveAt)}</p>}
        </div>
        <ChevronRight className="size-4 shrink-0 text-ink-subtle nudge" aria-hidden />
      </Link>
    </li>
  )
}
