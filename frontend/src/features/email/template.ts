import type { Advisor, Contact } from '../../data/types'
import type { EmailTemplate } from '../../data/adapter'

// One template for every client, editable only in Einstellungen. Only the
// greeting (with the contact name) and the advisor signature are filled in.
// No law names, no details, no LLM.

export const PLACEHOLDER_GREETING = '{Anrede}'
export const PLACEHOLDER_SIGNATURE = '{Signatur}'
export const PLACEHOLDERS = [PLACEHOLDER_GREETING, PLACEHOLDER_SIGNATURE]

export const TEMPLATE_SUBJECT = 'Gesprächsangebot zu aktuellen regulatorischen Entwicklungen'

const TEMPLATE_BODY = `{Anrede},

wir verfolgen für unsere Mandanten laufend die aktuellen regulatorischen Entwicklungen. Dabei sind uns Änderungen aufgefallen, die für Ihr Unternehmen von Bedeutung sein könnten.

Gerne besprechen wir mit Ihnen in einem persönlichen Gespräch, ob und wie Ihr Unternehmen betroffen ist. Bitte teilen Sie uns mit, wann ein Termin für Sie passt.

Mit freundlichen Grüßen
{Signatur}`

/** The template with placeholders, as stored. */
export const DEFAULT_TEMPLATE: EmailTemplate = { subject: TEMPLATE_SUBJECT, body: TEMPLATE_BODY }

const ACADEMIC_TITLES = /^(dr\.|prof\.|dipl\.-[\wäöü]+\.?)$/i

export function buildGreeting(contact: Contact): string {
  const parts = contact.name.trim().split(/\s+/).filter(Boolean)
  const titles = parts.filter((p) => ACADEMIC_TITLES.test(p))
  const lastName = parts[parts.length - 1] ?? ''
  const salutation = contact.salutation?.trim().toLowerCase()
  const nameWithTitle = [...titles, lastName].join(' ')
  if (salutation === 'herr') return `Sehr geehrter Herr ${nameWithTitle}`
  if (salutation === 'frau') return `Sehr geehrte Frau ${nameWithTitle}`
  return `Guten Tag ${contact.name.trim()}`
}

export function buildSignature(advisor: Advisor): string {
  return [advisor.name, advisor.title, advisor.firm].filter(Boolean).join('\n')
}

function fill(text: string, contact: Contact, advisor: Advisor) {
  return text.replaceAll(PLACEHOLDER_GREETING, buildGreeting(contact)).replaceAll(PLACEHOLDER_SIGNATURE, buildSignature(advisor))
}

export function fillTemplate(contact: Contact, advisor: Advisor, source: EmailTemplate = DEFAULT_TEMPLATE): EmailTemplate {
  return { subject: fill(source.subject, contact, advisor), body: fill(source.body, contact, advisor) }
}

/** Placeholders like {Name} that are not supported. */
export function unknownPlaceholders(text: string): string[] {
  return [...new Set(text.match(/\{[^{}\n]*\}/g) ?? [])].filter((p) => !PLACEHOLDERS.includes(p))
}
