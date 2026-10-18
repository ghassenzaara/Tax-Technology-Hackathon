import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { FactValue } from './FactValue'

describe('readable extracted values', () => {
  it('renders a record as labeled details with umlauts and no empty-cell clutter', () => {
    const html = renderToStaticMarkup(<FactValue value={[{ Art: 'natuerliche_person', Ort: null, Aktiv: false, Quote: 0 }]} />)
    const container = document.createElement('div')
    container.innerHTML = html
    expect(container.querySelector('dl')).not.toBeNull()
    expect(container.querySelector('table')).toBeNull()
    expect(container.textContent).toContain('Natürliche Person')
    expect(container.textContent).not.toContain('Ort')
    expect(container.textContent).toContain('Nein')
    expect(container.textContent).toContain('0')
    expect(container.textContent).not.toMatch(/[{}\[\]"]|natuerliche_person/)
  })

  it('keeps multirow data in a table but omits entirely empty columns', () => {
    const container = document.createElement('div')
    container.innerHTML = renderToStaticMarkup(<FactValue value={[{ Jahr: 2024, Wert: 0, Leer: null }, { Jahr: 2025, Wert: null, Leer: '' }]} />)
    expect(container.querySelectorAll('th')).toHaveLength(2)
    expect(container.textContent).not.toContain('Leer')
    expect(container.textContent).toContain('2024')
    expect(container.textContent).not.toContain('2.024')
  })

  it('renders document content as plain text, including mixed nested lists', () => {
    const container = document.createElement('div')
    container.innerHTML = renderToStaticMarkup(<FactValue value={['<script>bad()</script>', { Notiz: 'Quelle' }]} />)
    expect(container.querySelector('script')).toBeNull()
    expect(container.textContent).toContain('<script>bad()</script>')
    expect(container.textContent).toContain('Quelle')
  })
})
