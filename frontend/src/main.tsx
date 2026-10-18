import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from 'react-router-dom'
import { Toaster } from 'sonner'
import '@fontsource-variable/inter'
import './index.css'
import { router } from './app/router'
import { AdapterError } from './data/adapter'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Adapter errors (not found, validation) are final; only retry unexpected failures once.
      retry: (count, error) => !(error instanceof AdapterError) && count < 1,
      refetchOnWindowFocus: false,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster position="top-right" richColors closeButton />
    </QueryClientProvider>
  </StrictMode>,
)
