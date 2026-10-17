import { describe, expect, it } from 'vitest'
import { loadSeed } from '../../data/mock/seed'
import { buildDynamicTerms, findLawTerms, uniqueTerms } from './lawTermCheck'
import { fillTemplate } from './template'

const seed = loadSeed()
const advisor = seed.advisors[0]
const lawsFor = (companyId: string) => {
  const ids = new Set(seed.matches.filter((m) => m.companyId === companyId).map((m) => m.lawChangeId))
  return seed.lawChanges.filter((l) => ids.has(l.id))
}
const termsFor = (companyId: string) => {
  const c = seed.companies.find((x) => x.id === companyId)!
  const t = fillTemplate(c.contact, advisor)
  return buildDynamicTerms(lawsFor(companyId), [c.name, c.contact.name, t.subject, t.body])
}
const found = (text: string, terms: string[] = []) => uniqueTerms(findLawTerms(text, terms))

describe('law term check', () => {
  it.each(seed.companies.map((c) => [c.name, c.id]))('the plain template passes for %s', (_, id) => {
    const c = seed.companies.find((x) => x.id === id)!
    const t = fillTemplate(c.contact, advisor)
    expect(found(`${t.subject}\n${t.body}`, termsFor(id))).toEqual([])
  })

  it.each([
    ['Es geht um § 13b UStG.', ['§', 'UStG']],
    ['Laut BMF-Schreiben vom Oktober', ['BMF']],
    ['Das BFH Urteil V R 99/25 ist wichtig.', ['BFH', 'Urteil', 'V R 99/25']],
    ['Nach Art. 3 und Abs. 2 gilt', ['Art. 3', 'Abs. 2']],
    ['Das Jahressteuergesetz 2026 kommt.', ['Jahressteuergesetz']],
    ['Der Gesetzentwurf liegt vor.', ['Gesetzentwurf']],
    ['eine neue richtlinie der EU', ['richtlinie']],
  ])('blocks fixed terms: %s', (text, expected) => {
    expect(found(text)).toEqual(expected)
  })

  it('blocks keywords of the client matched law changes', () => {
    const terms = termsFor('cmp-2b9c44')
    expect(found('Es geht um Ihre Organschaft und die Zinsschranke.', terms)).toEqual(['Organschaft', 'Zinsschranke'])
    expect(found('Die Umsatzsteuer ändert sich.', termsFor('cmp-7f3a01'))).toEqual(['Umsatzsteuer'])
  })

  it('does not trigger on the client name, the contact, Steuerberater or generic words', () => {
    const terms = termsFor('cmp-c81d02')
    expect(found('Guten Tag Herr Schneider von der Schneider Bau GmbH & Co. KG, Ihr Steuerberater. Bitte schreiben Sie uns.', terms)).toEqual([])
  })

  it('reports positions for highlighting', () => {
    expect(findLawTerms('Hallo § 4')).toEqual([{ term: '§', index: 6, length: 1 }])
  })
})
