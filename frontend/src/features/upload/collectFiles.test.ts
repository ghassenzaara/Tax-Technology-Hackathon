import { describe, expect, it } from 'vitest'
import { extensionOf, isSupported, mergeFiles } from './collectFiles'

describe('upload file helpers', () => {
  it.each([
    ['DD/Jahresabschluss 2025.PDF', 'pdf', true],
    ['BWA.xlsm', 'xlsm', true],
    ['Notizen/Protokoll.docx', 'docx', false],
    ['README', '', false],
  ])('%s', (path, ext, supported) => {
    expect(extensionOf(path)).toBe(ext)
    expect(isSupported(path)).toBe(supported)
  })

  it('keeps relative paths and replaces duplicates', () => {
    const a = { relativePath: 'DD/a.pdf', sizeBytes: 1 }
    const b = { relativePath: 'DD/b.pdf', sizeBytes: 2 }
    const a2 = { relativePath: 'DD/a.pdf', sizeBytes: 3 }
    expect(mergeFiles([a, b], [a2])).toEqual([a2, b])
  })
})
