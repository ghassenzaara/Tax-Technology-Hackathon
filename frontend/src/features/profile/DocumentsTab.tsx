import { Link } from 'react-router-dom'
import { FolderUp, ShieldCheck, Upload } from 'lucide-react'
import type { Company } from '../../data/types'
import { buttonClasses } from '../../components/ui/Button'
import { Card, CardHeader } from '../../components/ui/Card'

/** No document list: raw documents are deleted after processing (UI.md 7). */
export function DocumentsTab({ company }: { company: Company }) {
  return (
    <Card>
      <CardHeader title="Dokumente" />
      <div className="flex flex-col items-center px-5 pt-6 pb-8 text-center">
        <span className="grid size-12 place-items-center rounded-full bg-primary-soft text-primary">
          <FolderUp className="size-6" aria-hidden />
        </span>
        <p className="mt-4 max-w-md text-sm text-ink-muted">
          Neue Dokumente werden ausgewertet und die Angaben automatisch im Profil gespeichert. Sie können die Werte anschließend bearbeiten.
        </p>
        <Link
          to={`/mandanten/${encodeURIComponent(company.id)}/upload`}
          className={buttonClasses('primary', 'md', 'mt-5 px-5')}
        >
          <Upload className="size-4" aria-hidden />
          Weitere Dokumente hochladen
        </Link>
        <p className="mt-6 flex max-w-md items-start gap-2 text-left text-xs text-ink-subtle">
          <ShieldCheck className="mt-px size-4 shrink-0 text-success" aria-hidden />
          Hochgeladene Dokumente werden nach der Auswertung gelöscht. Im Profil bleiben die gespeicherten Angaben.
        </p>
      </div>
    </Card>
  )
}
