import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { cn } from '../../lib/cn'
import { Button } from './Button'

// Native <dialog>: focus trap, Escape and top layer come from the browser.

interface DialogProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  description?: ReactNode
  children?: ReactNode
  footer?: ReactNode
  variant?: 'modal' | 'sheet'
  size?: 'sm' | 'md' | 'lg'
  /** Block closing (Escape, backdrop, X) while work is in progress. */
  busy?: boolean
}

const widths = { sm: 'sm:max-w-md', md: 'sm:max-w-xl', lg: 'sm:max-w-3xl' }

export function Dialog({ open, onClose, title, description, children, footer, variant = 'modal', size = 'md', busy }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  const descId = useId()

  // Content stays mounted until the exit transition ends, then unmounts so forms start fresh.
  const [render, setRender] = useState(open)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (open) {
      setRender(true)
      if (!el.open) el.showModal()
      return
    }
    if (el.open) el.close()
    const t = setTimeout(() => setRender(false), 280)
    return () => clearTimeout(t)
  }, [open])

  const requestClose = () => {
    if (!busy) onClose()
  }

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={description ? descId : undefined}
      onCancel={(e) => {
        e.preventDefault()
        requestClose()
      }}
      onClick={(e) => {
        if (e.target === ref.current) requestClose()
      }}
      className={cn(
        'app-dialog m-0 max-h-dvh max-w-none overflow-clip bg-transparent p-0 text-ink backdrop:bg-ink/40',
        variant === 'modal' ? 'app-dialog-modal inset-0 m-auto w-full sm:w-[calc(100%-2rem)]' : 'app-dialog-sheet ml-auto h-dvh w-full sm:w-[min(32rem,100%)]',
        variant === 'modal' && widths[size],
      )}
    >
      {(open || render) && (
        <div
          className={cn(
            'flex flex-col bg-surface shadow-xl',
            variant === 'modal' ? 'max-h-[calc(100dvh-2rem)] rounded-xl' : 'h-full',
          )}
        >
          <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
            <div className="min-w-0">
              <h2 id={titleId} className="text-base font-semibold">
                {title}
              </h2>
              {description && (
                <p id={descId} className="mt-0.5 text-sm text-ink-muted">
                  {description}
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={requestClose}
              disabled={busy}
              className="pressable -mr-2 grid size-9 shrink-0 place-items-center rounded-lg text-ink-muted hover:bg-neutral-soft hover:text-ink disabled:opacity-40"
              aria-label="Schließen"
            >
              <X className="size-5" aria-hidden />
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
          {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
        </div>
      )}
    </dialog>
  )
}

export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  onConfirm,
  onCancel,
  busy,
  tone = 'primary',
}: {
  open: boolean
  title: ReactNode
  children?: ReactNode
  confirmLabel: string
  onConfirm: () => void
  onCancel: () => void
  busy?: boolean
  tone?: 'primary' | 'danger'
}) {
  return (
    <Dialog
      open={open}
      onClose={onCancel}
      title={title}
      size="sm"
      busy={busy}
      footer={
        <>
          <Button onClick={onCancel} disabled={busy}>
            Abbrechen
          </Button>
          <Button variant={tone} onClick={onConfirm} disabled={busy}>
            {busy ? 'Bitte warten' : confirmLabel}
          </Button>
        </>
      }
    >
      <div className="text-sm text-ink-muted">{children}</div>
    </Dialog>
  )
}
