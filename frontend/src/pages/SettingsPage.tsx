import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ChevronRight, Mail, RotateCcw } from 'lucide-react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { PageHeader } from '../components/PageHeader'
import { Button } from '../components/ui/Button'
import { Card, CardHeader } from '../components/ui/Card'
import { ConfirmDialog } from '../components/ui/Dialog'
import { adapter, isMockData } from '../data'
import { Badge } from '../components/ui/Badge'
import { useStoredEmailTemplate } from '../data/queries'

export function SettingsPage() {
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const stored = useStoredEmailTemplate()
  const [busy, setBusy] = useState(false)

  async function reset() {
    if (!adapter.resetDemo) return
    setBusy(true)
    try {
      await adapter.resetDemo()
      await queryClient.invalidateQueries()
      toast.success('Demo wurde zurückgesetzt.')
      setConfirming(false)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader title="Einstellungen" />
      <div className="space-y-6">
        <Card className="max-w-2xl overflow-hidden">
          <Link
            to="/einstellungen/email-vorlage"
            className="group flex items-center gap-4 px-5 py-4 transition-colors duration-150 hover:bg-canvas"
          >
            <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-primary-soft text-primary">
              <Mail className="size-5" aria-hidden />
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
                E-Mail Vorlage ansehen und ändern
                {stored.data && <Badge tone={stored.data.isDefault ? 'neutral' : 'info'}>{stored.data.isDefault ? 'Standard' : 'Angepasst'}</Badge>}
              </span>
              <span className="mt-0.5 block text-sm text-ink-muted">Text der Gesprächsangebote an Ihre Mandanten, mit Verlauf aller Änderungen.</span>
            </span>
            <ChevronRight className="size-5 shrink-0 text-ink-subtle nudge" aria-hidden />
          </Link>
        </Card>
        {isMockData && adapter.resetDemo && (
          <Card className="max-w-2xl">
            <CardHeader title="Demo" />
            <div className="flex flex-wrap items-center justify-between gap-4 px-5 pb-5">
              <p className="text-sm text-ink-muted">Setzt alle Beispieldaten und die E-Mail Vorlage auf den Ausgangszustand zurück.</p>
              <Button onClick={() => setConfirming(true)}>
                <RotateCcw className="size-4" aria-hidden />
                Demo zurücksetzen
              </Button>
            </div>
          </Card>
        )}
      </div>
      <ConfirmDialog
        open={confirming}
        title="Sind Sie sicher?"
        confirmLabel="Ja, zurücksetzen"
        tone="danger"
        busy={busy}
        onCancel={() => setConfirming(false)}
        onConfirm={reset}
      >
        Alle Änderungen in der Demo gehen verloren.
      </ConfirmDialog>
    </>
  )
}
