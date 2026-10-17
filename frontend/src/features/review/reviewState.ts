import type { FactResolution } from '../../data/adapter'
import type { Fact, Id } from '../../data/types'
import { needsDecision } from '../../data/draftRules'
import { coreValueProblem } from '../../data/coreFields'
import { fromDraft, toDraft, type FactDraft } from '../profile/factDraft'

// The advisor's choice per draft field during review. Pure logic, unit tested.
// UI.md 8 to 10: conflicts are never pre-selected, confirm stays disabled until
// every open field is resolved or deliberately left empty.

export type FactChoice =
  | { mode: 'accept' }
  | { mode: 'candidate'; candidateId: Id }
  | { mode: 'own'; draft: FactDraft }
  | { mode: 'empty' }

export type Choices = Record<Id, FactChoice>

export { needsDecision }

/** The company name is required; every other field may be left empty on purpose. */
export function canLeaveEmpty(fact: Fact): boolean {
  return fact.coreField !== 'name'
}

/** A fresh own-value draft for a fact: prefilled with its value, empty for conflicts. */
export function ownDraft(fact: Fact): FactDraft {
  const base = fact.reviewStatus === 'conflict' ? { ...fact, value: null } : fact
  const d = toDraft(base)
  // Unknown types cannot be typed in; fall back to free text.
  return d.kind === 'readonly' ? { kind: 'text', text: '' } : d
}

export type ChoiceResult = { ok: true; resolution: FactResolution | null } | { ok: false; error: string } | { ok: 'open' }

/** What a choice means for one fact. `resolution: null` = keep the extracted value as is. */
export function resolve(fact: Fact, choice: FactChoice | undefined): ChoiceResult {
  if (!choice) return needsDecision(fact) ? { ok: 'open' } : { ok: true, resolution: null }
  switch (choice.mode) {
    case 'accept':
      if (fact.reviewStatus === 'conflict' || fact.reviewStatus === 'missing') return { ok: 'open' }
      return { ok: true, resolution: needsDecision(fact) ? { kind: 'value', value: fact.value } : null }
    case 'candidate': {
      const c = fact.candidates?.find((x) => x.id === choice.candidateId)
      if (!c) return { ok: 'open' }
      return { ok: true, resolution: { kind: 'value', value: c.value, candidateId: c.id } }
    }
    case 'own': {
      const parsed = fromDraft(fact, choice.draft)
      if (!parsed.ok) return { ok: false, error: parsed.error }
      const empty = parsed.value === null || (Array.isArray(parsed.value) && parsed.value.length === 0)
      if (empty) return { ok: false, error: 'Bitte einen Wert eingeben oder das Feld bewusst leer lassen.' }
      const problem = coreValueProblem(fact.coreField, parsed.value)
      if (problem) return { ok: false, error: problem }
      return { ok: true, resolution: { kind: 'value', value: parsed.value } }
    }
    case 'empty':
      if (!canLeaveEmpty(fact)) return { ok: 'open' }
      return { ok: true, resolution: { kind: 'left_empty' } }
  }
}

export interface ReviewSummary {
  /** Fields that still need a decision. */
  open: Id[]
  /** Fields with an invalid own value. */
  errors: Record<Id, string>
  /** What to send to confirmProfile. */
  resolutions: Record<Id, FactResolution>
  canConfirm: boolean
}

export function summarize(facts: Fact[], choices: Choices): ReviewSummary {
  const open: Id[] = []
  const errors: Record<Id, string> = {}
  const resolutions: Record<Id, FactResolution> = {}
  for (const f of facts) {
    const r = resolve(f, choices[f.id])
    if (r.ok === 'open') open.push(f.id)
    else if (r.ok === false) errors[f.id] = r.error
    else if (r.resolution) resolutions[f.id] = r.resolution
  }
  return { open, errors, resolutions, canConfirm: open.length === 0 && Object.keys(errors).length === 0 }
}
