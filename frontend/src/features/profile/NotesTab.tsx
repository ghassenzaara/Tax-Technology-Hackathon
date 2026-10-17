import { useEffect, useRef, useState } from 'react'
import { Check, Info, Loader2 } from 'lucide-react'
import type { Company } from '../../data/types'
import { useSaveCompanyNote } from '../../data/mutations'
import { Card, CardHeader } from '../../components/ui/Card'
import { Textarea } from '../../components/ui/Field'

const SAVE_DELAY_MS = 800

/** One free text note per client, saved automatically. */
export function NotesTab({ company }: { company: Company }) {
  const [text, setText] = useState(company.notes ?? '')
  const save = useSaveCompanyNote(company.id)
  const lastSaved = useRef(company.notes ?? '')
  const latest = useRef(text)
  latest.current = text

  useEffect(() => {
    if (text === lastSaved.current) return
    const t = setTimeout(() => {
      lastSaved.current = text
      save.mutate(text)
    }, SAVE_DELAY_MS)
    return () => clearTimeout(t)
  }, [text])

  // Save pending text when leaving the tab.
  useEffect(
    () => () => {
      if (latest.current !== lastSaved.current) save.mutate(latest.current)
    },
    [],
  )

  const status = save.isPending ? (
    <><Loader2 className="size-3.5 animate-spin" aria-hidden /> Wird gespeichert</>
  ) : save.isError ? (
    <span className="text-danger">Speichern fehlgeschlagen: {save.error.message}</span>
  ) : save.isSuccess && text === lastSaved.current ? (
    <><Check className="size-3.5 text-success" aria-hidden /> Gespeichert</>
  ) : (
    <><Info className="size-3.5" aria-hidden /> Ihre Notizen werden automatisch gespeichert.</>
  )

  return (
    <Card>
      <CardHeader title="Notizen" />
      <div className="px-5 pb-5">
        <label htmlFor="company-notes" className="sr-only">Notiz zu {company.name}</label>
        <Textarea id="company-notes" value={text} onChange={(e) => setText(e.target.value)} placeholder="Notiz hinzufügen" className="min-h-48" />
        <p role="status" className="mt-2 flex items-center gap-1.5 text-xs text-ink-muted">{status}</p>
      </div>
    </Card>
  )
}
