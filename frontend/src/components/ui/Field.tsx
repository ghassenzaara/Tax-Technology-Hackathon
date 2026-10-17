import { useId, type ComponentProps, type ReactNode } from 'react'
import { cn } from '../../lib/cn'

export const inputClass =
  'w-full rounded-lg border border-line-strong bg-surface px-3 text-sm text-ink placeholder:text-ink-subtle transition-colors duration-150 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:bg-canvas disabled:text-ink-subtle aria-[invalid=true]:border-danger'

export function Field({
  label,
  hint,
  error,
  children,
  className,
}: {
  label: ReactNode
  hint?: ReactNode
  error?: ReactNode
  children: (props: { id: string; 'aria-invalid'?: boolean; 'aria-describedby'?: string }) => ReactNode
  className?: string
}) {
  const id = useId()
  const describedBy = [hint && `${id}-hint`, error && `${id}-error`].filter(Boolean).join(' ') || undefined
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1 block text-sm font-medium text-ink">
        {label}
      </label>
      {children({ id, 'aria-invalid': error ? true : undefined, 'aria-describedby': describedBy })}
      {hint && !error && (
        <p id={`${id}-hint`} className="mt-1 text-xs text-ink-subtle">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="mt-1 text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  )
}

export function Input({ className, ...rest }: ComponentProps<'input'>) {
  return <input className={cn(inputClass, 'h-10', className)} {...rest} />
}

export function Select({ className, ...rest }: ComponentProps<'select'>) {
  return <select className={cn(inputClass, 'h-10 pr-8', className)} {...rest} />
}

export function Textarea({ className, ...rest }: ComponentProps<'textarea'>) {
  return <textarea className={cn(inputClass, 'min-h-24 py-2 leading-relaxed', className)} {...rest} />
}
