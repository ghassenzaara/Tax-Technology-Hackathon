import type { DataAdapter } from './adapter'
import { MockAdapter } from './mock/mockAdapter'

// The one line to change when the HTTP adapter exists.
export const adapter: DataAdapter = new MockAdapter()

export const isMockData = true

export * from './adapter'
export type * from './types'
