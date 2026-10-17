import { PageHeader } from '../components/PageHeader'
import { TemplateSettings } from '../features/email/TemplateSettings'

export function EmailTemplatePage() {
  return (
    <>
      <PageHeader
        title="E-Mail Vorlage"
        description="Diese Vorlage wird für jedes Gesprächsangebot an Ihre Mandanten verwendet."
        back={{ to: '/einstellungen', label: 'Zurück zu den Einstellungen' }}
      />
      <TemplateSettings />
    </>
  )
}
