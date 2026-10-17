import { Check } from 'lucide-react'
import { cn } from '../../lib/cn'

/** Step indicator. `current` is 0 based; earlier steps show a check. */
export function Stepper({ steps, current }: { steps: string[]; current: number }) {
  return (
    <ol className="mb-6 flex flex-wrap items-center gap-x-3 gap-y-2" aria-label="Fortschritt">
      {steps.map((label, i) => {
        const done = i < current
        const active = i === current
        return (
          <li key={label} className="flex items-center gap-3" aria-current={active ? 'step' : undefined}>
            <span className="flex items-center gap-2">
              <span
                className={cn(
                  'grid size-7 shrink-0 place-items-center rounded-full text-xs font-semibold transition-colors duration-200',
                  done && 'bg-primary text-white',
                  active && 'bg-primary text-white ring-4 ring-primary/15',
                  !done && !active && 'bg-neutral-soft text-ink-muted',
                )}
              >
                {done ? <Check className="size-3.5" strokeWidth={3} aria-hidden /> : i + 1}
              </span>
              <span className={cn('text-sm', active ? 'font-medium text-ink' : 'text-ink-muted')}>
                {label}
                {done && <span className="sr-only"> (erledigt)</span>}
              </span>
            </span>
            {i < steps.length - 1 && <span className="hidden h-px w-10 bg-line sm:block" aria-hidden />}
          </li>
        )
      })}
    </ol>
  )
}
