import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Bell, CalendarDays, Home, Menu, Search, Settings, Users, X } from 'lucide-react'
import logoUrl from '../assets/klaris-logo.png'
import { isMockData } from '../data'
import { unreadLawChangeIds } from '../data/derive'
import { useAdvisor, useMatches } from '../data/queries'
import { Badge } from '../components/ui/Badge'
import { ActivityProvider } from '../features/activity/ActivityProvider'
import { ActivityTray } from '../features/activity/ActivityTray'
import { cn } from '../lib/cn'

function initials(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .map((p) => p[0])
    .slice(-2)
    .join('')
    .toUpperCase()
}

function useUnreadCount() {
  const advisor = useAdvisor()
  const matches = useMatches()
  return useMemo(
    () => (advisor.data && matches.data ? unreadLawChangeIds(matches.data, advisor.data).length : 0),
    [advisor.data, matches.data],
  )
}

const navItems = [
  { to: '/', label: 'Dashboard', icon: Home, end: true },
  { to: '/mandanten', label: 'Mandanten', icon: Users, end: false },
  { to: '/gesetzesaenderungen', label: 'Gesetzesänderungen', icon: Bell, end: false, unread: true },
  { to: '/kalender', label: 'Kalender', icon: CalendarDays, end: false },
  { to: '/einstellungen', label: 'Einstellungen', icon: Settings, end: false },
]

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const advisor = useAdvisor()
  const unread = useUnreadCount()
  return (
    <div className="flex h-full flex-col bg-sidebar text-sidebar-ink">
      <div className="flex items-center px-5 py-5">
        <img src={logoUrl} alt="Klaris" className="h-7 w-auto" />
      </div>
      <nav aria-label="Hauptnavigation" className="flex-1 px-3">
        <ul className="space-y-0.5">
          {navItems.map(({ to, label, icon: Icon, end, unread: hasUnread }) => (
            <li key={to}>
              <NavLink
                to={to}
                end={end}
                onClick={onNavigate}
                className={({ isActive }) =>
                  cn(
                    'group flex h-10 items-center gap-3 rounded-lg px-3 text-sm font-medium',
                    isActive ? 'bg-sidebar-active text-white' : 'hover:bg-sidebar-hover hover:text-white',
                  )
                }
              >
                <Icon className="nudge size-[18px] shrink-0" aria-hidden />
                <span className="flex-1 truncate">{label}</span>
                {hasUnread && unread > 0 && (
                  <span
                    className="grid min-w-5 place-items-center rounded-full bg-primary px-1.5 text-xs font-semibold text-white tabular-nums"
                    aria-label={`${unread} ungelesen`}
                  >
                    {unread}
                  </span>
                )}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      {advisor.data && (
        <div className="m-3 flex items-center gap-3 rounded-lg bg-white/5 px-3 py-3">
          <span className="grid size-9 place-items-center rounded-full bg-white text-sm font-semibold text-sidebar">
            {initials(advisor.data.name)}
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-white">{advisor.data.name}</p>
            <p className="truncate text-xs">{advisor.data.title}</p>
          </div>
        </div>
      )}
    </div>
  )
}

function TopBar({ onOpenMenu }: { onOpenMenu: () => void }) {
  const navigate = useNavigate()
  const advisor = useAdvisor()
  const [query, setQuery] = useState('')

  function submit(e: FormEvent) {
    e.preventDefault()
    const q = query.trim()
    navigate(q ? `/mandanten?q=${encodeURIComponent(q)}` : '/mandanten')
  }

  return (
    <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-line bg-surface/95 px-4 backdrop-blur sm:px-6">
      <button
        type="button"
        onClick={onOpenMenu}
        className="pressable grid size-10 place-items-center rounded-lg text-ink-muted hover:bg-neutral-soft lg:hidden"
        aria-label="Menü öffnen"
      >
        <Menu className="size-5" aria-hidden />
      </button>
      <form role="search" onSubmit={submit} className="relative w-full max-w-sm">
        <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-ink-subtle" aria-hidden />
        <label htmlFor="global-search" className="sr-only">
          Mandanten suchen
        </label>
        <input
          id="global-search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Mandanten suchen"
          className="h-10 w-full rounded-lg border border-line bg-canvas pr-3 pl-9 text-sm placeholder:text-ink-subtle focus:border-primary focus:bg-surface focus:outline-none"
        />
      </form>
      <div className="hidden min-w-0 flex-1 md:block">
        <ActivityTray />
      </div>
      <div className="ml-auto flex shrink-0 items-center gap-3">
        {isMockData && (
          <Badge tone="warning" className="hidden sm:inline-flex">
            Beispieldaten
          </Badge>
        )}
        {advisor.data && (
          <span
            className="grid size-9 place-items-center rounded-full bg-primary-soft text-sm font-semibold text-primary"
            title={advisor.data.name}
            aria-hidden
          >
            {initials(advisor.data.name)}
          </span>
        )}
      </div>
    </header>
  )
}

export function Layout() {
  const [menuOpen, setMenuOpen] = useState(false)
  const location = useLocation()

  useEffect(() => setMenuOpen(false), [location.pathname])

  useEffect(() => {
    if (!menuOpen) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setMenuOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [menuOpen])

  return (
    <ActivityProvider>
    <div className="min-h-dvh lg:grid lg:grid-cols-[256px_1fr]">
      <aside className="sticky top-0 hidden h-dvh lg:block">
        <Sidebar />
      </aside>

      {/* Mobile drawer */}
      <div
        className={cn('fixed inset-0 z-40 lg:hidden', menuOpen ? 'pointer-events-auto' : 'pointer-events-none')}
        aria-hidden={!menuOpen}
      >
        <div
          className={cn(
            'absolute inset-0 bg-ink/40 transition-opacity duration-200',
            menuOpen ? 'opacity-100' : 'opacity-0',
          )}
          onClick={() => setMenuOpen(false)}
        />
        <div
          role="dialog"
          data-motion="slide"
          aria-modal="true"
          aria-label="Navigation"
          inert={!menuOpen}
          className={cn(
            'absolute inset-y-0 left-0 w-72 max-w-[85vw] transition-transform duration-250 ease-[cubic-bezier(0.32,0.72,0,1)]',
            menuOpen ? 'translate-x-0' : '-translate-x-full',
          )}
        >
          <Sidebar onNavigate={() => setMenuOpen(false)} />
          <button
            type="button"
            onClick={() => setMenuOpen(false)}
            className="pressable absolute top-4 right-3 grid size-9 place-items-center rounded-lg text-sidebar-ink hover:bg-sidebar-hover hover:text-white"
            aria-label="Menü schließen"
          >
            <X className="size-5" aria-hidden />
          </button>
        </div>
      </div>

      <div className="min-w-0">
        <TopBar onOpenMenu={() => setMenuOpen(true)} />
        <div className="border-b border-line px-4 py-2 md:hidden empty:hidden">
          <ActivityTray />
        </div>
        <main className="mx-auto w-full max-w-[1280px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          <Outlet />
        </main>
      </div>
    </div>
    </ActivityProvider>
  )
}
