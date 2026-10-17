import type { Fact } from './types'

/** Conflicts, missing values and values flagged for review need an explicit decision (UI.md 9). */
export function needsDecision(fact: Fact): boolean {
  return fact.reviewStatus === 'conflict' || fact.reviewStatus === 'missing' || fact.needsReview === true
}
