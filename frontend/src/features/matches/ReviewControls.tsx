import { Lock } from 'lucide-react'
import { toast } from 'sonner'
import type { Match, MatchReviewPatch } from '../../data/types'
import { useUpdateMatchReview } from '../../data/mutations'
import { allowedEmailStatuses } from '../../data/matchRules'
import { SelectMenu } from '../../components/ui/SelectMenu'
import { cn } from '../../lib/cn'
import { formatDateTime } from '../../lib/format'
import { emailStatusText, matchLabelText, textFor } from '../../lib/labels'

// Label and e-mail status controls for one match. Used in the client table and
// on the law change detail page, so both follow the same rules.

const LABELS = ['unlabeled', 'relevant', 'irrelevant']

export const emailStatusOptions = (match: Match) => allowedEmailStatuses(match.review)

export const statusDot: Record<string, string> = { not_sent: 'bg-warning', sent: 'bg-info', discussed: 'bg-success' }
export const labelDot: Record<string, string> = { unlabeled: 'bg-warning', relevant: 'bg-success', irrelevant: 'bg-ink-subtle' }

/** Saves a review change with a toast. */
export function useMatchSave(match: Match) {
  const update = useUpdateMatchReview()
  function save(patch: MatchReviewPatch, message: string) {
    update.mutate(
      { matchId: match.id, patch },
      {
        onSuccess: () => toast.success(message),
        onError: (e) => toast.error(e.message),
      },
    )
  }
  return { save, pending: update.isPending }
}

/** `context` names the row for screen readers, for example the law title or client name. */
export function LabelMenu({ match, context, onSave, disabled }: { match: Match; context: string; onSave: (patch: MatchReviewPatch, message: string) => void; disabled?: boolean }) {
  const options = LABELS.includes(match.review.label) ? LABELS : [match.review.label, ...LABELS]
  return (
    <SelectMenu
      label={`Einstufung für ${context}`}
      value={match.review.label}
      disabled={disabled}
      onChange={(v) => onSave({ label: v }, `Einstufung: ${textFor(matchLabelText, v)}`)}
      options={options.map((l) => ({ value: l, label: textFor(matchLabelText, l), dot: labelDot[l] ?? 'bg-ink-subtle' }))}
    />
  )
}

export function StatusControl({ match, context, onSave, disabled }: { match: Match; context: string; onSave: (patch: MatchReviewPatch, message: string) => void; disabled?: boolean }) {
  const options = emailStatusOptions(match)
  if (options.length <= 1) return <LockedStatus match={match} />
  return (
    <SelectMenu
      label={`E-Mail Status für ${context}`}
      value={match.review.emailStatus}
      disabled={disabled}
      onChange={(v) => onSave({ emailStatus: v }, `Status: ${textFor(emailStatusText, v)}`)}
      options={options.map((s) => ({ value: s, label: textFor(emailStatusText, s), dot: statusDot[s] ?? 'bg-ink-subtle' }))}
    />
  )
}

/** Final status after an email: shown as text, no menu. */
function LockedStatus({ match }: { match: Match }) {
  const { emailStatus, sentAt, discussedAt } = match.review
  const parts = [`Per E-Mail gesendet${sentAt ? ` am ${formatDateTime(sentAt)}` : ''}.`]
  if (emailStatus === 'discussed' && discussedAt) parts.push(`Besprochen am ${formatDateTime(discussedAt)}.`)
  parts.push('Status ist festgelegt.')
  const hint = parts.join(' ')
  return (
    <span title={hint} className="flex h-9 items-center gap-2 rounded-lg bg-canvas px-2.5 text-xs font-medium text-ink">
      <span className={cn('size-2 shrink-0 rounded-full', statusDot[emailStatus] ?? 'bg-ink-subtle')} aria-hidden />
      <span className="min-w-0 flex-1 truncate">
        {textFor(emailStatusText, emailStatus)}
        <span className="sr-only"> ({hint})</span>
      </span>
      <Lock className="size-3.5 shrink-0 text-ink-subtle" aria-hidden />
    </span>
  )
}
