import type { Tone } from '../components/ui/Badge'

// German display labels for open vocabularies. Unknown values fall back to the raw text.

export const matchLabelText: Record<string, string> = {
  unlabeled: 'Nicht eingestuft',
  relevant: 'Relevant',
  irrelevant: 'Nicht relevant',
}

export const emailStatusText: Record<string, string> = {
  not_sent: 'Nicht gesendet',
  sent: 'Gesendet',
  discussed: 'Besprochen',
}

export const companyStatusText: Record<string, string> = {
  draft: 'Entwurf',
  active: 'Aktiv',
  archived: 'Archiviert',
}

export const reviewStatusText: Record<string, string> = {
  confirmed: 'Bestätigt',
  single_source: 'Eine Quelle',
  conflict: 'Konflikt',
  missing: 'Nicht gefunden',
  advisor_entered: 'Manuell eingetragen',
}

export const sourceTypeText: Record<string, string> = {
  gesetzentwurf: 'Gesetzentwurf',
  bmf_schreiben: 'BMF-Schreiben',
  gesetz: 'Gesetz',
  urteil: 'Urteil',
}

export const coreFieldText: Record<string, string> = {
  name: 'Name',
  legalForm: 'Rechtsform',
  industry: 'Branche',
  customerNumber: 'Kundennummer',
  clientSince: 'Mandant seit',
  contact: 'Ansprechpartner',
  'contact.email': 'E-Mail',
  label: 'Einstufung',
  emailStatus: 'E-Mail Status',
  notes: 'Notiz',
}

export function textFor(map: Record<string, string>, value: string | undefined | null): string {
  if (value === undefined || value === null || value === '') return ''
  return map[value] ?? value
}

export const matchLabelTone: Record<string, Tone> = { relevant: 'success', irrelevant: 'neutral', unlabeled: 'warning' }
export const emailStatusTone: Record<string, Tone> = { not_sent: 'warning', sent: 'info', discussed: 'success' }
export const companyStatusTone: Record<string, Tone> = { active: 'success', draft: 'warning', archived: 'neutral' }

export function toneFor(map: Record<string, Tone>, value: string | undefined): Tone {
  return (value && map[value]) || 'neutral'
}

/** Thresholds from the brief: hoch from 0.8, mittel from 0.5. */
export function confidenceLevel(confidence: number): { text: string; tone: Tone } {
  if (confidence >= 0.8) return { text: 'hoch', tone: 'success' }
  if (confidence >= 0.5) return { text: 'mittel', tone: 'warning' }
  return { text: 'niedrig', tone: 'neutral' }
}

// Document processing statuses, UI.md 12. Unknown values render as text.
export const parsingStatusText: Record<string, string> = {
  pending: 'Wartet',
  ok: 'Gelesen',
  partial: 'Teilweise gelesen',
  failed: 'Fehlgeschlagen',
  unsupported: 'Nicht unterstützt',
}
export const extractionStatusText: Record<string, string> = {
  pending: 'Wartet',
  ok: 'Ausgewertet',
  partial: 'Teilweise ausgewertet',
  failed: 'Fehlgeschlagen',
  skipped: 'Übersprungen',
  write_failed: 'Speichern fehlgeschlagen',
}
export const statusTone: Record<string, Tone> = {
  pending: 'neutral',
  ok: 'success',
  partial: 'warning',
  failed: 'danger',
  unsupported: 'neutral',
  skipped: 'neutral',
  write_failed: 'danger',
}
export const fileTypeText: Record<string, string> = {
  text_pdf: 'PDF',
  scanned_pdf: 'PDF (Scan)',
  excel: 'Excel',
  unsupported: 'Nicht unterstützt',
}
