import { createBrowserRouter } from 'react-router-dom'
import { Layout } from './Layout'
import { DashboardPage } from '../pages/DashboardPage'
import { LawChangeListPage } from '../pages/LawChangeListPage'
import { LawChangeDetailPage } from '../pages/LawChangeDetailPage'
import { ClientListPage } from '../pages/ClientListPage'
import { UploadPage } from '../pages/UploadPage'
import { ReviewPage } from '../pages/ReviewPage'
import { ClientProfilePage } from '../pages/ClientProfilePage'
import { SettingsPage } from '../pages/SettingsPage'
import { EmailTemplatePage } from '../pages/EmailTemplatePage'
import { NotFoundPage, RouteError } from '../pages/NotFoundPage'

export const router = createBrowserRouter([
  {
    element: <Layout />,
    errorElement: <RouteError />,
    children: [
      {
        errorElement: <RouteError />,
        children: [
          { index: true, element: <DashboardPage /> },
          { path: 'gesetzesaenderungen', element: <LawChangeListPage /> },
          { path: 'gesetzesaenderungen/:id', element: <LawChangeDetailPage /> },
          { path: 'mandanten', element: <ClientListPage /> },
          { path: 'mandanten/neu', element: <UploadPage /> },
          { path: 'mandanten/:id', element: <ClientProfilePage /> },
          { path: 'mandanten/:id/upload', element: <UploadPage /> },
          { path: 'mandanten/:id/pruefung', element: <ReviewPage /> },
          { path: 'einstellungen', element: <SettingsPage /> },
          { path: 'einstellungen/email-vorlage', element: <EmailTemplatePage /> },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
])
