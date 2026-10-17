import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { Check, ChevronDown } from 'lucide-react'
import { cn } from '../../lib/cn'

export interface MenuOption {
  value: string
  label: string
  /** Tailwind background class for the status dot, for example bg-success. */
  dot?: string
}

/**
 * Compact select in the app style (button plus listbox). Keyboard: Enter, Space
 * or arrows open it; arrows move; Enter or Space picks; Escape closes.
 */
export function SelectMenu({
  value,
  options,
  onChange,
  label,
  disabled,
  className,
}: {
  value: string
  options: MenuOption[]
  onChange: (value: string) => void
  /** Accessible name, for example "Einstufung für <Gesetz>". */
  label: string
  disabled?: boolean
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [above, setAbove] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const id = useId()
  const current = options.find((o) => o.value === value) ?? { value, label: value }

  function openMenu() {
    if (disabled) return
    // Open upwards when there is no room below.
    const rect = buttonRef.current?.getBoundingClientRect()
    setAbove(!!rect && window.innerHeight - rect.bottom < options.length * 40 + 16)
    setActive(Math.max(0, options.findIndex((o) => o.value === value)))
    setOpen(true)
  }

  function close(focusButton = true) {
    setOpen(false)
    if (focusButton) buttonRef.current?.focus()
  }

  function pick(v: string) {
    close()
    if (v !== value) onChange(v)
  }

  useEffect(() => {
    if (!open) return
    listRef.current?.focus()
    const onPointer = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) close(false)
    }
    document.addEventListener('pointerdown', onPointer)
    return () => document.removeEventListener('pointerdown', onPointer)
  }, [open])

  function onButtonKey(e: KeyboardEvent) {
    if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) {
      e.preventDefault()
      openMenu()
    }
  }

  function onListKey(e: KeyboardEvent) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const d = e.key === 'ArrowDown' ? 1 : -1
      setActive((a) => (a + d + options.length) % options.length)
    } else if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault()
      setActive(e.key === 'Home' ? 0 : options.length - 1)
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      pick(options[active].value)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      close()
    } else if (e.key === 'Tab') {
      close(false)
    }
  }

  return (
    <div ref={rootRef} className={cn('relative', className)}>
      <button
        ref={buttonRef}
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? `${id}-list` : undefined}
        aria-label={`${label}: ${current.label}`}
        onClick={() => (open ? close() : openMenu())}
        onKeyDown={onButtonKey}
        className={cn(
          'pressable flex h-9 w-full items-center gap-2 rounded-lg border border-line bg-surface pr-2 pl-2.5 text-left text-xs font-medium text-ink',
          'hover:border-line-strong disabled:cursor-not-allowed disabled:opacity-60',
          open && 'border-primary ring-2 ring-primary/15',
        )}
      >
        {current.dot && <span className={cn('size-2 shrink-0 rounded-full', current.dot)} aria-hidden />}
        <span className="min-w-0 flex-1 truncate">{current.label}</span>
        <ChevronDown className={cn('size-3.5 shrink-0 text-ink-subtle transition-transform duration-150', open && 'rotate-180')} aria-hidden />
      </button>

      {open && (
        <ul
          ref={listRef}
          id={`${id}-list`}
          role="listbox"
          tabIndex={-1}
          aria-label={label}
          aria-activedescendant={`${id}-opt-${active}`}
          onKeyDown={onListKey}
          className={cn(
            'menu-pop absolute right-0 left-0 z-30 min-w-40 rounded-lg border border-line bg-surface p-1 shadow-lg shadow-ink/10 focus:outline-none',
            above ? 'bottom-full mb-1 origin-bottom' : 'top-full mt-1 origin-top',
          )}
        >
          {options.map((o, i) => {
            const selected = o.value === value
            return (
              <li
                key={o.value}
                id={`${id}-opt-${i}`}
                role="option"
                aria-selected={selected}
                onPointerEnter={() => setActive(i)}
                onClick={() => pick(o.value)}
                className={cn(
                  'flex h-8 cursor-pointer items-center gap-2 rounded-md px-2 text-xs text-ink',
                  i === active && 'bg-neutral-soft',
                  selected && 'font-medium',
                )}
              >
                {o.dot && <span className={cn('size-2 shrink-0 rounded-full', o.dot)} aria-hidden />}
                <span className="flex-1 whitespace-nowrap">{o.label}</span>
                {selected && <Check className="size-3.5 text-primary" aria-hidden />}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
