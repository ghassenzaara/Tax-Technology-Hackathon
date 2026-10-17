import type { Match, MatchReview } from './types'

// Review rules for one match, shared by the UI and the adapter.

const EMAIL_STATUSES = ['not_sent', 'sent', 'discussed']

/**
 * True when an email was sent from the app (an outreach record exists) and the
 * status still reflects it. A row on "Nicht gesendet" is never treated as sent.
 */
export function sentByEmail(review: MatchReview): boolean {
  return !!review.outreachId && (review.emailStatus === 'sent' || review.emailStatus === 'discussed')
}

/**
 * E-Mail statuses the advisor may pick by hand.
 * After an email was sent from the app the status only moves forward:
 * Gesendet to Besprochen, and Besprochen stays. Otherwise all statuses are free.
 * An unknown status stays visible.
 */
export function allowedEmailStatuses(review: MatchReview): string[] {
  if (sentByEmail(review)) return review.emailStatus === 'sent' ? ['sent', 'discussed'] : ['discussed']
  return EMAIL_STATUSES.includes(review.emailStatus) ? EMAIL_STATUSES : [review.emailStatus, ...EMAIL_STATUSES]
}

/** Newest law change first; matches labeled irrelevant go to the end. */
export function sortForCompany(matches: Match[], publishedAt: (lawChangeId: string) => string): Match[] {
  const irrelevant = (m: Match) => (m.review.label === 'irrelevant' ? 1 : 0)
  return [...matches].sort(
    (a, b) => irrelevant(a) - irrelevant(b) || publishedAt(b.lawChangeId).localeCompare(publishedAt(a.lawChangeId)),
  )
}
