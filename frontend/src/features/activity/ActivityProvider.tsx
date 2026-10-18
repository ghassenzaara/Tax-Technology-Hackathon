import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { adapter } from '../../data'
import { keys } from '../../data/queries'

// Background work the advisor started (an upload creating a client, a screening). It keeps
// running while they work elsewhere; the top bar shows it and a toast announces the result.

export interface Activity {
  /** Run id (upload) or job id (screening). */
  id: string
  kind: 'upload' | 'screening'
  label: string
  companyId?: string
  startedAt: number
  state: 'running' | 'done' | 'failed'
  message?: string
}

interface ActivityContextValue {
  activities: Activity[]
  track: (activity: Omit<Activity, 'startedAt' | 'state'>) => void
  dismiss: (id: string) => void
  clear: () => void
  open: (activity: Activity) => void
}

const STORAGE_KEY = 'klaris-activities-v1'
const MAX_ACTIVITIES = 6
/** A screening that finds nothing new still shows its spinner this long, so the check is visible. */
const MIN_SCREENING_MS = 2500

const ActivityContext = createContext<ActivityContextValue | null>(null)

function load(): Activity[] {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')
    return Array.isArray(value) ? value : []
  } catch {
    return []
  }
}

function save(activities: Activity[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(activities))
  } catch {
    // Storage blocked: the tray still works for this page view.
  }
}

export function hrefFor(activity: Activity) {
  if (activity.kind === 'screening') return '/gesetzesaenderungen'
  const client = `/mandanten/${encodeURIComponent(activity.companyId ?? '')}`
  return activity.state === 'running' ? `${client}/pruefung?run=${encodeURIComponent(activity.id)}` : client
}

export function ActivityProvider({ children }: { children: ReactNode }) {
  const [activities, setActivities] = useState<Activity[]>(load)
  const navigate = useNavigate()

  useEffect(() => save(activities), [activities])

  const track = useCallback((activity: Omit<Activity, 'startedAt' | 'state'>) => {
    setActivities((list) =>
      [{ ...activity, startedAt: Date.now(), state: 'running' as const }, ...list.filter((a) => a.id !== activity.id)].slice(0, MAX_ACTIVITIES),
    )
  }, [])

  const finish = useCallback((id: string, patch: Partial<Activity>) => {
    setActivities((list) => list.map((a) => (a.id === id ? { ...a, ...patch } : a)))
  }, [])

  const dismiss = useCallback((id: string) => setActivities((list) => list.filter((a) => a.id !== id)), [])
  const clear = useCallback(() => setActivities([]), [])

  const open = useCallback(
    (activity: Activity) => {
      navigate(hrefFor(activity))
      if (activity.state !== 'running') dismiss(activity.id)
    },
    [navigate, dismiss],
  )

  const value = useMemo(() => ({ activities, track, dismiss, clear, open }), [activities, track, dismiss, clear, open])

  return (
    <ActivityContext.Provider value={value}>
      {children}
      {activities
        .filter((a) => a.state === 'running')
        .map((a) =>
          a.kind === 'upload' ? (
            <UploadWatcher key={a.id} activity={a} finish={finish} open={open} />
          ) : (
            <ScreeningWatcher key={a.id} activity={a} finish={finish} open={open} />
          ),
        )}
    </ActivityContext.Provider>
  )
}

export function useActivities() {
  const value = useContext(ActivityContext)
  if (!value) throw new Error('useActivities needs an ActivityProvider')
  return value
}

interface WatcherProps {
  activity: Activity
  finish: (id: string, patch: Partial<Activity>) => void
  open: (activity: Activity) => void
}

function UploadWatcher({ activity, finish, open }: WatcherProps) {
  const qc = useQueryClient()
  const run = useQuery({
    queryKey: ['run', activity.id],
    queryFn: () => adapter.getRunProgress(activity.id),
    refetchInterval: (q) => (q.state.data?.status === 'running' || !q.state.data ? 2000 : false),
  })
  const status = run.data?.status

  useEffect(() => {
    if (run.error) {
      finish(activity.id, { state: 'failed', message: 'Verarbeitung nicht gefunden' })
      return
    }
    if (status === 'failed') {
      finish(activity.id, { state: 'failed', message: run.data?.error?.message ?? 'Verarbeitung fehlgeschlagen' })
      toast.error(`${activity.label}: Verarbeitung fehlgeschlagen`)
      return
    }
    if (status !== 'finished') return
    let cancelled = false
    adapter
      .getCompany(activity.companyId ?? '')
      .then((company) => {
        if (cancelled) return
        qc.invalidateQueries({ queryKey: keys.companies })
        qc.invalidateQueries({ queryKey: ['draftCompanies'] })
        if (company.status === 'draft') {
          finish(activity.id, { state: 'failed', message: 'Keine verwertbaren Profildaten' })
          toast.error(`${activity.label}: keine verwertbaren Profildaten gefunden`)
          return
        }
        const done = { ...activity, state: 'done' as const, label: company.name || activity.label }
        finish(activity.id, { state: 'done', label: done.label })
        toast.success(`Mandant angelegt: ${done.label}`, {
          description: 'Das Profil wurde aus den Dokumenten erstellt.',
          action: { label: 'Öffnen', onClick: () => open(done) },
        })
      })
      .catch(() => {
        if (!cancelled) finish(activity.id, { state: 'failed', message: 'Mandant nicht gefunden' })
      })
    return () => {
      cancelled = true
    }
    // Runs once per status change; the activity object itself changes on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, run.error])

  return null
}

function ScreeningWatcher({ activity, finish, open }: WatcherProps) {
  const qc = useQueryClient()
  const job = useQuery({
    queryKey: ['screeningJob', activity.id],
    queryFn: () => adapter.getJob!(activity.id),
    enabled: !!adapter.getJob,
    refetchInterval: (q) => (!q.state.data || ['queued', 'running'].includes(q.state.data.status) ? 1500 : false),
  })
  const status = job.data?.status

  useEffect(() => {
    if (job.error || status === 'failed' || status === 'interrupted') {
      finish(activity.id, { state: 'failed', message: job.data?.error ?? 'Screening fehlgeschlagen' })
      toast.error('Screening fehlgeschlagen', { description: job.data?.error })
      return
    }
    if (status !== 'succeeded') return
    const result = job.data?.result ?? {}
    // Keep the spinner for a moment even when nothing new had to be processed.
    const wait = Math.max(0, activity.startedAt + MIN_SCREENING_MS - Date.now())
    const timer = setTimeout(() => {
      qc.invalidateQueries({ queryKey: ['lawChanges'] })
      qc.invalidateQueries({ queryKey: ['matches'] })
      qc.invalidateQueries({ queryKey: ['processing'] })
      qc.invalidateQueries({ queryKey: ['screeningJob', 'latest'] })
      const hits = Number(result.hits ?? 0)
      const companies = Array.isArray(result.affectedCompanyIds) ? result.affectedCompanyIds.length : 0
      const laws = Number(result.lawChanges ?? 0)
      const message =
        hits > 0
          ? `${hits} neue Treffer bei ${companies} Mandant${companies === 1 ? '' : 'en'}`
          : laws === 0
            ? 'Auf dem neuesten Stand'
            : 'Keine neuen Treffer'
      finish(activity.id, { state: 'done', message })
      const done = { ...activity, state: 'done' as const, message }
      if (hits > 0) {
        toast.warning('Neue relevante Gesetzesänderungen', {
          description: message,
          duration: 12000,
          action: { label: 'Ansehen', onClick: () => open(done) },
        })
      } else if (laws === 0) {
        toast.success('Sie sind auf dem neuesten Stand', { description: 'Keine neuen Dokumente gefunden.' })
      } else {
        toast.success(`${laws} neue Quelle${laws === 1 ? '' : 'n'} geprüft`, { description: 'Keine Mandanten betroffen.' })
      }
    }, wait)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, job.error])

  return null
}
