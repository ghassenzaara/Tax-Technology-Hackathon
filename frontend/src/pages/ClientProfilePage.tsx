import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { AlertCircle, Mail } from 'lucide-react'
import { PageHeader } from '../components/PageHeader'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card, CardHeader } from '../components/ui/Card'
import { ErrorState, LoadingState } from '../components/ui/States'
import { Tabs } from '../components/ui/Tabs'
import { useCompany, useLawChanges, useMatches } from '../data/queries'
import type { Company, LawChange, Match } from '../data/types'
import { EmailDialog, sendableMatches } from '../features/email/EmailDialog'
import { MatchTable } from '../features/matches/MatchTable'
import { DocumentsTab } from '../features/profile/DocumentsTab'
import { HistoryPanel } from '../features/profile/HistoryPanel'
import { NotesTab } from '../features/profile/NotesTab'
import { ProfileEditor } from '../features/profile/ProfileEditor'
import { ProfileDetails, StammdatenCard } from '../features/profile/StammdatenCard'
import { formatDate } from '../lib/format'
import { companyStatusText, companyStatusTone, textFor, toneFor } from '../lib/labels'

const TABS = [
  { id: 'uebersicht', label: 'Übersicht' },
  { id: 'profil', label: 'Unternehmensprofil' },
  { id: 'dokumente', label: 'Dokumente' },
  { id: 'notizen', label: 'Notizen' },
]

const back = { to: '/mandanten', label: 'Zurück zu den Mandanten' }

export function ClientProfilePage() {
  const { id = '' } = useParams()
  const company = useCompany(id)
  const [params, setParams] = useSearchParams()
  const tab = TABS.some((t) => t.id === params.get('tab')) ? params.get('tab')! : 'uebersicht'
  const [editing, setEditing] = useState(false)

  if (company.isPending) return <LoadingState />
  if (company.isError)
    return (
      <>
        <PageHeader title="Mandant" back={back} />
        <ErrorState error={company.error} />
      </>
    )

  const c = company.data
  const meta = [c.industry, c.customerNumber && `Kundennummer: ${c.customerNumber}`, c.clientSince && `Seit ${c.clientSince}`, c.lastDdAt && `Letzte DD: ${formatDate(c.lastDdAt)}`]
    .filter(Boolean)
    .join(' · ')

  return (
    <>
      <PageHeader
        back={back}
        title={
          <span className="flex flex-wrap items-center gap-3">
            {c.name}
            <Badge tone={toneFor(companyStatusTone, c.status)}>{textFor(companyStatusText, c.status)}</Badge>
          </span>
        }
        description={meta}
      />

      {c.status === 'draft' && (
        <div className="mb-5 flex flex-wrap items-center gap-3 rounded-lg bg-warning-soft px-4 py-3 text-sm text-warning">
          <AlertCircle className="size-4 shrink-0" aria-hidden />
          <span className="flex-1">Das Profil ist noch nicht bestätigt.</span>
          <Link to={`/mandanten/${encodeURIComponent(c.id)}/pruefung`} className="font-medium underline">
            Zur Prüfung
          </Link>
        </div>
      )}

      <Tabs items={TABS} value={tab} onChange={(t) => setParams(t === 'uebersicht' ? {} : { tab: t }, { replace: true })} />

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="mt-6">
        {tab === 'uebersicht' && <Overview company={c} onEdit={() => setEditing(true)} />}
        {tab === 'profil' && <ProfileDetails company={c} onEdit={() => setEditing(true)} />}
        {tab === 'dokumente' && <DocumentsTab company={c} />}
        {tab === 'notizen' && <NotesTab key={c.id} company={c} />}
      </div>

      <ProfileEditor company={c} open={editing} onClose={() => setEditing(false)} />
    </>
  )
}

function Overview({ company, onEdit }: { company: Company; onEdit: () => void }) {
  const [history, setHistory] = useState(false)
  const matches = useMatches({ companyId: company.id })
  const lawChanges = useLawChanges()

  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
      <StammdatenCard company={company} onEdit={onEdit} onHistory={() => setHistory(true)} />

      <div className="space-y-6">
        <Card>
          <CardHeader title="Relevante Gesetzesänderungen für diesen Mandanten" />
          {(matches.isPending || lawChanges.isPending) && <LoadingState />}
          {matches.isError && <div className="px-5 pb-5"><ErrorState error={matches.error} /></div>}
          {matches.isSuccess && lawChanges.isSuccess && <MatchTable matches={matches.data} lawChanges={lawChanges.data} />}
        </Card>
        {matches.isSuccess && lawChanges.isSuccess && (
          <InformCard company={company} matches={matches.data} lawChanges={lawChanges.data} />
        )}
      </div>

      <HistoryPanel company={company} open={history} onClose={() => setHistory(false)} />
    </div>
  )
}

function InformCard({ company, matches, lawChanges }: { company: Company; matches: Match[]; lawChanges: LawChange[] }) {
  const [open, setOpen] = useState(false)
  const ready = sendableMatches(matches).length
  const hasEmail = !!company.contact.email
  return (
    <Card>
      <CardHeader title="Mandanten informieren" />
      <div className="flex flex-wrap items-center justify-between gap-4 px-5 pb-5">
        <div className="max-w-md text-sm text-ink-muted">
          <p>
            {ready === 0
              ? 'Keine relevanten Gesetzesänderungen offen.'
              : `Senden Sie dem Mandanten eine vorbereitete E-Mail zu ${ready === 1 ? 'einer relevanten Gesetzesänderung' : `${ready} relevanten Gesetzesänderungen`}.`}
          </p>
          {!hasEmail && (
            <p className="mt-1 flex items-start gap-1.5 text-warning">
              <AlertCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              Keine E-Mail Adresse hinterlegt. Sie können den Empfänger im Entwurf eintragen.
            </p>
          )}
        </div>
        <Button variant="primary" disabled={ready === 0} onClick={() => setOpen(true)}>
          <Mail className="size-4" aria-hidden />
          E-Mail öffnen
        </Button>
      </div>
      <EmailDialog company={company} matches={matches} lawChanges={lawChanges} open={open} onClose={() => setOpen(false)} />
    </Card>
  )
}
