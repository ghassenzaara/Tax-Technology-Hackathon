import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ArrowLeft, Check, Mail, RotateCcw, Send, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'
import { AdapterError } from '../../data/adapter'
import type { Company, LawChange, Match } from '../../data/types'
import { useSendClientEmail } from '../../data/mutations'
import { useAdvisor, useEmailTemplate } from '../../data/queries'
import { Button } from '../../components/ui/Button'
import { Dialog } from '../../components/ui/Dialog'
import { Field, Input, Textarea } from '../../components/ui/Field'
import { ErrorState, LoadingState } from '../../components/ui/States'
import { cn } from '../../lib/cn'
import { formatDate } from '../../lib/format'
import { sourceTypeText, textFor } from '../../lib/labels'
import { checkEmail, findLawTerms, termsForClient, uniqueTerms, type TermHit } from './lawTermCheck'

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

type Step = 'edit' | 'confirm'

/** Matches offered for this email: labeled relevant and not yet sent. */
export function sendableMatches(matches: Match[]): Match[] {
  return matches.filter((m) => m.review.label === 'relevant' && m.review.emailStatus === 'not_sent'
    && m.assessment.current !== false && !m.review.sendPending)
}

export function EmailDialog({
  company,
  matches,
  lawChanges,
  open,
  onClose,
}: {
  company: Company
  matches: Match[]
  lawChanges: LawChange[]
  open: boolean
  onClose: () => void
}) {
  const template = useEmailTemplate(company.id, open)
  const advisor = useAdvisor()
  const send = useSendClientEmail()

  const candidates = useMemo(() => sendableMatches(matches), [matches])
  const laws = useMemo(() => new Map(lawChanges.map((l) => [l.id, l])), [lawChanges])

  const [step, setStep] = useState<Step>('edit')
  const [recipient, setRecipient] = useState('')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [errors, setErrors] = useState<{ recipient?: string; subject?: string; body?: string; matches?: string }>({})
  const [hits, setHits] = useState<TermHit[] | null>(null)

  // Fresh state every time the dialog opens; all offered matches ticked.
  useEffect(() => {
    if (!open) return
    setStep('edit')
    setRecipient(company.contact.email ?? '')
    setSelected(new Set(candidates.map((m) => m.id)))
    setErrors({})
    setHits(null)
    send.reset()
    if (template.data) {
      setSubject(template.data.subject)
      setBody(template.data.body)
    }
  }, [open, company.id])

  useEffect(() => {
    if (open && template.data && !subject && !body) {
      setSubject(template.data.subject)
      setBody(template.data.body)
    }
  }, [open, template.data, subject, body])

  const terms = useMemo(() => {
    if (!advisor.data) return []
    const matchedLaws = [...new Set(matches.map((m) => m.lawChangeId))].map((id) => laws.get(id)).filter((l): l is LawChange => !!l)
    return termsForClient(company, matchedLaws, advisor.data)
  }, [advisor.data, company, matches, laws])

  const edited = template.data && (subject !== template.data.subject || body !== template.data.body)

  function changed<T>(setter: (v: T) => void) {
    return (v: T) => {
      setter(v)
      setHits(null)
      setStep('edit')
    }
  }

  function toggle(id: string) {
    setSelected((s) => {
      const next = new Set(s)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function check() {
    const errs: typeof errors = {}
    if (!EMAIL_PATTERN.test(recipient.trim())) errs.recipient = 'Bitte eine gültige E-Mail Adresse angeben.'
    if (!subject.trim()) errs.subject = 'Der Betreff darf nicht leer sein.'
    if (!body.trim()) errs.body = 'Der Text darf nicht leer sein.'
    if (selected.size === 0) errs.matches = 'Bitte mindestens eine Gesetzesänderung auswählen.'
    setErrors(errs)
    const found = checkEmail(subject, body, terms)
    setHits(found)
    if (Object.keys(errs).length === 0 && found.length === 0) setStep('confirm')
  }

  function submit() {
    const chosen = candidates.filter((m) => selected.has(m.id)).map((m) => m.id)
    send.mutate(
      { companyId: company.id, recipient: recipient.trim(), subject, body, matchIds: chosen },
      {
        onSuccess: (o) => {
          toast.success(`E-Mail an ${o.recipient} gesendet.`)
          onClose()
        },
        onError: (e) => {
          if (e instanceof AdapterError && e.code === 'law_terms_found') {
            setHits(checkEmail(subject, body, terms))
            setStep('edit')
          }
        },
      },
    )
  }

  const chosenMatches = candidates.filter((m) => selected.has(m.id))
  const blocked = hits !== null && hits.length > 0

  const footer =
    step === 'edit' ? (
      <>
        <Button onClick={onClose}>Abbrechen</Button>
        <Button variant="primary" onClick={check} disabled={!template.data || candidates.length === 0}>
          <ShieldCheck className="size-4" aria-hidden />
          Prüfen und weiter
        </Button>
      </>
    ) : (
      <>
        <Button onClick={() => setStep('edit')} disabled={send.isPending}>
          <ArrowLeft className="size-4" aria-hidden />
          Zurück
        </Button>
        <Button variant="primary" onClick={submit} disabled={send.isPending}>
          <Send className="size-4" aria-hidden />
          {send.isPending ? 'Wird gesendet' : 'E-Mail senden'}
        </Button>
      </>
    )

  return (
    <Dialog
      open={open}
      onClose={onClose}
      busy={send.isPending}
      size="lg"
      title={
        <span className="flex items-center gap-2">
          <Mail className="size-[18px] text-primary" aria-hidden />
          {step === 'edit' ? 'E-Mail Entwurf an Mandant' : 'E-Mail senden?'}
        </span>
      }
      description={company.name}
      footer={footer}
    >
      {template.isPending && <LoadingState />}
      {template.isError && <ErrorState error={template.error} />}

      {template.data && step === 'edit' && (
        <div className="space-y-5">
          <Field
            label="Empfänger"
            error={errors.recipient}
            hint={company.contact.email ? undefined : 'Für diesen Mandanten ist keine E-Mail Adresse hinterlegt. Bitte tragen Sie den Empfänger hier ein.'}
          >
            {(p) => (
              <Input
                {...p}
                type="email"
                autoFocus={!company.contact.email}
                placeholder="name@unternehmen.de"
                value={recipient}
                onChange={(e) => changed(setRecipient)(e.target.value)}
                className={cn(!company.contact.email && !recipient && 'border-warning')}
              />
            )}
          </Field>
          <Field label="Betreff" error={errors.subject}>
            {(p) => <Input {...p} value={subject} onChange={(e) => changed(setSubject)(e.target.value)} />}
          </Field>
          <div>
            <Field label="Nachricht" error={errors.body} hint="Feste Vorlage für alle Mandanten. Keine Gesetze, Paragraphen oder Details nennen.">
              {(p) => <Textarea {...p} value={body} onChange={(e) => changed(setBody)(e.target.value)} className="min-h-72" />}
            </Field>
            {edited && (
              <button
                type="button"
                onClick={() => {
                  changed(setSubject)(template.data.subject)
                  setBody(template.data.body)
                }}
                className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-primary link-underline"
              >
                <RotateCcw className="size-3" aria-hidden />
                Vorlage wiederherstellen
              </button>
            )}
          </div>

          {blocked && <TermWarning subject={subject} body={body} terms={terms} hits={hits} />}

          <fieldset>
            <legend className="mb-1 text-sm font-medium">Gesetzesänderungen, die als gesendet markiert werden</legend>
            {candidates.length === 0 ? (
              <p className="text-sm text-ink-muted">
                Keine relevanten, noch nicht gesendeten Gesetzesänderungen. Stufen Sie zuerst eine Änderung als relevant ein.
              </p>
            ) : (
              <ul className="divide-y divide-line rounded-lg border border-line">
                {candidates.map((m) => {
                  const law = laws.get(m.lawChangeId)
                  const checked = selected.has(m.id)
                  return (
                    <li key={m.id}>
                      <label className="flex cursor-pointer items-start gap-3 px-3 py-2.5 hover:bg-canvas">
                        <input type="checkbox" checked={checked} onChange={() => toggle(m.id)} className="sr-only peer" />
                        <span
                          aria-hidden
                          className={cn(
                            'mt-0.5 grid size-4 shrink-0 place-items-center rounded border transition-colors duration-150 peer-focus-visible:ring-2 peer-focus-visible:ring-primary/40',
                            checked ? 'border-primary bg-primary text-white' : 'border-line-strong bg-surface',
                          )}
                        >
                          {checked && <Check className="size-3" strokeWidth={3} />}
                        </span>
                        <span className="min-w-0">
                          <span className="block text-sm font-medium">{law?.title ?? m.lawChangeId}</span>
                          {law && (
                            <span className="text-xs text-ink-muted">
                              {textFor(sourceTypeText, law.sourceType)} · {formatDate(law.publishedAt)}
                            </span>
                          )}
                        </span>
                      </label>
                    </li>
                  )
                })}
              </ul>
            )}
            {errors.matches && <p className="mt-1 text-xs text-danger">{errors.matches}</p>}
            <p className="mt-1.5 text-xs text-ink-subtle">Nicht ausgewählte Änderungen bleiben auf „Nicht gesendet“.</p>
          </fieldset>
        </div>
      )}

      {template.data && step === 'confirm' && (
        <div className="space-y-4 text-sm">
          <p className="flex items-center gap-2 rounded-lg bg-success-soft px-3 py-2 text-success">
            <ShieldCheck className="size-4 shrink-0" aria-hidden />
            Prüfung bestanden: keine gesetzesbezogenen Begriffe gefunden.
          </p>
          <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-2">
            <dt className="text-ink-muted">Empfänger</dt>
            <dd className="font-medium break-all">{recipient.trim()}</dd>
            <dt className="text-ink-muted">Betreff</dt>
            <dd className="font-medium">{subject}</dd>
          </dl>
          <div className="max-h-64 overflow-y-auto rounded-lg border border-line bg-canvas px-4 py-3 leading-relaxed whitespace-pre-wrap">{body}</div>
          <div>
            <p className="font-medium">
              {chosenMatches.length === 1 ? '1 Gesetzesänderung wird' : `${chosenMatches.length} Gesetzesänderungen werden`} als gesendet
              markiert:
            </p>
            <ul className="mt-1 list-disc pl-5 text-ink-muted">
              {chosenMatches.map((m) => (
                <li key={m.id}>{laws.get(m.lawChangeId)?.title ?? m.lawChangeId}</li>
              ))}
            </ul>
          </div>
          {send.isError && <ErrorState error={send.error} />}
        </div>
      )}
    </Dialog>
  )
}

function TermWarning({ subject, body, terms, hits }: { subject: string; body: string; terms: string[]; hits: TermHit[] }) {
  return (
    <div role="alert" className="rounded-lg border border-danger/30 bg-danger-soft px-4 py-3 text-sm">
      <p className="flex items-start gap-2 font-medium text-danger">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
        Senden blockiert. Die E-Mail enthält gesetzesbezogene Begriffe:
      </p>
      <p className="mt-2 flex flex-wrap gap-1.5">
        {uniqueTerms(hits).map((t) => (
          <span key={t} className="rounded-md bg-surface px-2 py-0.5 text-xs font-medium text-danger">
            {t}
          </span>
        ))}
      </p>
      <div className="mt-3 rounded-md bg-surface px-3 py-2 leading-relaxed whitespace-pre-wrap text-ink">
        <Highlighted text={subject} hits={findLawTerms(subject, terms)} />
        {'\n\n'}
        <Highlighted text={body} hits={findLawTerms(body, terms)} />
      </div>
      <p className="mt-2 text-xs text-ink-muted">Bitte entfernen Sie diese Begriffe und prüfen Sie erneut.</p>
    </div>
  )
}

/** Plain text with marked hits. Never renders HTML from the text. */
function Highlighted({ text, hits }: { text: string; hits: TermHit[] }) {
  const parts: React.ReactNode[] = []
  let pos = 0
  hits.forEach((h, i) => {
    if (h.index > pos) parts.push(text.slice(pos, h.index))
    parts.push(
      <mark key={i} className="rounded bg-danger/15 px-0.5 font-medium text-danger">
        {text.slice(h.index, h.index + h.length)}
      </mark>,
    )
    pos = h.index + h.length
  })
  parts.push(text.slice(pos))
  return <>{parts}</>
}
