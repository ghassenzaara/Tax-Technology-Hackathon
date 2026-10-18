import type { DataAdapter } from './adapter'
import { MockAdapter } from './mock/mockAdapter'
import { HttpAdapter } from './http/httpAdapter'

// The one line to change when the HTTP adapter exists.
export const adapter: DataAdapter = import.meta.env.VITE_DATA_SOURCE === 'http'
  ? new HttpAdapter(import.meta.env.VITE_API_URL ?? '')
  : new MockAdapter()

export const isMockData = import.meta.env.VITE_DATA_SOURCE !== 'http'

export * from './adapter'
export type * from './types'
