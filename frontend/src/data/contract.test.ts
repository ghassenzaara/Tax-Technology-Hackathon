import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import Ajv2020 from 'ajv/dist/2020'
import addFormats from 'ajv-formats'

// Validates every mock file against the JSON Schemas in screening/contract,
// so the TypeScript types and the mock data cannot drift from the contract.
const contractDir = resolve(__dirname, '../../../screening/contract')
const mockDir = resolve(__dirname, '../mock-data')

const ajv = new Ajv2020({ allErrors: true, strict: false })
addFormats(ajv)
for (const file of readdirSync(contractDir).filter((f) => f.endsWith('.schema.json'))) {
  ajv.addSchema(JSON.parse(readFileSync(join(contractDir, file), 'utf-8')))
}

const read = (file: string) => JSON.parse(readFileSync(join(mockDir, file), 'utf-8'))

function expectValid(schemaId: string, data: unknown) {
  const validate = ajv.getSchema(schemaId)
  expect(validate, `schema ${schemaId} loaded`).toBeTypeOf('function')
  const ok = validate!(data)
  expect(ok ? [] : ajv.errorsText(validate!.errors, { separator: '\n' }).split('\n')).toEqual([])
}

const collections: [string, string][] = [
  ['advisors.json', 'advisor.schema.json'],
  ['companies.json', 'company.schema.json'],
  ['lawChanges.json', 'law_change.schema.json'],
  ['matches.json', 'match.schema.json'],
  ['outreach.json', 'outreach.schema.json'],
  ['auditLog.json', 'audit_entry.schema.json'],
]

describe('mock data follows the contract', () => {
  it.each(collections)('%s', (file, schemaId) => {
    const data = read(file)
    expectValid('collection.schema.json', data)
    for (const item of data.items) expectValid(schemaId, item)
  })

  it('uploadRun.json', () => expectValid('upload_run.schema.json', read('uploadRun.json')))
  it('profileDraft.json', () => expectValid('profile_draft.schema.json', read('profileDraft.json')))

  it('rejects a match without assessment', () => {
    const validate = ajv.getSchema('match.schema.json')!
    expect(validate({ id: 'x', companyId: 'c', lawChangeId: 'l', review: { label: 'relevant', emailStatus: 'sent' } })).toBe(false)
  })
})
