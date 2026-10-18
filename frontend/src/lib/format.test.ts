import { describe, expect, it } from 'vitest'
import { formatDate, formatNumber, formatScalar } from './format'
import { textFor, emailStatusText } from './labels'

const nbsp = (s: string) => s.replace(/ /g, ' ')

describe('formatting', () => {
  it('German numbers and currency', () => {
    expect(formatNumber(1234.5)).toBe('1.234,5')
    expect(nbsp(formatNumber(31750000.5, 'EUR'))).toBe('31.750.000,50 €')
    expect(formatNumber(470, '%')).toBe('470 %')
    expect(formatNumber(54.3233)).toBe('54,3233')
    expect(formatNumber(2025, undefined, 'berichtsjahr')).toBe('2025')
    expect(formatNumber(1840, undefined, 'co2Tonnen')).toBe('1.840')
  })

  it('dates without time zone shift', () => {
    expect(formatDate('2023-12-31')).toBe('31.12.2023')
    expect(formatDate('kein Datum')).toBe('kein Datum')
  })

  it('scalars of every type, unknown ones as text', () => {
    expect(formatScalar(true)).toBe('Ja')
    expect(formatScalar(false)).toBe('Nein')
    expect(formatScalar(null)).toBe('')
    expect(formatScalar('2027-03-31', 'date')).toBe('31.03.2027')
    expect(formatScalar(['a', 'b'], 'list')).toBe('a, b')
    expect(formatScalar({ lat: 1 }, 'geo_point')).toBe('lat: 1')
    expect(formatScalar('<b>x</b>')).toBe('<b>x</b>')
  })

  it('displays German vocabulary while leaving prose and unknown codes intact', () => {
    expect(formatScalar('natuerliche_person', 'enum')).toBe('Natürliche Person')
    expect(formatScalar('anhaengig')).toBe('Anhängig')
    expect(formatScalar('aussenpruefung_171_4')).toBe('Außenprüfung (§ 171 Abs. 4 AO)')
    expect(formatScalar('Unveränderter Text mit Steuer und Quelle')).toBe('Unveränderter Text mit Steuer und Quelle')
    expect(formatScalar('new_backend_code')).toBe('new_backend_code')
    expect(formatScalar([{ Art: 'natuerliche_person', Ende: null }])).toBe('Art: Natürliche Person')
  })

  it('unknown vocabulary values fall back to raw text', () => {
    expect(textFor(emailStatusText, 'sent')).toBe('Gesendet')
    expect(textFor(emailStatusText, 'bounced')).toBe('bounced')
  })
})
