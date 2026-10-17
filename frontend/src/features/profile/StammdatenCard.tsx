import type { ReactNode } from 'react'
import { FileText, History, Pencil, UserPen } from 'lucide-react'
import type { Company, Fact } from '../../data/types'
import { Card, CardHeader } from '../../components/ui/Card'
import { EmptyState } from '../../components/ui/States'
import { reviewStatusText, textFor } from '../../lib/labels'
import { FactValue } from './FactValue'

const UNGROUPED = 'Weitere Merkmale'

/**
 * Fact groups shown on the short Stammdaten card. Every other group, including
 * groups the backend adds later, appears in the Unternehmensprofil tab.
 */
const SUMMARY_GROUPS = new Set(['stammdaten'])

export function groupFacts(facts: Fact[]): [string, Fact[]][] {
  const groups = new Map<string, Fact[]>()
  for (const f of facts) {
    const key = f.group?.trim() || UNGROUPED
    groups.set(key, [...(groups.get(key) ?? []), f])
  }
  return [...groups.entries()]
}

export function splitFacts(facts: Fact[]): { summary: Fact[]; details: Fact[] } {
  const isSummary = (f: Fact) => SUMMARY_GROUPS.has(f.group?.trim().toLowerCase() ?? '')
  return { summary: facts.filter(isSummary), details: facts.filter((f) => !isSummary(f)) }
}

function Row({ label, children, footnote, hideLabel }: { label: string; children: ReactNode; footnote?: ReactNode; hideLabel?: boolean }) {
  return (
    <div className="py-2">
      <dt className={hideLabel ? 'sr-only' : 'text-xs text-ink-muted'}>{label}</dt>
      <dd className="mt-0.5 text-sm text-ink">{children}</dd>
      {footnote}
    </div>
  )
}

/** After confirmation only filename and page remain (brief 2.2). */
function SourceLine({ fact }: { fact: Fact }) {
  if (fact.reviewStatus === 'advisor_entered') {
    return (
      <p className="mt-0.5 flex items-center gap-1 text-xs text-ink-subtle">
        <UserPen className="size-3" aria-hidden />
        Manuell eingetragen
      </p>
    )
  }
  if (fact.sources.length === 0) return null
  return (
    <p className="mt-0.5 flex items-start gap-1 text-xs text-ink-subtle">
      <FileText className="mt-px size-3 shrink-0" aria-hidden />
      <span>
        {fact.sources.map((s, i) => (
          <span key={i}>
            {i > 0 && '; '}
            {s.filename}, S. {s.page}
          </span>
        ))}
      </span>
    </p>
  )
}

/** `group`: when a group holds one field of the same name, the label is not repeated. */
function FactRows({ facts, group }: { facts: Fact[]; group?: string }) {
  return (
    <>
      {facts.map((f) => (
        <Row key={f.id} label={f.label} footnote={<SourceLine fact={f} />} hideLabel={facts.length === 1 && f.label === group}>
          <FactValue value={f.value} type={f.type} unit={f.unit} label={f.label} />
          {f.reviewStatus === 'missing' && (
            <span className="ml-2 text-xs text-ink-subtle">({textFor(reviewStatusText, f.reviewStatus)})</span>
          )}
        </Row>
      ))}
    </>
  )
}

export function IconButton({ label, onClick, children, tone = 'muted' }: { label: string; onClick: () => void; children: ReactNode; tone?: 'muted' | 'primary' }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className={
        'pressable grid size-9 place-items-center rounded-lg hover:bg-neutral-soft ' +
        (tone === 'primary' ? 'text-primary' : 'text-ink-muted hover:text-ink')
      }
    >
      {children}
    </button>
  )
}

export function StammdatenCard({ company, onEdit, onHistory }: { company: Company; onEdit: () => void; onHistory: () => void }) {
  const { contact } = company
  const core: [string, ReactNode][] = [
    ['Name', company.name],
    ['Rechtsform', company.legalForm],
    ['Branche', company.industry],
    ['Kundennummer', company.customerNumber],
    ['Mandant seit', company.clientSince],
    ['Ansprechpartner', [contact.salutation, contact.name].filter(Boolean).join(' ')],
    [
      'E-Mail',
      contact.email ? (
        <a href={`mailto:${contact.email}`} className="break-all text-primary link-underline">
          {contact.email}
        </a>
      ) : undefined,
    ],
    ['Telefon', contact.phone],
  ]
  const { summary } = splitFacts(company.facts)

  return (
    <Card>
      <CardHeader
        title="Stammdaten"
        action={
          <div className="-mr-2 flex">
            <IconButton label="Verlauf anzeigen" onClick={onHistory}>
              <History className="size-[18px]" aria-hidden />
            </IconButton>
            <IconButton label="Stammdaten bearbeiten" onClick={onEdit} tone="primary">
              <Pencil className="size-[18px]" aria-hidden />
            </IconButton>
          </div>
        }
      />
      <dl className="divide-y divide-line/70 px-5 pb-4">
        {core.map(([label, value]) => (
          <Row key={label} label={label}>
            {value ? value : <FactValue value={null} />}
          </Row>
        ))}
        <FactRows facts={summary} />
      </dl>
    </Card>
  )
}

/** All facts outside the summary groups, plus the free extra area. */
export function ProfileDetails({ company, onEdit }: { company: Company; onEdit: () => void }) {
  const groups = groupFacts(splitFacts(company.facts).details)
  const extra = Object.entries(company.extra ?? {})

  if (groups.length === 0 && extra.length === 0) {
    return (
      <Card>
        <EmptyState>Keine weiteren Angaben im Profil.</EmptyState>
      </Card>
    )
  }

  return (
    <div className="gap-6 md:columns-2">
      {groups.map(([group, facts]) => (
        <Card key={group} className="mb-6 break-inside-avoid">
          <CardHeader
            title={group}
            action={
              <div className="-mr-2">
                <IconButton label={`${group} bearbeiten`} onClick={onEdit} tone="primary">
                  <Pencil className="size-[18px]" aria-hidden />
                </IconButton>
              </div>
            }
          />
          <dl className="divide-y divide-line/70 px-5 pb-4">
            <FactRows facts={facts} group={group} />
          </dl>
        </Card>
      ))}
      {extra.length > 0 && (
        <Card className="mb-6 break-inside-avoid">
          <CardHeader title="Weitere Angaben" />
          <dl className="divide-y divide-line/70 px-5 pb-4">
            {extra.map(([k, v]) => (
              <Row key={k} label={k}>
                <FactValue value={v} label={k} />
              </Row>
            ))}
          </dl>
        </Card>
      )}
    </div>
  )
}
