import { AlertTriangle, CheckCircle2, Loader2, X } from 'lucide-react'
import { cn } from '../../lib/cn'
import { useActivities, type Activity } from './ActivityProvider'

const prefix: Record<Activity['kind'], string> = { upload: 'Neuer Mandant', screening: 'Screening' }

/** Running and finished background work in the top bar; a click opens its result. */
export function ActivityTray() {
  const { activities, dismiss, open } = useActivities()
  if (activities.length === 0) return null
  return (
    <ul aria-label="Hintergrundaufgaben" className="flex min-w-0 items-center gap-2 overflow-x-auto">
      {activities.map((a) => (
        <li
          key={a.id}
          className={cn(
            'activity-chip flex h-8 shrink-0 items-center rounded-full border text-xs font-medium transition-colors duration-300',
            a.state === 'running' && 'border-primary/30 bg-primary-soft text-primary',
            a.state === 'done' && 'border-success/30 bg-success-soft text-success',
            a.state === 'failed' && 'border-danger/30 bg-danger-soft text-danger',
          )}
        >
          <button
            type="button"
            onClick={() => open(a)}
            title={a.message ?? (a.state === 'running' ? 'Läuft im Hintergrund' : undefined)}
            className="pressable flex h-full max-w-56 items-center gap-1.5 rounded-full pr-1 pl-2.5"
          >
            {a.state === 'running' ? (
              <Loader2 className="size-3.5 shrink-0 animate-spin" aria-hidden />
            ) : a.state === 'done' ? (
              <CheckCircle2 className="activity-pop size-3.5 shrink-0" aria-hidden />
            ) : (
              <AlertTriangle className="size-3.5 shrink-0" aria-hidden />
            )}
            <span className="truncate">
              {a.kind === 'screening' ? (a.state === 'done' && a.message ? a.message : prefix.screening) : `${prefix.upload}: ${a.label}`}
            </span>
            <span className="sr-only">
              {a.state === 'running' ? 'läuft' : a.state === 'done' ? 'fertig' : 'fehlgeschlagen'}
            </span>
          </button>
          {a.state !== 'running' && (
            <button
              type="button"
              onClick={() => dismiss(a.id)}
              aria-label="Ausblenden"
              className="pressable mr-1 grid size-6 place-items-center rounded-full opacity-70 hover:opacity-100"
            >
              <X className="size-3.5" aria-hidden />
            </button>
          )}
          {a.state === 'running' && <span className="w-1.5" />}
        </li>
      ))}
    </ul>
  )
}
