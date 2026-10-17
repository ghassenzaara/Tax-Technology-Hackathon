import { useMemo, useState, type FormEvent } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import type { CompanyCorePatch, CompanyUpdate } from '../../data/adapter'
import type { Company, Fact } from '../../data/types'
import { useUpdateCompany } from '../../data/mutations'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog, Dialog } from '../../components/ui/Dialog'
import { Field, Input, Select, inputClass } from '../../components/ui/Field'
import { ErrorState } from '../../components/ui/States'
import { cn } from '../../lib/cn'
import { FactValue } from './FactValue'
import { groupFacts } from './StammdatenCard'
import { fromDraft, sameValue, toDraft, type FactDraft } from './factDraft'

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

interface CoreDraft {
  name: string
  legalForm: string
  industry: string
  customerNumber: string
  clientSince: string
  salutation: string
  contactName: string
  email: string
  phone: string
}

function coreDraftOf(c: Company): CoreDraft {
  return {
    name: c.name,
    legalForm: c.legalForm ?? '',
    industry: c.industry ?? '',
    customerNumber: c.customerNumber ?? '',
    clientSince: c.clientSince ?? '',
    salutation: c.contact.salutation ?? '',
    contactName: c.contact.name,
    email: c.contact.email ?? '',
    phone: c.contact.phone ?? '',
  }
}

const FORM_ID = 'profile-editor-form'

const opt = (s: string) => (s.trim() === '' ? undefined : s.trim())

export function ProfileEditor({ company, open, onClose }: { company: Company; open: boolean; onClose: () => void }) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Stammdaten bearbeiten"
      description={company.name}
      size="lg"
      footer={
        <>
          <Button onClick={onClose}>Abbrechen</Button>
          <Button variant="primary" type="submit" form={FORM_ID}>
            Speichern
          </Button>
        </>
      }
    >
      {open && <EditorBody company={company} onClose={onClose} />}
    </Dialog>
  )
}

function EditorBody({ company, onClose }: { company: Company; onClose: () => void }) {
  const [core, setCore] = useState(() => coreDraftOf(company))
  const [drafts, setDrafts] = useState<Record<string, FactDraft>>(() =>
    Object.fromEntries(company.facts.map((f) => [f.id, toDraft(f)])),
  )
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [pending, setPending] = useState<CompanyUpdate | null>(null)
  const update = useUpdateCompany(company.id)
  const groups = useMemo(() => groupFacts(company.facts), [company.facts])

  const setCoreField = (key: keyof CoreDraft) => (e: { target: { value: string } }) =>
    setCore((c) => ({ ...c, [key]: e.target.value }))
  const setDraft = (id: string, d: FactDraft) => setDrafts((all) => ({ ...all, [id]: d }))

  function buildUpdate(): CompanyUpdate | null {
    const errs: Record<string, string> = {}
    if (!core.name.trim()) errs.name = 'Bitte einen Namen angeben.'
    if (!core.contactName.trim()) errs.contactName = 'Bitte einen Ansprechpartner angeben.'
    if (core.email.trim() && !EMAIL_PATTERN.test(core.email.trim())) errs.email = 'Die E-Mail Adresse ist ungültig.'

    const corePatch: CompanyCorePatch = {}
    const next = {
      name: core.name.trim(),
      legalForm: opt(core.legalForm),
      industry: opt(core.industry),
      customerNumber: opt(core.customerNumber),
      clientSince: opt(core.clientSince),
    }
    for (const [k, v] of Object.entries(next) as [keyof typeof next, string | undefined][]) {
      if (!sameValue(company[k], v)) corePatch[k] = v as never
    }
    const contact = { salutation: opt(core.salutation), name: core.contactName.trim(), email: opt(core.email), phone: opt(core.phone) }
    const cleanContact = Object.fromEntries(
      Object.entries(contact).filter(([, v]) => v !== undefined),
    ) as unknown as Company['contact']
    if (!sameValue(company.contact, cleanContact)) corePatch.contact = cleanContact

    const facts: CompanyUpdate['facts'] = []
    for (const f of company.facts) {
      const r = fromDraft(f, drafts[f.id])
      if (!r.ok) errs[f.id] = r.error
      else if (!sameValue(r.value, f.value)) facts.push({ id: f.id, value: r.value })
    }

    setErrors(errs)
    if (Object.keys(errs).length > 0) return null
    return { core: corePatch, facts }
  }

  function onSave(e: FormEvent) {
    e.preventDefault()
    update.reset()
    const u = buildUpdate()
    if (!u) return
    if (Object.keys(u.core ?? {}).length === 0 && (u.facts ?? []).length === 0) {
      toast.info('Keine Änderungen vorhanden.')
      onClose()
      return
    }
    setPending(u)
  }

  function confirmSave() {
    if (!pending) return
    update.mutate(pending, {
      onSuccess: () => {
        toast.success('Stammdaten gespeichert.')
        setPending(null)
        onClose()
      },
      onError: () => setPending(null),
    })
  }

  const changeCount = pending ? Object.keys(pending.core ?? {}).length + (pending.facts ?? []).length : 0

  return (
    <form id={FORM_ID} onSubmit={onSave} noValidate className="space-y-6">
      <section>
        <h3 className="mb-3 text-sm font-semibold">Unternehmen und Kontakt</h3>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name" error={errors.name}>{(p) => <Input {...p} value={core.name} onChange={setCoreField('name')} />}</Field>
          <Field label="Rechtsform">{(p) => <Input {...p} value={core.legalForm} onChange={setCoreField('legalForm')} />}</Field>
          <Field label="Branche">{(p) => <Input {...p} value={core.industry} onChange={setCoreField('industry')} />}</Field>
          <Field label="Kundennummer">{(p) => <Input {...p} value={core.customerNumber} onChange={setCoreField('customerNumber')} />}</Field>
          <Field label="Mandant seit">{(p) => <Input {...p} value={core.clientSince} onChange={setCoreField('clientSince')} />}</Field>
          <Field label="Anrede">
            {(p) => (
              <Select {...p} value={core.salutation} onChange={setCoreField('salutation')}>
                <option value="">Keine</option>
                <option value="Herr">Herr</option>
                <option value="Frau">Frau</option>
                {core.salutation && !['Herr', 'Frau'].includes(core.salutation) && <option value={core.salutation}>{core.salutation}</option>}
              </Select>
            )}
          </Field>
          <Field label="Ansprechpartner" error={errors.contactName}>
            {(p) => <Input {...p} value={core.contactName} onChange={setCoreField('contactName')} />}
          </Field>
          <Field label="E-Mail" error={errors.email}>
            {(p) => <Input {...p} type="email" value={core.email} onChange={setCoreField('email')} />}
          </Field>
          <Field label="Telefon">{(p) => <Input {...p} type="tel" value={core.phone} onChange={setCoreField('phone')} />}</Field>
        </div>
      </section>

      {groups.map(([group, facts]) => (
        <section key={group}>
          <h3 className="mb-3 text-sm font-semibold">{group}</h3>
          <div className="grid gap-4 sm:grid-cols-2">
            {facts.map((f) => (
              <FactEditor key={f.id} fact={f} draft={drafts[f.id]} error={errors[f.id]} onChange={(d) => setDraft(f.id, d)} />
            ))}
          </div>
        </section>
      ))}

      {update.isError && <ErrorState error={update.error} />}

      <ConfirmDialog
        open={pending !== null}
        title="Sind Sie sicher?"
        confirmLabel="Ja, speichern"
        busy={update.isPending}
        onCancel={() => setPending(null)}
        onConfirm={confirmSave}
      >
        {changeCount === 1 ? 'Eine Änderung wird' : `${changeCount} Änderungen werden`} gespeichert und im Verlauf protokolliert.
        Es gibt kein Rückgängig. Ein Fehler wird durch eine neue Änderung korrigiert.
      </ConfirmDialog>
    </form>
  )
}

/** Typed input for one fact. Also used in the document review for own values. */
export function FactEditor({
  fact,
  draft,
  error,
  onChange,
  label: labelOverride,
}: {
  fact: Fact
  draft: FactDraft
  error?: string
  onChange: (d: FactDraft) => void
  label?: string
}) {
  const name = labelOverride ?? fact.label
  const label = fact.unit ? `${name} (${fact.unit})` : name
  const wide = draft.kind === 'rows' || draft.kind === 'items' || draft.kind === 'readonly'

  return (
    <Field label={label} error={error} className={cn(wide && 'sm:col-span-2')}
      hint={draft.kind === 'readonly' ? 'Dieses Feld kann hier nicht bearbeitet werden.' : undefined}>
      {(p) => {
        switch (draft.kind) {
          case 'text':
            return <Input {...p} value={draft.text} onChange={(e) => onChange({ ...draft, text: e.target.value })} />
          case 'number':
            return (
              <Input {...p} type="number" inputMode="decimal" step={draft.integer ? 1 : 'any'} value={draft.text}
                onChange={(e) => onChange({ ...draft, text: e.target.value })} className="tabular-nums" />
            )
          case 'date':
            return <Input {...p} type="date" value={draft.text} onChange={(e) => onChange({ ...draft, text: e.target.value })} />
          case 'boolean':
            return (
              <Select {...p} value={draft.text} onChange={(e) => onChange({ ...draft, text: e.target.value as '' | 'true' | 'false' })}>
                <option value="">Nicht angegeben</option>
                <option value="true">Ja</option>
                <option value="false">Nein</option>
              </Select>
            )
          case 'enum':
            return (
              <Select {...p} value={draft.text} onChange={(e) => onChange({ ...draft, text: e.target.value })}>
                <option value="">Nicht angegeben</option>
                {draft.options.map((o) => (
                  <option key={o} value={o}>{o}</option>
                ))}
              </Select>
            )
          case 'items':
            return (
              <div id={p.id} className="space-y-2">
                {draft.items.map((item, i) => (
                  <div key={i} className="flex gap-2">
                    <Input aria-label={`${fact.label} Eintrag ${i + 1}`} value={item}
                      onChange={(e) => onChange({ ...draft, items: draft.items.map((x, j) => (j === i ? e.target.value : x)) })} />
                    <RemoveButton label={`Eintrag ${i + 1} entfernen`} onClick={() => onChange({ ...draft, items: draft.items.filter((_, j) => j !== i) })} />
                  </div>
                ))}
                <AddButton onClick={() => onChange({ ...draft, items: [...draft.items, ''] })} />
              </div>
            )
          case 'rows':
            return (
              <div id={p.id} className="space-y-2">
                <div className="overflow-x-auto rounded-lg border border-line">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-canvas text-ink-muted">
                      <tr>
                        {draft.columns.map((c) => <th key={c} scope="col" className="px-2 py-1.5 font-medium whitespace-nowrap">{c}</th>)}
                        <th className="w-10"><span className="sr-only">Aktion</span></th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {draft.rows.map((row, r) => (
                        <tr key={r}>
                          {draft.columns.map((c, ci) => (
                            <td key={c} className="p-1">
                              <input aria-label={`${c}, Zeile ${r + 1}`} value={row[ci]}
                                inputMode={draft.numeric[c] ? 'decimal' : undefined}
                                onChange={(e) => onChange({ ...draft, rows: draft.rows.map((x, j) => (j === r ? x.map((y, k) => (k === ci ? e.target.value : y)) : x)) })}
                                className={cn(inputClass, 'h-8 min-w-24 px-2 text-xs', draft.numeric[c] && 'text-right tabular-nums')} />
                            </td>
                          ))}
                          <td className="p-1">
                            <RemoveButton label={`Zeile ${r + 1} entfernen`} onClick={() => onChange({ ...draft, rows: draft.rows.filter((_, j) => j !== r) })} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <AddButton onClick={() => onChange({ ...draft, rows: [...draft.rows, draft.columns.map(() => '')] })} />
              </div>
            )
          case 'readonly':
            return (
              <div id={p.id} className="rounded-lg bg-canvas px-3 py-2 text-sm">
                <FactValue value={fact.value} type={fact.type} unit={fact.unit} />
              </div>
            )
        }
      }}
    </Field>
  )
}

function AddButton({ onClick }: { onClick: () => void }) {
  return (
    <Button size="sm" variant="ghost" onClick={onClick}>
      <Plus className="size-4" aria-hidden />
      Zeile hinzufügen
    </Button>
  )
}

function RemoveButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-label={label}
      className="pressable grid size-8 shrink-0 place-items-center rounded-lg text-ink-subtle hover:bg-danger-soft hover:text-danger">
      <Trash2 className="size-4" aria-hidden />
    </button>
  )
}
