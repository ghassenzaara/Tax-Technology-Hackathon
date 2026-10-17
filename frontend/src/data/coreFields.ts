import type { Company, Fact } from './types'

// Maps draft facts with a `coreField` onto the thin company core (contract:
// fact.coreField). The UI never needs to know which fact is which.

export const CORE_FIELDS = [
  'name',
  'legalForm',
  'industry',
  'customerNumber',
  'contact.salutation',
  'contact.name',
  'contact.email',
  'contact.phone',
] as const
export type CoreField = (typeof CORE_FIELDS)[number]

export function isCoreField(value: string | undefined): value is CoreField {
  return !!value && (CORE_FIELDS as readonly string[]).includes(value)
}

/** True for facts that fill the core instead of being stored as profile facts. */
export function feedsCore(fact: Fact): boolean {
  return isCoreField(fact.coreField)
}

export function getCoreValue(company: Company, field: CoreField): string | undefined {
  if (field.startsWith('contact.')) {
    const key = field.slice('contact.'.length) as keyof Company['contact']
    return company.contact[key] || undefined
  }
  return (company[field as 'name' | 'legalForm' | 'industry' | 'customerNumber'] as string | undefined) || undefined
}

/** Sets a core value. Empty values clear optional fields. Returns the old value. */
export function setCoreValue(company: Company, field: CoreField, value: unknown): string | undefined {
  const old = getCoreValue(company, field)
  const text = value === null || value === undefined ? '' : String(value).trim()
  if (field.startsWith('contact.')) {
    const key = field.slice('contact.'.length) as keyof Company['contact']
    if (key === 'name') company.contact.name = text
    else if (text) company.contact[key] = text
    else delete company.contact[key]
  } else if (field === 'name') {
    company.name = text
  } else {
    const k = field as 'legalForm' | 'industry' | 'customerNumber'
    if (text) company[k] = text
    else delete company[k]
  }
  return old
}

export const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

/** Checks a value meant for a core field. Returns a German message or null. */
export function coreValueProblem(field: string | undefined, value: unknown): string | null {
  const text = value === null || value === undefined ? '' : String(value).trim()
  if (field === 'name' && !text) return 'Der Name des Unternehmens darf nicht leer sein.'
  if (field === 'contact.email' && text && !EMAIL_PATTERN.test(text)) return 'Die E-Mail Adresse ist ungültig.'
  return null
}
