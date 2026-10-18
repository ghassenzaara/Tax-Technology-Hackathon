import { describe, expect, it, vi } from 'vitest'
import { HttpAdapter } from './httpAdapter'
import type { Company } from '../types'

const company = { id: 'Mandant ä', extra: { profileVersion: 3 } } as unknown as Company
const response = (body: unknown, status = 200) => new Response(status === 204 ? null : JSON.stringify(body), { status })

describe('HTTP adapter', () => {
  it('encodes opaque ids and remembers the version actually read', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response(company)).mockResolvedValueOnce(response(company))
    const adapter = new HttpAdapter('http://localhost:8000/', fetcher)
    await adapter.getCompany(company.id)
    await adapter.updateCompany(company.id, { core: { name: 'Neu' } })
    expect(fetcher.mock.calls[0][0]).toBe('http://localhost:8000/api/ui/companies/Mandant%20%C3%A4')
    expect(fetcher.mock.calls[0][1]?.cache).toBe('no-store')
    expect(JSON.parse(fetcher.mock.calls[1][1]!.body as string)).toEqual({ core: { name: 'Neu' }, profileVersion: 3 })
  })

  it('uses the editor version even after another read refreshes the cache', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(company))
    const adapter = new HttpAdapter('', fetcher)
    await adapter.updateCompany(company.id, { profileVersion: 1, core: { name: 'Neu' } })
    expect(JSON.parse(fetcher.mock.calls[0][1]!.body as string).profileVersion).toBe(1)
  })

  it('requires a profile read before an update', async () => {
    const fetcher = vi.fn<typeof fetch>()
    await expect(new HttpAdapter('', fetcher).updateCompany('x', {})).rejects.toMatchObject({ code: 'stale_profile' })
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('returns backend codes and field details without replaying mutations', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ code: 'send_pending', message: 'Versand unklar', details: [{ field: 'recipient', message: 'Bitte prüfen' }] }, 409))
    const adapter = new HttpAdapter('', fetcher)
    await expect(adapter.sendClientEmail({ companyId: 'c', matchIds: ['m'], recipient: 'x@example.test', subject: 'Test', body: 'Text' })).rejects.toMatchObject({ code: 'send_pending', message: 'Versand unklar recipient: Bitte prüfen' })
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(JSON.parse(fetcher.mock.calls[0][1]!.body as string).confirmed).toBe(true)
  })

  it('sends files and relative paths in multipart form', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ id: 'run' }, 202))
    const file = new File(['text'], 'source.pdf', { type: 'application/pdf' })
    await new HttpAdapter('', fetcher).uploadFiles('c', [{ relativePath: 'DD/source.pdf', sizeBytes: 4, file }])
    const request = fetcher.mock.calls[0][1]!
    const form = request.body as FormData
    expect(form.get('paths')).toBe('DD/source.pdf')
    expect(form.get('companyId')).toBe('c')
    expect(form.get('files')).toBeInstanceOf(File)
    expect(request.headers).toBeUndefined()
  })

  it('rejects mock-only file metadata before making a request', async () => {
    const fetcher = vi.fn<typeof fetch>()
    await expect(new HttpAdapter('', fetcher).uploadFiles(null, [{ relativePath: 'a.pdf', sizeBytes: 1 }])).rejects.toMatchObject({ code: 'validation_error' })
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('sends the reviewed draft version', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ companyId: 'c', draftVersion: 'hash:2', facts: [] })).mockResolvedValueOnce(response(company))
    const adapter = new HttpAdapter('', fetcher)
    await adapter.getProfileDraft('c')
    await adapter.confirmProfile('c', { name: { kind: 'value', value: 'Firma' } })
    expect(JSON.parse(fetcher.mock.calls[1][1]!.body as string).draftVersion).toBe('hash:2')
  })

  it('handles 204 and a recovery response with no job', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response(null, 204)).mockResolvedValueOnce(response(null))
    const adapter = new HttpAdapter('', fetcher)
    await expect(adapter.markLawChangeSeen('1')).resolves.toBeUndefined()
    await expect(adapter.recoverCompany('c')).resolves.toBeNull()
  })

  it('encodes match filters and uses explicit job retry', async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => response([]))
    const adapter = new HttpAdapter('', fetcher)
    await adapter.listMatches({ companyId: 'ä & b' })
    expect(fetcher.mock.calls[0][0]).toBe('/api/ui/matches?companyId=%C3%A4+%26+b')
    await adapter.retryJob('job 1')
    expect(fetcher.mock.calls[1][0]).toBe('/api/ui/jobs/job%201/retry')
    expect(fetcher.mock.calls[1][1]?.method).toBe('POST')
  })

  it('reports network and malformed response errors', async () => {
    const offline = vi.fn<typeof fetch>().mockRejectedValue(new Error('offline'))
    await expect(new HttpAdapter('', offline).listCompanies()).rejects.toMatchObject({ code: 'network_error' })
    const malformed = vi.fn<typeof fetch>().mockResolvedValue(new Response('not json'))
    await expect(new HttpAdapter('', malformed).listCompanies()).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
