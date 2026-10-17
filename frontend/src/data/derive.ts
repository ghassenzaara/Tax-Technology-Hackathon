import type { Advisor, Id, Match } from './types'

// Counter definitions from PLANNING.md section 3. Pure functions, unit tested.

/** Law changes with at least one match for the advisor's clients that the advisor has not opened. */
export function unreadLawChangeIds(matches: Match[], advisor: Advisor): Id[] {
  const seen = new Set(advisor.seenLawChangeIds ?? [])
  return [...new Set(matches.map((m) => m.lawChangeId))].filter((id) => !seen.has(id))
}

/** Unlabeled matches, plus relevant matches not yet sent. */
export function isOpenTask(m: Match): boolean {
  return m.review.label === 'unlabeled' || (m.review.label === 'relevant' && m.review.emailStatus === 'not_sent')
}

/** "Relevanz für N Mandanten": matches of a law not labeled irrelevant. */
export function relevantCompanyCount(matches: Match[], lawChangeId: Id): number {
  return new Set(matches.filter((m) => m.lawChangeId === lawChangeId && m.review.label !== 'irrelevant').map((m) => m.companyId)).size
}

/** "Relevante Updates" per client: not labeled irrelevant and not discussed. */
export function relevantUpdateCount(matches: Match[], companyId: Id): number {
  return matches.filter((m) => m.companyId === companyId && m.review.label !== 'irrelevant' && m.review.emailStatus !== 'discussed').length
}

/** Companies created in the same calendar month as `now` (Europe/Berlin is close enough to UTC for months). */
export function newThisMonth(createdAt: string[], now: Date): number {
  const ym = now.toISOString().slice(0, 7)
  return createdAt.filter((d) => d.slice(0, 7) === ym).length
}

/** Greeting by local time: Guten Morgen until 11, Guten Tag until 18, then Guten Abend. */
export function greetingFor(now: Date): string {
  // formatToParts: the German format adds " Uhr", so only the hour part is read.
  const parts = new Intl.DateTimeFormat('de-DE', { hour: 'numeric', hourCycle: 'h23', timeZone: 'Europe/Berlin' }).formatToParts(now)
  const hour = Number(parts.find((p) => p.type === 'hour')?.value ?? 12)
  if (hour < 11) return 'Guten Morgen'
  if (hour < 18) return 'Guten Tag'
  return 'Guten Abend'
}
