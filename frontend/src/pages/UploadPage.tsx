import { useRef, useState, type DragEvent } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { AlertTriangle, ArrowRight, FileSpreadsheet, FileText, FileX2, FolderOpen, FolderUp, Upload, X } from 'lucide-react'
import { PageHeader } from '../components/PageHeader'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card, CardHeader } from '../components/ui/Card'
import { EmptyState, ErrorState, LoadingState } from '../components/ui/States'
import { Stepper } from '../components/ui/Stepper'
import { adapter, isMockData } from '../data'
import type { UploadFileInfo } from '../data/adapter'
import { useUploadFiles } from '../data/mutations'
import { useCompany } from '../data/queries'
import { MAX_FILE_BYTES, extensionOf, fromDataTransfer, fromFileList, isSupported, mergeFiles } from '../features/upload/collectFiles'
import { STEP, stepsFor } from '../features/upload/steps'
import { cn } from '../lib/cn'
import { formatBytes } from '../lib/format'

export function UploadPage() {
  // Without an id this is a new client: the upload creates it (UI.md 14).
  const { id } = useParams()
  const company = useCompany(id ?? '', !!id)
  const upload = useUploadFiles(id ?? null)
  const navigate = useNavigate()
  const [files, setFiles] = useState<UploadFileInfo[]>([])
  const [dragging, setDragging] = useState(false)
  const [reading, setReading] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)

  if (id && company.isPending) return <LoadingState />
  if (company.isError) return <ErrorState error={company.error} />
  const c = company.data
  const isNew = !c || c.status === 'draft'
  const hrefFor = (companyId: string) => `/mandanten/${encodeURIComponent(companyId)}`

  const add = (added: UploadFileInfo[]) => setFiles((f) => mergeFiles(f, added))

  async function onDrop(e: DragEvent) {
    e.preventDefault()
    setDragging(false)
    setReading(true)
    try {
      add(await fromDataTransfer(e.dataTransfer))
    } finally {
      setReading(false)
    }
  }

  async function useDemoFolder() {
    if (adapter.getDemoFiles) add(await adapter.getDemoFiles())
  }

  function start() {
    upload.mutate(files, {
      onSuccess: (run) => navigate(`${hrefFor(run.companyId)}/pruefung?run=${encodeURIComponent(run.id)}`),
    })
  }

  const unsupported = files.filter((f) => !isSupported(f.relativePath)).length
  const tooLarge = files.filter((f) => f.sizeBytes > MAX_FILE_BYTES).length
  const totalBytes = files.reduce((s, f) => s + f.sizeBytes, 0)

  return (
    <>
      <PageHeader
        title={isNew ? 'Neuer Mandant: DD Unterlagen hochladen' : 'Weitere Dokumente hochladen'}
        description={
          isNew
            ? 'Laden Sie den DD Ordner hoch. Klaris liest das Profil daraus und Sie prüfen es im nächsten Schritt. Erst nach Ihrer Bestätigung wird der Mandant angelegt.'
            : c?.name
        }
        back={isNew || !c ? { to: '/mandanten', label: 'Zurück zu den Mandanten' } : { to: `${hrefFor(c.id)}?tab=dokumente`, label: 'Zurück zum Mandanten' }}
      />
      <Stepper steps={stepsFor(isNew)} current={STEP.upload} />

      <Card>
        <div className="p-5">
          <div
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false)
            }}
            onDrop={onDrop}
            className={cn(
              'flex flex-col items-center rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors duration-150',
              dragging ? 'border-primary bg-primary-soft' : 'border-line-strong bg-canvas',
            )}
          >
            <span className={cn('grid size-12 place-items-center rounded-full bg-surface text-primary shadow-sm transition-transform duration-200 ease-[var(--ease-out)]', dragging && 'scale-110')}>
              <Upload className="size-6" aria-hidden />
            </span>
            <p className="mt-4 text-sm font-medium">
              {reading ? 'Ordner wird gelesen' : 'Ordner oder Dateien hierhin ziehen'}
            </p>
            <p className="mt-1 max-w-md text-xs text-ink-muted">
              Unterstützt werden PDF, XLSX und XLSM bis 50 MB pro Datei. Andere Dateien werden angenommen und als nicht unterstützt gemeldet.
            </p>
            <div className="mt-5 flex flex-wrap justify-center gap-2">
              <Button onClick={() => folderInput.current?.click()}>
                <FolderOpen className="size-4" aria-hidden />
                Ordner auswählen
              </Button>
              <Button onClick={() => fileInput.current?.click()}>
                <FileText className="size-4" aria-hidden />
                Dateien auswählen
              </Button>
              {isMockData && adapter.getDemoFiles && (
                <Button variant="ghost" onClick={useDemoFolder}>
                  <FolderUp className="size-4" aria-hidden />
                  Demo-Ordner verwenden
                </Button>
              )}
            </div>
            <input
              ref={fileInput}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files) add(fromFileList(e.target.files))
                e.target.value = ''
              }}
            />
            <input
              ref={folderInput}
              type="file"
              hidden
              {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
              onChange={(e) => {
                if (e.target.files) add(fromFileList(e.target.files))
                e.target.value = ''
              }}
            />
          </div>
        </div>
      </Card>

      <Card className="mt-6">
        <CardHeader
          title={files.length === 0 ? 'Ausgewählte Dateien' : `${files.length} ${files.length === 1 ? 'Datei' : 'Dateien'} ausgewählt`}
          action={files.length > 0 && <span className="text-xs text-ink-muted tabular-nums">{formatBytes(totalBytes)}</span>}
        />
        {files.length === 0 ? (
          <EmptyState>Noch keine Dateien ausgewählt.</EmptyState>
        ) : (
          <>
            {(unsupported > 0 || tooLarge > 0) && (
              <p className="mx-5 mb-3 flex items-start gap-2 rounded-lg bg-warning-soft px-3 py-2 text-xs text-warning">
                <AlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden />
                {[
                  unsupported > 0 && `${unsupported} ${unsupported === 1 ? 'Datei wird' : 'Dateien werden'} als nicht unterstützt gemeldet`,
                  tooLarge > 0 && `${tooLarge} ${tooLarge === 1 ? 'Datei ist' : 'Dateien sind'} größer als 50 MB`,
                ]
                  .filter(Boolean)
                  .join('. ')}
                . Der Upload ist trotzdem möglich.
              </p>
            )}
            <ul className="max-h-96 divide-y divide-line overflow-y-auto border-t border-line">
              {files.map((f) => (
                <FileRow key={f.relativePath} file={f} onRemove={() => setFiles((all) => all.filter((x) => x.relativePath !== f.relativePath))} />
              ))}
            </ul>
          </>
        )}
      </Card>

      {upload.isError && (
        <div className="mt-4">
          <ErrorState error={upload.error} />
        </div>
      )}

      <div className="mt-6 flex justify-end gap-2">
        {files.length > 0 && <Button onClick={() => setFiles([])}>Auswahl leeren</Button>}
        <Button variant="primary" onClick={start} disabled={files.length === 0 || upload.isPending} className="group">
          {upload.isPending ? 'Wird hochgeladen' : 'Hochladen und prüfen'}
          <ArrowRight className="nudge size-4" aria-hidden />
        </Button>
      </div>
    </>
  )
}

function FileRow({ file, onRemove }: { file: UploadFileInfo; onRemove: () => void }) {
  const ext = extensionOf(file.relativePath)
  const supported = isSupported(file.relativePath)
  const Icon = !supported ? FileX2 : ext === 'pdf' ? FileText : FileSpreadsheet
  const parts = file.relativePath.split('/')
  const name = parts.pop()
  return (
    <li className="flex items-center gap-3 px-5 py-2.5">
      <span
        className={cn(
          'grid size-8 shrink-0 place-items-center rounded-md',
          !supported ? 'bg-neutral-soft text-ink-subtle' : ext === 'pdf' ? 'bg-danger-soft text-danger' : 'bg-success-soft text-success',
        )}
        aria-hidden
      >
        <Icon className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium" title={file.relativePath}>
          {name}
        </p>
        <p className="truncate text-xs text-ink-subtle">{parts.length > 0 ? parts.join(' / ') : 'Hauptordner'}</p>
      </div>
      {!supported && <Badge>Nicht unterstützt</Badge>}
      {file.sizeBytes > MAX_FILE_BYTES && <Badge tone="warning">Über 50 MB</Badge>}
      <span className="w-16 shrink-0 text-right text-xs text-ink-muted tabular-nums">{formatBytes(file.sizeBytes)}</span>
      <button
        type="button"
        onClick={onRemove}
        aria-label={`${name} entfernen`}
        className="pressable grid size-8 shrink-0 place-items-center rounded-lg text-ink-subtle hover:bg-danger-soft hover:text-danger"
      >
        <X className="size-4" aria-hidden />
      </button>
    </li>
  )
}
