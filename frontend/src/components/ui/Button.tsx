import type { ButtonHTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md'

const variants: Record<Variant, string> = {
  primary: 'bg-primary text-white hover:bg-primary-hover shadow-sm',
  secondary: 'bg-surface text-ink border border-line-strong hover:bg-canvas',
  ghost: 'text-ink-muted hover:bg-neutral-soft hover:text-ink',
  danger: 'bg-danger text-white hover:bg-red-800 shadow-sm',
}

const sizes: Record<Size, string> = {
  sm: 'h-8 px-3 text-sm gap-1.5',
  md: 'h-10 px-4 text-sm gap-2',
}

const motion: Record<Variant, string> = {
  primary: 'hover-lift',
  danger: 'hover-lift',
  secondary: 'pressable hover:border-ink-subtle/40',
  ghost: 'pressable',
}

/** Classes for anything that should look like a button, including links. */
export function buttonClasses(variant: Variant = 'secondary', size: Size = 'md', className?: string) {
  return cn(
    'inline-flex items-center justify-center rounded-lg font-medium whitespace-nowrap',
    'disabled:opacity-50 disabled:cursor-not-allowed aria-disabled:opacity-50',
    motion[variant],
    variants[variant],
    sizes[size],
    className,
  )
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
}

export function Button({ variant = 'secondary', size = 'md', className, type = 'button', ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      className={buttonClasses(variant, size, className)}
      {...rest}
    />
  )
}
