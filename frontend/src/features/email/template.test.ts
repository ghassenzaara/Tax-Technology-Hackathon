import { describe, expect, it } from 'vitest'
import { buildGreeting, fillTemplate } from './template'

describe('greeting', () => {
  it.each([
    [{ salutation: 'Herr', name: 'Max Mustermann' }, 'Sehr geehrter Herr Mustermann'],
    [{ salutation: 'Frau', name: 'Anna Müller' }, 'Sehr geehrte Frau Müller'],
    [{ salutation: 'Frau', name: 'Dr. Eva Weber' }, 'Sehr geehrte Frau Dr. Weber'],
    [{ name: 'Dr. Kim Larsen' }, 'Guten Tag Dr. Kim Larsen'],
    [{ salutation: 'Divers', name: 'Alex Kim' }, 'Guten Tag Alex Kim'],
  ])('%o', (contact, expected) => {
    expect(buildGreeting(contact)).toBe(expected)
  })
})

describe('template', () => {
  it('is the same text for every client apart from greeting and signature', () => {
    const advisor = { id: 'a', name: 'Max Schmidt', title: 'Steuerberater' }
    const a = fillTemplate({ salutation: 'Herr', name: 'Max Mustermann' }, advisor)
    const b = fillTemplate({ salutation: 'Frau', name: 'Anna Müller' }, advisor)
    expect(a.subject).toBe(b.subject)
    expect(a.body.split('\n').slice(1)).toEqual(b.body.split('\n').slice(1))
    expect(a.body).not.toMatch(/§|BMF|BFH|Urteil|gesetz/i)
  })
})
