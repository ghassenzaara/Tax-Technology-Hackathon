import { useMemo, useRef, useState, type ReactNode } from 'react'
import { AlertTriangle, History, Pencil, RotateCcw } from 'lucide-react'
import { toast } from 'sonner'
import { useSaveEmailTemplate } from '../../data/mutations'
import { useAdvisor, useLawChanges, useStoredEmailTemplate } from '../../data/queries'
import type { StoredEmailTemplate } from '../../data/adapter'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Card, CardHeader } from '../../components/ui/Card'
import { ConfirmDialog } from '../../components/ui/Dialog'
import { Field, Input, Textarea } from '../../components/ui/Field'
import { ErrorState, LoadingState } from '../../components/ui/States'
import { IconButton } from '../profile/StammdatenCard'
import { formatDateTime } from '../../lib/format'
import { templateProblems } from './lawTermCheck'
import { TemplateHistoryPanel } from './TemplateHistoryPanel'
import { PLACEHOLDERS, fillTemplate } from './template'

const SAMPLE_CONTACT = { salutation: 'Herr', name: 'Max Mustermann' }

const placeholderText: Record<string, string> = {
  '{Anrede}': 'Begrüßung mit Name des Ansprechpartners',
  '{Signatur}': 'Ihr Name, Titel und Kanzlei',
}

/** Text with the supported placeholders shown as chips. Plain text only. */
function WithPlaceholders({ text }: { text: string }) {
  const parts = text.split(/(\{Anrede\}|\{Signatur\})/g)
  return (
    <>
      {parts.map((p, i) =>
        PLACEHOLDERS.includes(p) ? (
          <span key={i} title={placeholderText[p]} className="rounded bg-primary-soft px-1 py-px font-medium text-primary">
            {p}
          </span>
        ) : (
          p
        ),
      )}
    </>
  )
}

export function TemplateSettings() {
  const stored = useStoredEmailTemplate()
  const [editing, setEditing] = useState(false)
  const [history, setHistory] = useState(false)

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex flex-wrap items-center gap-2">
            Aktuelle Vorlage
            {stored.data && <Badge tone={stored.data.isDefault ? 'neutral' : 'info'}>{stored.data.isDefault ? 'Standard' : 'Angepasst'}</Badge>}
          </span>
        }
        action={
          !editing &&
          stored.data && (
            <div className="-mr-2 flex">
              <IconButton label="Verlauf der Vorlage anzeigen" onClick={() => setHistory(true)}>
                <History className="size-[18px]" aria-hidden />
              </IconButton>
              <IconButton label="Vorlage bearbeiten" onClick={() => setEditing(true)} tone="primary">
                <Pencil className="size-[18px]" aria-hidden />
              </IconButton>
            </div>
          )
        }
      />
      <div className="px-5 pb-5">
        {stored.isPending && <LoadingState />}
        {stored.isError && <ErrorState error={stored.error} />}
        {stored.data && !editing && <TemplateView template={stored.data} />}
        {stored.data && editing && <TemplateEditor template={stored.data} onDone={() => setEditing(false)} />}
      </div>
      <TemplateHistoryPanel open={history} onClose={() => setHistory(false)} />
    </Card>
  )
}

function Note({ children }: { children: ReactNode }) {
  return <p className="text-xs leading-relaxed text-ink-muted">{children}</p>
}

function TemplateView({ template }: { template: StoredEmailTemplate }) {
  const advisor = useAdvisor()
  return (
    <div className="space-y-4">
      <Note>
        Diese Vorlage gilt für alle Mandanten. Pro E-Mail werden nur <WithPlaceholders text="{Anrede}" /> und{' '}
        <WithPlaceholders text="{Signatur}" /> ausgefüllt. Änderungen gelten für künftige E-Mails, bereits gesendete E-Mails bleiben
        unverändert.
      </Note>
      <dl className="space-y-3 text-sm">
        <div>
          <dt className="text-xs text-ink-muted">Betreff</dt>
          <dd className="mt-0.5 font-medium">
            <WithPlaceholders text={template.subject} />
          </dd>
        </div>
        <div>
          <dt className="text-xs text-ink-muted">Text</dt>
          <dd className="mt-1 rounded-lg border border-line bg-canvas px-4 py-3 leading-relaxed whitespace-pre-wrap">
            <WithPlaceholders text={template.body} />
          </dd>
        </div>
      </dl>
      <p className="text-xs text-ink-subtle">
        {template.isDefault
          ? 'Standardvorlage'
          : `Zuletzt geändert am ${template.updatedAt ? formatDateTime(template.updatedAt) : 'unbekannt'}${
              template.updatedBy ? ` von ${advisor.data?.id === template.updatedBy ? advisor.data.name : template.updatedBy}` : ''
            }`}
      </p>
    </div>
  )
}

function TemplateEditor({ template, onDone }: { template: StoredEmailTemplate; onDone: () => void }) {
  const [subject, setSubject] = useState(template.subject)
  const [body, setBody] = useState(template.body)
  const [problems, setProblems] = useState<string[]>([])
  const [confirm, setConfirm] = useState<'save' | 'reset' | null>(null)
  const bodyRef = useRef<HTMLTextAreaElement>(null)
  const advisor = useAdvisor()
  const lawChanges = useLawChanges()
  const { update, reset } = useSaveEmailTemplate()
  const busy = update.isPending || reset.isPending

  const preview = useMemo(
    () => (advisor.data ? fillTemplate(SAMPLE_CONTACT, advisor.data, { subject, body }) : null),
    [advisor.data, subject, body],
  )
  const changed = subject !== template.subject || body !== template.body

  function insert(placeholder: string) {
    const el = bodyRef.current
    const start = el?.selectionStart ?? body.length
    const end = el?.selectionEnd ?? body.length
    setBody(body.slice(0, start) + placeholder + body.slice(end))
    setProblems([])
    requestAnimationFrame(() => {
      el?.focus()
      el?.setSelectionRange(start + placeholder.length, start + placeholder.length)
    })
  }

  function onSave() {
    const { messages } = templateProblems(subject, body, lawChanges.data ?? [])
    setProblems(messages)
    if (messages.length === 0) setConfirm('save')
  }

  function run() {
    const mutation = confirm === 'reset' ? reset.mutateAsync() : update.mutateAsync({ subject, body })
    mutation
      .then(() => {
        toast.success(confirm === 'reset' ? 'Standardvorlage wiederhergestellt.' : 'Vorlage gespeichert.')
        setConfirm(null)
        onDone()
      })
      .catch((e: Error) => {
        setConfirm(null)
        setProblems([e.message])
      })
  }

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div className="space-y-4">
        <Field label="Betreff">
          {(p) => <Input {...p} value={subject} onChange={(e) => (setSubject(e.target.value), setProblems([]))} />}
        </Field>
        <div>
          <Field label="Text" hint="Keine Gesetze, Paragraphen, Behörden oder Urteile nennen. Die Vorlage gilt für alle Mandanten.">
            {(p) => (
              <Textarea
                {...p}
                ref={bodyRef}
                value={body}
                onChange={(e) => (setBody(e.target.value), setProblems([]))}
                className="min-h-80 font-[inherit]"
              />
            )}
          </Field>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="text-xs text-ink-muted">Platzhalter einfügen:</span>
            {PLACEHOLDERS.map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => insert(p)}
                title={placeholderText[p]}
                className="pressable rounded-md border border-line bg-primary-soft px-2 py-1 text-xs font-medium text-primary hover:border-primary/40"
              >
                {p}
              </button>
            ))}
          </div>
        </div>

        {problems.length > 0 && (
          <div role="alert" className="rounded-lg border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">
            <p className="flex items-center gap-2 font-medium">
              <AlertTriangle className="size-4 shrink-0" aria-hidden />
              Vorlage kann nicht gespeichert werden
            </p>
            <ul className="mt-1 list-disc pl-6">
              {problems.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" onClick={onSave} disabled={!changed || busy}>
            Speichern
          </Button>
          <Button onClick={onDone} disabled={busy}>
            Abbrechen
          </Button>
          {!template.isDefault && (
            <Button variant="ghost" onClick={() => setConfirm('reset')} disabled={busy} className="ml-auto">
              <RotateCcw className="size-4" aria-hidden />
              Standard wiederherstellen
            </Button>
          )}
        </div>
      </div>

      <div>
        <p className="mb-1 text-sm font-medium">Vorschau</p>
        <p className="mb-2 text-xs text-ink-muted">Beispiel für den Ansprechpartner Herr Max Mustermann.</p>
        {preview && (
          <div className="rounded-lg border border-line bg-canvas px-4 py-3 text-sm">
            <p className="border-b border-line pb-2 font-medium">{preview.subject}</p>
            <p className="pt-2 leading-relaxed whitespace-pre-wrap">{preview.body}</p>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirm !== null}
        title="Sind Sie sicher?"
        confirmLabel={confirm === 'reset' ? 'Ja, wiederherstellen' : 'Ja, speichern'}
        busy={busy}
        onCancel={() => setConfirm(null)}
        onConfirm={run}
      >
        {confirm === 'reset'
          ? 'Die angepasste Vorlage wird durch die Standardvorlage ersetzt.'
          : 'Die neue Vorlage gilt ab sofort für alle künftigen E-Mails an alle Mandanten. Bereits gesendete E-Mails bleiben unverändert.'}
      </ConfirmDialog>
    </div>
  )
}
