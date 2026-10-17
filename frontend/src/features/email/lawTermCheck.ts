import type { Advisor, Company, LawChange } from '../../data/types'
import {
  DEFAULT_TEMPLATE,
  PLACEHOLDERS,
  PLACEHOLDER_GREETING,
  PLACEHOLDER_SIGNATURE,
  buildSignature,
  fillTemplate,
  unknownPlaceholders,
} from './template'

// Blocks an email that names laws or law details. The email must only offer a
// talk (CLAUDE.md). Pure functions, used by the dialog and by the adapter send.

export interface TermHit {
  term: string
  index: number
  length: number
}

const L = '\\p{L}' // any letter, including umlauts

/** Fixed patterns. Case insensitive, on word boundaries. */
const FIXED_PATTERNS: RegExp[] = [
  /§+/gu,
  new RegExp(`(?<!${L})(?:Abs|Art|Nr|Rn|Tz)\\.\\s*\\d+`, 'giu'),
  new RegExp(
    `(?<!${L})(?:BMF|BFH|EuGH|BVerfG|BGBl|BStBl|BT-Drs|Urteil|Urteile|Urteils|Beschluss|Beschlusses|Aktenzeichen|Erlass|Erlasses|Richtlinie|Richtlinien|Verordnung|Verordnungen|UStG|EStG|KStG|GewStG|AO|AStG|UmwStG|GrStG|ErbStG|UStAE|EStR)(?!${L})`,
    'giu',
  ),
  // Aktenzeichen like "V R 99/25" or "I R 0/25".
  /\b[IVXL]{1,4}\s+[A-Z]{1,3}(?:\s+[A-Z]{1,2})?\s+\d{1,4}\/\d{2}\b/gu,
  // Any word containing "gesetz": Jahressteuergesetz, Gesetzentwurf, Gesetzes.
  new RegExp(`(?<!${L})${L}*gesetz${L}*(?!${L})`, 'giu'),
]

/** Generic words that say nothing about a specific law. */
const STOP_WORDS = new Set(
  `der die das den dem des ein eine einer eines einem und oder aber bei ab für fuer von vom zu zur zum im in mit auf über ueber
   unter aus nach vor bis durch gegen ohne um an als auch nicht neue neuer neues neuen änderung änderungen anpassung anpassungen
   neuregelung neuregelungen entwurf regelung regelungen regeln pflicht pflichten verkehr fragen hinweise ihre ihr wir sie
   unternehmen mandanten`
    .split(/\s+/)
    .filter(Boolean),
)

const tokenize = (s: string) => s.split(new RegExp(`[^${L}\\d-]+`, 'u')).filter(Boolean)

/**
 * Words from the client's matched law changes: title words, tax types and norms.
 * Words that also appear in `allowedText` (client name, contact, signature, the
 * fixed template) are dropped, so those never trigger the check.
 */
export function buildDynamicTerms(laws: LawChange[], allowedText: string[] = []): string[] {
  const allowed = new Set(allowedText.flatMap((t) => tokenize(t.toLowerCase())))
  const words = laws.flatMap((l) => [
    ...tokenize(l.title),
    ...(l.tags?.taxTypes ?? []).flatMap(tokenize),
    ...(l.tags?.norms ?? []).flatMap(tokenize),
  ])
  const terms = new Set<string>()
  for (const w of words) {
    const lower = w.toLowerCase()
    if (lower.length < 4 || /^\d+$/.test(lower) || STOP_WORDS.has(lower) || allowed.has(lower)) continue
    terms.add(lower)
  }
  return [...terms]
}

function escapeRegExp(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** All hits in the text, sorted by position, overlaps removed. */
export function findLawTerms(text: string, dynamicTerms: string[] = []): TermHit[] {
  const patterns = [...FIXED_PATTERNS]
  if (dynamicTerms.length > 0) {
    const alternatives = dynamicTerms.map(escapeRegExp).join('|')
    patterns.push(new RegExp(`(?<!${L})(?:${alternatives})(?!${L})`, 'giu'))
  }
  const hits: TermHit[] = []
  for (const pattern of patterns) {
    for (const m of text.matchAll(pattern)) {
      if (m[0].trim()) hits.push({ term: m[0], index: m.index ?? 0, length: m[0].length })
    }
  }
  hits.sort((a, b) => a.index - b.index || b.length - a.length)
  const merged: TermHit[] = []
  for (const h of hits) {
    const last = merged[merged.length - 1]
    if (last && h.index < last.index + last.length) continue
    merged.push(h)
  }
  return merged
}

/** Distinct found terms for the error message, in order of appearance. */
export function uniqueTerms(hits: TermHit[]): string[] {
  const seen = new Map<string, string>()
  for (const h of hits) if (!seen.has(h.term.toLowerCase())) seen.set(h.term.toLowerCase(), h.term)
  return [...seen.values()]
}

/**
 * Dynamic terms for one client: words of all its matched law changes, minus
 * the client name, the contact, the advisor signature and the fixed template.
 */
export function termsForClient(company: Company, matchedLaws: LawChange[], advisor: Advisor): string[] {
  const t = fillTemplate(company.contact, advisor)
  return buildDynamicTerms(matchedLaws, [company.name, company.contact.name, buildSignature(advisor), t.subject, t.body])
}

/** Hits in subject and body together. */
export function checkEmail(subject: string, body: string, terms: string[]): TermHit[] {
  return findLawTerms(`${subject}\n${body}`, terms)
}

/**
 * Problems that block saving a template. The template applies to every client,
 * so it is checked against the fixed list and the words of all known law changes.
 */
export function templateProblems(subject: string, body: string, allLaws: LawChange[]): { messages: string[]; hits: TermHit[] } {
  const messages: string[] = []
  if (!subject.trim()) messages.push('Der Betreff darf nicht leer sein.')
  if (!body.trim()) messages.push('Der Text darf nicht leer sein.')
  if (!body.includes(PLACEHOLDER_GREETING)) messages.push(`Der Text muss den Platzhalter ${PLACEHOLDER_GREETING} enthalten.`)
  if (!body.includes(PLACEHOLDER_SIGNATURE)) messages.push(`Der Text muss den Platzhalter ${PLACEHOLDER_SIGNATURE} enthalten.`)
  const unknown = unknownPlaceholders(`${subject}\n${body}`)
  if (unknown.length > 0) messages.push(`Unbekannte Platzhalter: ${unknown.join(', ')}`)
  const strip = (t: string) => PLACEHOLDERS.reduce((s, p) => s.replaceAll(p, ' '.repeat(p.length)), t)
  const terms = buildDynamicTerms(allLaws, [DEFAULT_TEMPLATE.subject, DEFAULT_TEMPLATE.body])
  const hits = checkEmail(strip(subject), strip(body), terms)
  if (hits.length > 0) messages.push(`Die Vorlage enthält gesetzesbezogene Begriffe: ${uniqueTerms(hits).join(', ')}`)
  return { messages, hits }
}
