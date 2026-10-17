import { AlertTriangle, Loader2 } from 'lucide-react'
import { AdapterError } from '../../data/adapter'

export function LoadingState({ label = 'Wird geladen' }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 px-5 py-8 text-sm text-ink-muted">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      {label}
    </div>
  )
}

export function ErrorState({ error }: { error: unknown }) {
  const message =
    error instanceof AdapterError || error instanceof Error ? error.message : 'Ein unbekannter Fehler ist aufgetreten.'
  return (
    <div role="alert" className="flex items-start gap-2 rounded-lg bg-danger-soft px-4 py-3 text-sm text-danger">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span>{message}</span>
    </div>
  )
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return <p className="px-5 py-8 text-center text-sm text-ink-subtle">{children}</p>
}
