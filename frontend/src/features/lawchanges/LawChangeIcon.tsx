import { FilePen, FileText, Gavel, Landmark } from 'lucide-react'
import { cn } from '../../lib/cn'

const styles: Record<string, { icon: typeof FileText; className: string }> = {
  gesetzentwurf: { icon: FilePen, className: 'bg-warning-soft text-warning' },
  bmf_schreiben: { icon: FileText, className: 'bg-info-soft text-info' },
  gesetz: { icon: Landmark, className: 'bg-danger-soft text-danger' },
  urteil: { icon: Gavel, className: 'bg-success-soft text-success' },
}

/** Icon tile per source type. Unknown types get a neutral document icon. */
export function LawChangeIcon({ sourceType, size = 'md' }: { sourceType: string; size?: 'md' | 'lg' }) {
  const s = styles[sourceType] ?? { icon: FileText, className: 'bg-neutral-soft text-ink-muted' }
  const Icon = s.icon
  return (
    <span className={cn('grid shrink-0 place-items-center rounded-lg', s.className, size === 'lg' ? 'size-14' : 'size-10')} aria-hidden>
      <Icon className={size === 'lg' ? 'size-7' : 'size-5'} />
    </span>
  )
}
