import { Link, isRouteErrorResponse, useRouteError } from 'react-router-dom'
import { Card } from '../components/ui/Card'
import { ErrorState } from '../components/ui/States'

export function NotFoundPage() {
  return (
    <Card className="px-5 py-10 text-center">
      <p className="text-sm text-ink-muted">Diese Seite gibt es nicht.</p>
      <Link to="/" className="mt-3 inline-block text-sm font-medium text-primary link-underline">
        Zum Dashboard
      </Link>
    </Card>
  )
}

export function RouteError() {
  const error = useRouteError()
  if (isRouteErrorResponse(error) && error.status === 404) return <NotFoundPage />
  return <ErrorState error={error} />
}
