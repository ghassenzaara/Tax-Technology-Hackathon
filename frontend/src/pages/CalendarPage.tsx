import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { CalendarCheck, ChevronLeft, ChevronRight, Mail } from 'lucide-react'
import { PageHeader } from '../components/PageHeader'
import { Badge, type Tone } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card, CardHeader } from '../components/ui/Card'
import { Dialog } from '../components/ui/Dialog'
import { Field, Input, Textarea } from '../components/ui/Field'
import { EmptyState, ErrorState, LoadingState } from '../components/ui/States'
import { adapter } from '../data'
import { useRecordResponse } from '../data/mutations'
import { useCalendar } from '../data/queries'
import type { CalendarEntry } from '../data/types'
import { cn } from '../lib/cn'
import { formatDate, formatDateTime } from '../lib/format'

const WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']
const monthFormat = new Intl.DateTimeFormat('de-DE', { month: 'long', year: 'numeric' })
const timeFormat = new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' })

const statusText: Record<CalendarEntry['status'], string> = {
  awaiting: 'Antwort ausstehend',
  accepted: 'Besprechung zugesagt',
  rejected: 'Abgelehnt',
}
const statusTone: Record<CalendarEntry['status'], Tone> = { awaiting: 'warning', accepted: 'success', rejected: 'danger' }

/** Local calendar day (YYYY-MM-DD) of an ISO timestamp. */
function dayKey(value: Date | string) {
  const d = typeof value === 'string' ? new Date(value) : value
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

interface DayEvent {
  kind: 'sent' | 'meeting'
  entry: CalendarEntry
  at: string
}

function monthDays(month: Date) {
  const first = new Date(month.getFullYear(), month.getMonth(), 1)
  const start = new Date(first)
  start.setDate(1 - ((first.getDay() + 6) % 7)) // back to Monday
  return Array.from({ length: 42 }, (_, i) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + i))
}

export function CalendarPage() {
  const calendar = useCalendar()
  const [month, setMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1))
  const [editing, setEditing] = useState<CalendarEntry>()

  const events = useMemo(() => {
    const byDay = new Map<string, DayEvent[]>()
    const add = (at: string, event: DayEvent) => byDay.set(dayKey(at), [...(byDay.get(dayKey(at)) ?? []), event])
    for (const entry of calendar.data ?? []) {
      add(entry.sentAt, { kind: 'sent', entry, at: entry.sentAt })
      if (entry.response?.meetingAt) add(entry.response.meetingAt, { kind: 'meeting', entry, at: entry.response.meetingAt })
    }
    for (const list of byDay.values()) list.sort((a, b) => a.at.localeCompare(b.at))
    return byDay
  }, [calendar.data])

  const upcoming = useMemo(
    () =>
      (calendar.data ?? [])
        .filter((e) => e.response?.meetingAt && new Date(e.response.meetingAt) >= new Date(new Date().toDateString()))
        .sort((a, b) => a.response!.meetingAt!.localeCompare(b.response!.meetingAt!)),
    [calendar.data],
  )

  if (!adapter.listCalendar) {
    return (
      <>
        <PageHeader title="Kalender" />
        <Card>
          <EmptyState>Der Kalender ist nur mit dem Server verfügbar.</EmptyState>
        </Card>
      </>
    )
  }

  const today = dayKey(new Date())
  const shift = (n: number) => setMonth(new Date(month.getFullYear(), month.getMonth() + n, 1))

  return (
    <>
      <PageHeader
        title="Kalender"
        description="Gesendete E-Mails und vereinbarte Besprechungen. Antworten der Mandanten erfassen Sie hier von Hand."
      />
      {calendar.error && <ErrorState error={calendar.error} />}

      <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
        <Card className="min-w-0">
          <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-4 pb-3">
            <h2 className="text-base font-semibold capitalize">{monthFormat.format(month)}</h2>
            <div className="flex items-center gap-1">
              <Button size="sm" variant="ghost" onClick={() => shift(-1)} aria-label="Vorheriger Monat">
                <ChevronLeft className="size-4" aria-hidden />
              </Button>
              <Button size="sm" onClick={() => setMonth(new Date(new Date().getFullYear(), new Date().getMonth(), 1))}>
                Heute
              </Button>
              <Button size="sm" variant="ghost" onClick={() => shift(1)} aria-label="Nächster Monat">
                <ChevronRight className="size-4" aria-hidden />
              </Button>
            </div>
          </div>
          {calendar.isLoading ? (
            <LoadingState />
          ) : (
            <div className="px-3 pb-4 sm:px-5">
              <div className="grid grid-cols-7 gap-px overflow-hidden rounded-lg border border-line bg-line text-xs">
                {WEEKDAYS.map((d) => (
                  <div key={d} className="bg-canvas px-2 py-1.5 font-medium text-ink-muted">
                    {d}
                  </div>
                ))}
                {monthDays(month).map((day) => {
                  const key = dayKey(day)
                  const items = events.get(key) ?? []
                  const outside = day.getMonth() !== month.getMonth()
                  return (
                    <div key={key} className={cn('min-h-20 bg-surface p-1.5 sm:min-h-24', outside && 'bg-canvas/60 text-ink-subtle')}>
                      <span
                        className={cn(
                          'mb-1 inline-grid size-6 place-items-center rounded-full tabular-nums',
                          key === today && 'bg-primary font-semibold text-white',
                        )}
                      >
                        {day.getDate()}
                      </span>
                      <ul className="space-y-1">
                        {items.map((ev) => (
                          <li key={`${ev.kind}-${ev.entry.outreachId}`}>
                            <button
                              type="button"
                              onClick={() => setEditing(ev.entry)}
                              title={`${ev.kind === 'meeting' ? 'Besprechung' : 'E-Mail gesendet'}: ${ev.entry.companyName}`}
                              className={cn(
                                'pressable flex w-full items-center gap-1 truncate rounded px-1.5 py-0.5 text-left',
                                ev.kind === 'meeting'
                                  ? 'bg-success-soft text-success'
                                  : ev.entry.status === 'rejected'
                                    ? 'bg-danger-soft text-danger line-through'
                                    : 'bg-info-soft text-info',
                              )}
                            >
                              {ev.kind === 'meeting' ? (
                                <CalendarCheck className="size-3 shrink-0" aria-hidden />
                              ) : (
                                <Mail className="size-3 shrink-0" aria-hidden />
                              )}
                              <span className="truncate">
                                {ev.kind === 'meeting' && `${timeFormat.format(new Date(ev.at))} `}
                                {ev.entry.companyName}
                              </span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )
                })}
              </div>
              <div className="mt-3 flex flex-wrap gap-3 text-xs text-ink-muted">
                <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm bg-info" />E-Mail gesendet</span>
                <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm bg-success" />Besprechung</span>
                <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-sm bg-danger" />Abgelehnt</span>
              </div>
            </div>
          )}
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader title="Nächste Besprechungen" />
            {upcoming.length === 0 ? (
              <EmptyState>Keine Besprechungen geplant.</EmptyState>
            ) : (
              <ul className="divide-y divide-line">
                {upcoming.map((e) => (
                  <li key={e.outreachId} className="px-5 py-3 text-sm">
                    <p className="font-medium">{formatDateTime(e.response!.meetingAt!)}</p>
                    <Link to={`/mandanten/${e.companyId}`} className="text-ink-muted hover:text-primary">
                      {e.companyName}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <CardHeader title="Gesendete E-Mails" />
            {calendar.data?.length === 0 ? (
              <EmptyState>Noch keine E-Mails gesendet.</EmptyState>
            ) : (
              <ul className="divide-y divide-line">
                {(calendar.data ?? []).map((e) => (
                  <li key={e.outreachId} className="flex items-start justify-between gap-3 px-5 py-3 text-sm">
                    <div className="min-w-0">
                      <Link to={`/mandanten/${e.companyId}`} className="block truncate font-medium hover:text-primary">
                        {e.companyName}
                      </Link>
                      <p className="truncate text-xs text-ink-muted" title={e.subject}>
                        Gesendet am {formatDate(dayKey(e.sentAt))} an {e.recipient}
                      </p>
                      <Badge tone={statusTone[e.status]} className="mt-1.5">
                        {statusText[e.status]}
                      </Badge>
                    </div>
                    <Button size="sm" variant={e.status === 'awaiting' ? 'primary' : 'secondary'} onClick={() => setEditing(e)}>
                      {e.status === 'awaiting' ? 'Antwort erfassen' : 'Ändern'}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>

      <ResponseDialog entry={editing} onClose={() => setEditing(undefined)} />
    </>
  )
}

/** datetime-local value (YYYY-MM-DDTHH:mm) of an ISO timestamp, in local time. */
function localInput(iso?: string) {
  if (!iso) return ''
  const d = new Date(iso)
  return `${dayKey(d)}T${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function ResponseDialog({ entry, onClose }: { entry?: CalendarEntry; onClose: () => void }) {
  const record = useRecordResponse()
  const [outcome, setOutcome] = useState<'accepted' | 'rejected'>('accepted')
  const [receivedOn, setReceivedOn] = useState('')
  const [meetingAt, setMeetingAt] = useState('')
  const [note, setNote] = useState('')
  const [shown, setShown] = useState<string>()

  // Start the form from the stored reply whenever another email is opened.
  if (entry && shown !== entry.outreachId) {
    setShown(entry.outreachId)
    setOutcome(entry.response?.outcome ?? 'accepted')
    setReceivedOn(entry.response?.receivedOn ?? dayKey(new Date()))
    setMeetingAt(localInput(entry.response?.meetingAt))
    setNote(entry.response?.note ?? '')
    record.reset()
  }

  const missingMeeting = outcome === 'accepted' && !meetingAt
  const close = () => {
    setShown(undefined)
    onClose()
  }
  const save = () => {
    if (!entry || missingMeeting || !receivedOn) return
    record.mutate(
      {
        outreachId: entry.outreachId,
        input: {
          outcome,
          receivedOn,
          meetingAt: outcome === 'accepted' ? new Date(meetingAt).toISOString() : undefined,
          note: note.trim() || undefined,
        },
      },
      { onSuccess: close },
    )
  }

  return (
    <Dialog
      open={!!entry}
      onClose={close}
      busy={record.isPending}
      title="Antwort erfassen"
      description={entry && `${entry.companyName} · gesendet am ${formatDate(dayKey(entry.sentAt))}`}
      footer={
        <>
          <Button onClick={close} disabled={record.isPending}>
            Abbrechen
          </Button>
          <Button variant="primary" onClick={save} disabled={record.isPending || missingMeeting || !receivedOn}>
            {record.isPending ? 'Bitte warten' : 'Speichern'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {entry && <p className="text-sm text-ink-muted">Betreff: {entry.subject}</p>}
        <fieldset>
          <legend className="mb-1 text-sm font-medium">Antwort des Mandanten</legend>
          <div className="grid grid-cols-2 gap-2">
            {(['accepted', 'rejected'] as const).map((value) => (
              <label
                key={value}
                className={cn(
                  'pressable flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm',
                  outcome === value ? 'border-primary bg-primary-soft text-primary' : 'border-line-strong',
                )}
              >
                <input
                  type="radio"
                  name="outcome"
                  value={value}
                  checked={outcome === value}
                  onChange={() => setOutcome(value)}
                  className="accent-primary"
                />
                {value === 'accepted' ? 'Zugesagt' : 'Abgelehnt'}
              </label>
            ))}
          </div>
        </fieldset>
        <Field label="Antwort erhalten am">
          {(p) => <Input type="date" value={receivedOn} onChange={(e) => setReceivedOn(e.target.value)} {...p} />}
        </Field>
        {outcome === 'accepted' && (
          <Field label="Besprechung am" hint="Wird im Kalender eingetragen." error={missingMeeting ? 'Bitte Datum und Uhrzeit angeben.' : undefined}>
            {(p) => <Input type="datetime-local" value={meetingAt} onChange={(e) => setMeetingAt(e.target.value)} {...p} />}
          </Field>
        )}
        <Field label="Notiz (optional)">
          {(p) => <Textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} {...p} />}
        </Field>
        {record.error && <ErrorState error={record.error} />}
      </div>
    </Dialog>
  )
}
