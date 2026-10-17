import type { HTMLAttributes, ReactNode } from 'react'
import { cn } from '../../lib/cn'

export function Card({ className, ...rest }: HTMLAttributes<HTMLElement>) {
  return <section className={cn('rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgba(15,23,42,0.04)]', className)} {...rest} />
}

export function CardHeader({ title, action, className }: { title: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={cn('flex items-center justify-between gap-3 px-5 pt-4 pb-3', className)}>
      <h2 className="text-base font-semibold text-ink">{title}</h2>
      {action}
    </div>
  )
}
