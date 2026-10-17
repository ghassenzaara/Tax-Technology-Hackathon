import { CheckCircle2, Sparkles } from 'lucide-react'
import type { LawBlock } from '../../data/types'
import { Card, CardHeader } from '../../components/ui/Card'
import { cn } from '../../lib/cn'

// Renders whatever blocks the screening loop delivers. Known kinds get their own
// look; unknown kinds render as a plain card with title, text and items.

const kindStyle: Record<string, string> = {
  next_steps: 'border-success/25 bg-success-soft/40',
  impact: 'border-warning/25 bg-warning-soft/30',
}

export function BlockRenderer({ block }: { block: LawBlock }) {
  const checklist = block.kind === 'next_steps'
  return (
    <Card className={cn(kindStyle[block.kind])}>
      <CardHeader title={block.title} />
      <div className="space-y-3 px-5 pb-5 text-sm leading-relaxed">
        {block.text && <p className="text-ink">{block.text}</p>}
        {block.items && block.items.length > 0 && (
          <ul className={checklist ? 'space-y-2' : 'list-disc space-y-1 pl-5 marker:text-primary'}>
            {block.items.map((item, i) =>
              checklist ? (
                <li key={i} className="flex items-start gap-2">
                  <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" aria-hidden />
                  <span>{item}</span>
                </li>
              ) : (
                <li key={i}>{item}</li>
              ),
            )}
          </ul>
        )}
        {block.generated && (
          <p className="flex items-center gap-1.5 text-xs text-ink-subtle">
            <Sparkles className="size-3.5" aria-hidden />
            Automatisch erstellt. Bitte mit der Quelle abgleichen.
          </p>
        )}
      </div>
    </Card>
  )
}
