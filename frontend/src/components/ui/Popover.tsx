import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { cn } from '../../lib/cn'

/**
 * Small click popover anchored to its trigger. Closes on outside click and Escape,
 * returns focus to the trigger. Scales in from the trigger edge (menu-pop).
 */
export function Popover({
  label,
  trigger,
  children,
  align = 'start',
  className,
}: {
  /** Accessible name of the trigger and the panel. */
  label: string
  trigger: ReactNode
  children: ReactNode
  align?: 'start' | 'end'
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLSpanElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const id = useId()

  useEffect(() => {
    if (!open) return
    const onPointer = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        setOpen(false)
        buttonRef.current?.focus()
      }
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [open])

  return (
    <span ref={rootRef} className="relative inline-flex">
      <button
        ref={buttonRef}
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          'pressable grid size-7 place-items-center rounded-md text-ink-subtle hover:bg-neutral-soft hover:text-primary',
          open && 'bg-primary-soft text-primary',
        )}
      >
        {trigger}
      </button>
      {open && (
        <div
          id={id}
          role="dialog"
          aria-label={label}
          className={cn(
            'menu-pop absolute top-full z-30 mt-1 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-line bg-surface p-3 text-left text-sm shadow-lg shadow-ink/10',
            align === 'start' ? 'left-0 origin-top-left' : 'right-0 origin-top-right',
            className,
          )}
        >
          {children}
        </div>
      )}
    </span>
  )
}
