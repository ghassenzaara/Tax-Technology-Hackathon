import { useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronDown, Info, Pencil, RotateCcw } from 'lucide-react'
import type { Company, Fact, ProfileDraft } from '../../data/types'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Card, CardHeader } from '../../components/ui/Card'
import { ConfirmDialog } from '../../components/ui/Dialog'
import { Popover } from '../../components/ui/Popover'
import { ErrorState } from '../../components/ui/States'
import { cn } from '../../lib/cn'
import { formatScalar } from '../../lib/format'
import { reviewStatusText, textFor } from '../../lib/labels'
import { FactValue } from '../profile/FactValue'
import { FactEditor } from '../profile/ProfileEditor'
import { groupFacts } from '../profile/StammdatenCard'
import { canLeaveEmpty, needsDecision, ownDraft, resolve, reviewFacts, reviewOverview, summarize, type Choices, type FactChoice, type ReviewFilter } from './reviewState'
import type { FactResolution } from '../../data/adapter'
import type { Id } from '../../data/types'

const statusTone: Record<string, 'success' | 'neutral' | 'danger' | 'warning'> = {
  confirmed: 'success',
  single_source: 'neutral',
  conflict: 'danger',
  missing: 'danger',
}

export function ReviewForm({
  draft,
  company,
  onConfirm,
  confirming,
  error,
}: {
  draft: ProfileDraft
  company: Company
  onConfirm: (resolutions: Record<Id, FactResolution>) => void
  confirming: boolean
  error: unknown
}) {
  const [choices, setChoices] = useState<Choices>({})
  const [filter, setFilter] = useState<ReviewFilter>('review')
  const [asking, setAsking] = useState(false)
  const isUpdate = company.status !== 'draft'
  const previous = useMemo(() => new Map(company.facts.map((f) => [f.label, f])), [company.facts])

  const summary = useMemo(() => summarize(draft.facts, choices), [draft.facts, choices])
  const overview = reviewOverview(draft.facts)
  const required = overview.review
  const openSet = new Set(summary.open)
  const shown = reviewFacts(draft.facts, filter)
  const groups = groupFacts(shown)

  const setChoice = (id: Id, choice: FactChoice | undefined) =>
    setChoices((c) => {
      const next = { ...c }
      if (choice) next[id] = choice
      else delete next[id]
      return next
    })

  if (draft.facts.length === 0) {
    return (
      <Card className="mt-6">
        <CardHeader title="Prüfung" />
        <div className="px-5 pb-5">
          <p className="text-sm text-ink-muted">Die neuen Dokumente enthalten keine neuen oder abweichenden Werte. Das Profil bleibt unverändert.</p>
          <div className="mt-4 flex justify-end">
            <Button variant="primary" onClick={() => onConfirm({})} disabled={confirming}>
              Abschließen
            </Button>
          </div>
        </div>
      </Card>
    )
  }

  return (
    <div className="mt-6">
      <div className="mb-5">
        <h2 className="text-lg font-semibold">Ergebnis der Dokumentenauswertung</h2>
        <p className="mt-1 text-sm text-ink-muted">
          {overview.found} Angaben gefunden · {overview.conflicts} mit unterschiedlichen Werten · {overview.missing} nicht gefunden
        </p>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-ink-muted">
          Prüfen Sie zuerst die Widersprüche und markierten Angaben. Bei Widersprüchen stehen die gefundenen Werte zur Auswahl.
          Alle weiteren Angaben können Sie unter „Alle Angaben“ einsehen und bearbeiten.
        </p>
      </div>
      <div className="sticky top-16 z-10 -mx-1 mb-4 rounded-xl border border-line bg-surface/95 px-4 py-3 shadow-sm backdrop-blur">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <p className="text-sm font-medium" aria-live="polite">
            {summary.open.length === 0 ? (
              <span className="flex items-center gap-1.5 text-success">
                <CheckCircle2 className="size-4" aria-hidden />
                Alle offenen Angaben sind entschieden
              </span>
            ) : (
              <span className="flex items-center gap-1.5 text-danger">
                <AlertTriangle className="size-4" aria-hidden />
                Noch {summary.open.length} {summary.open.length === 1 ? 'Angabe' : 'Angaben'} zu prüfen
              </span>
            )}
          </p>
          <Button variant="primary" className="ml-auto" disabled={!summary.canConfirm || confirming} onClick={() => setAsking(true)}>
            {isUpdate ? 'Änderungen übernehmen' : 'Profil bestätigen'}
          </Button>
        </div>
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-neutral-soft" aria-hidden>
          <div
            className="h-full origin-left rounded-full bg-success transition-transform duration-300 ease-[var(--ease-out)]"
            style={{ transform: `scaleX(${required === 0 ? 1 : (required - summary.open.length) / required})` }}
          />
        </div>
      </div>

      <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label="Angaben filtern">
        {([
          ['review', 'Prüfbedarf', overview.review],
          ['all', 'Alle Angaben', overview.total],
          ['missing', 'Nicht gefunden', overview.missing],
        ] as const).map(([key, label, count]) => <button key={key} type="button" aria-pressed={filter === key}
          onClick={() => setFilter(key)} className={cn('rounded-lg border px-3 py-2 text-sm transition-colors',
            filter === key ? 'border-primary bg-primary-soft font-medium text-primary' : 'border-line bg-surface text-ink-muted hover:border-line-strong')}>
          {label} <span className="ml-1 tabular-nums">{count}</span>
        </button>)}
      </div>

      {error ? (
        <div className="mb-4">
          <ErrorState error={error} />
        </div>
      ) : null}

      <div className="space-y-6">
        {groups.map(([group, facts], index) => (
          <details key={`${filter}:${group}`} open={index === 0} className="group/review overflow-hidden rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
            <summary className="flex cursor-pointer list-none items-center gap-3 px-5 py-4 [&::-webkit-details-marker]:hidden">
              <h3 className="text-sm font-semibold">{group}</h3>
              <span className="text-xs text-ink-muted">{facts.length} {facts.length === 1 ? 'Angabe' : 'Angaben'}</span>
              {facts.some((f) => openSet.has(f.id)) && <Badge tone="warning">{facts.filter((f) => openSet.has(f.id)).length} offen</Badge>}
              <ChevronDown className="ml-auto size-4 shrink-0 text-ink-muted transition-transform group-open/review:rotate-180" aria-hidden />
            </summary>
            <ul className="divide-y divide-line border-t border-line">
              {facts.map((f) => (
                <FactReviewRow
                  key={f.id}
                  fact={f}
                  choice={choices[f.id]}
                  error={summary.errors[f.id]}
                  open={openSet.has(f.id)}
                  previous={isUpdate ? previous.get(f.label) : undefined}
                  isUpdate={isUpdate}
                  onChoose={(c) => setChoice(f.id, c)}
                />
              ))}
            </ul>
          </details>
        ))}
        {shown.length === 0 && <p className="py-6 text-center text-sm text-ink-muted">
          {filter === 'missing' ? 'Für jede Angabe wurden Werte gefunden.' : 'Keine Angaben mit Prüfbedarf. Unter „Alle Angaben“ können Sie das Ergebnis einsehen.'}
        </p>}
      </div>

      <ConfirmDialog
        open={asking}
        title="Sind Sie sicher?"
        confirmLabel={isUpdate ? 'Ja, übernehmen' : 'Ja, bestätigen'}
        busy={confirming}
        onCancel={() => setAsking(false)}
        onConfirm={() => {
          setAsking(false)
          onConfirm(summary.resolutions)
        }}
      >
        {isUpdate
          ? 'Die geprüften Werte ersetzen die bisherigen im Profil. Jede Änderung wird im Verlauf protokolliert.'
          : 'Das Profil wird bestätigt und gespeichert. Danach bleiben als Quelle nur Dateiname und Seite, Zitate und Alternativen werden nicht mehr angezeigt.'}
      </ConfirmDialog>
    </div>
  )
}

function SourceInfo({ fact }: { fact: Fact }) {
  const sources = fact.candidates?.length ? [] : fact.sources
  return (
    <Popover label={`Quelle und Begründung für ${fact.label}`} trigger={<Info className="size-4" aria-hidden />}>
      <p className="mb-2 text-xs font-semibold tracking-wide text-ink-muted uppercase">Quelle</p>
      {sources.length === 0 && !fact.candidates?.length && <p className="text-sm text-ink-muted">Kein Dokument enthält diesen Wert.</p>}
      {fact.candidates?.length ? <p className="text-sm text-ink-muted">Die Quellen stehen bei den einzelnen Werten.</p> : null}
      <ul className="space-y-2">
        {sources.map((s, i) => (
          <li key={i}>
            <p className="text-sm font-medium">
              {s.filename}, Seite {s.page}
            </p>
            {/* Evidence quotes are untrusted document text: rendered as plain text only (UI.md 6). */}
            {s.quote && <p className="mt-0.5 rounded bg-canvas px-2 py-1 text-xs text-ink-muted italic">„{s.quote}“</p>}
          </li>
        ))}
      </ul>
      {fact.reviewReason && (
        <p className="mt-3 text-xs text-warning">
          <span className="font-semibold">Hinweis: </span>
          {fact.reviewReason}
        </p>
      )}
      {fact.reasoning && (
        <p className="mt-3 text-xs text-ink-muted">
          <span className="font-semibold">Begründung der Auswertung: </span>
          {fact.reasoning}
        </p>
      )}
    </Popover>
  )
}

function FactReviewRow({
  fact,
  choice,
  error,
  open,
  previous,
  isUpdate,
  onChoose,
}: {
  fact: Fact
  choice?: FactChoice
  error?: string
  open: boolean
  previous?: Fact
  isUpdate: boolean
  onChoose: (c: FactChoice | undefined) => void
}) {
  const required = needsDecision(fact)
  const decided = required && resolve(fact, choice).ok === true
  const conflict = fact.reviewStatus === 'conflict'
  const missing = fact.reviewStatus === 'missing'
  const statusLabel = fact.needsReview && !conflict && !missing ? 'Prüfen' : textFor(reviewStatusText, fact.reviewStatus ?? '')
  const tone = fact.needsReview && !conflict && !missing ? 'warning' : (statusTone[fact.reviewStatus ?? ''] ?? 'neutral')
  const own = choice?.mode === 'own' ? choice : null
  const startOwn = () => onChoose({ mode: 'own', draft: ownDraft(fact) })

  return (
    <li className={cn('relative px-5 py-4 transition-colors duration-200', open && 'bg-danger-soft/30', error && 'bg-danger-soft/30')}>
      <span
        className={cn('absolute inset-y-0 left-0 w-1 transition-colors duration-200', open || error ? 'bg-danger' : decided ? 'bg-success' : 'bg-transparent')}
        aria-hidden
      />
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-medium">{fact.label}</h4>
        {fact.reviewStatus && <Badge tone={tone}>{statusLabel}</Badge>}
        {fact.reasoning?.startsWith('KI-Abgleich:') && <Badge tone="neutral">KI-Abgleich</Badge>}
        {decided && (
          <span className="flex items-center gap-1 text-xs text-success">
            <CheckCircle2 className="size-3.5" aria-hidden />
            Entschieden
          </span>
        )}
        <span className="ml-auto">
          <SourceInfo fact={fact} />
        </span>
      </div>

      {isUpdate && (
        <div className="mt-1 text-xs text-ink-muted">
          Bisher: {previous ? <FactValue value={previous.value} type={previous.type} unit={previous.unit} label={previous.label} /> : 'nicht im Profil'}
        </div>
      )}

      <div className="mt-2">
        {conflict ? (
          <fieldset>
            <legend className="mb-2 text-xs text-ink-muted">Die Dokumente nennen unterschiedliche Werte. Bitte wählen Sie einen aus.</legend>
            <div className="space-y-2">
              {fact.candidates?.map((c, candidateIndex) => (
                <div
                  key={c.id}
                  className={cn(
                    'min-w-0 rounded-lg border p-4',
                    choice?.mode === 'candidate' && choice.candidateId === c.id ? 'border-primary bg-primary-soft' : 'border-line bg-surface',
                  )}
                >
                  <label className="mb-3 flex cursor-pointer items-center gap-2 text-sm font-medium">
                    <input
                      type="radio"
                      name={`fact-${fact.id}`}
                      checked={choice?.mode === 'candidate' && choice.candidateId === c.id}
                      onChange={() => onChoose({ mode: 'candidate', candidateId: c.id })}
                      className="size-4 accent-primary"
                    />
                    Variante {candidateIndex + 1}
                  </label>
                  <FactValue value={c.value} type={fact.type} unit={fact.unit} label={fact.label} />
                  <div className="mt-3 border-t border-line/70 pt-2">
                    {c.sources.map((s, i) => (
                      <div key={i} className="mt-1 text-xs text-ink-muted">
                        {s.quote ? <details>
                          <summary className="cursor-pointer">{s.filename}, Seite {s.page} · Beleg anzeigen</summary>
                          <blockquote className="mt-2 border-l-2 border-line pl-3 leading-relaxed">„{s.quote}“</blockquote>
                        </details> : <p>{s.filename}, Seite {s.page}</p>}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
              <label
                className={cn(
                  'flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 hover:border-line-strong',
                  own ? 'border-primary bg-primary-soft' : 'border-line bg-surface',
                )}
              >
                <input type="radio" name={`fact-${fact.id}`} checked={!!own} onChange={startOwn} className="size-4 accent-primary" />
                <span className="text-sm font-medium">Eigenen Wert eingeben</span>
              </label>
            </div>
          </fieldset>
        ) : missing ? (
          <div>
            {!own && choice?.mode !== 'empty' && <p className="text-sm text-ink-muted">In keinem Dokument gefunden.</p>}
            {choice?.mode === 'empty' && <p className="text-sm text-ink-muted italic">Wird bewusst leer gelassen.</p>}
            <div className="mt-2 flex flex-wrap gap-2">
              {!own && (
                <Button size="sm" onClick={startOwn}>
                  <Pencil className="size-3.5" aria-hidden />
                  Wert eingeben
                </Button>
              )}
              {choice?.mode !== 'empty' ? (
                canLeaveEmpty(fact) && (
                  <Button size="sm" variant="ghost" onClick={() => onChoose({ mode: 'empty' })}>
                    Bewusst leer lassen
                  </Button>
                )
              ) : (
                <Button size="sm" variant="ghost" onClick={() => onChoose(undefined)}>
                  <RotateCcw className="size-3.5" aria-hidden />
                  Rückgängig
                </Button>
              )}
            </div>
          </div>
        ) : (
          <div>
            {!own && (
              <div className="text-sm">
                <FactValue value={fact.value} type={fact.type} unit={fact.unit} label={fact.label} />
              </div>
            )}
            {!own && fact.reasoning?.startsWith('KI-Abgleich:') && (
              <div className="mt-3 rounded-lg border border-line bg-canvas p-3 text-xs text-ink-muted">
                <p className="leading-relaxed">{fact.reasoning}</p>
                <details className="mt-2">
                  <summary className="cursor-pointer font-medium">Ursprüngliche Angaben vergleichen</summary>
                  <div className="mt-3 space-y-4">
                    {fact.alternatives?.map((alternative, index) => <div key={index}>
                      <FactValue value={alternative.value} type={fact.type} unit={fact.unit} label={fact.label} />
                      <p className="mt-1">{alternative.sources.map((source) => `${source.filename}, S. ${source.page}`).join('; ')}</p>
                    </div>)}
                  </div>
                </details>
              </div>
            )}
            {!own && !fact.reasoning?.startsWith('KI-Abgleich:') && fact.alternatives && fact.alternatives.length > 0 && (
              <p className="mt-1 text-xs text-ink-muted">
                Gerundete Angaben in anderen Dokumenten:{' '}
                {fact.alternatives
                  .map((a) => `${formatScalar(a.value, fact.type, fact.unit, fact.label)} (${a.sources.map((s) => `${s.filename}, S. ${s.page}`).join('; ')})`)
                  .join(', ')}
              </p>
            )}
            {!own && fact.needsReview && (
              <p className="mt-2 flex items-start gap-1.5 rounded-lg bg-warning-soft px-3 py-2 text-xs text-warning">
                <AlertTriangle className="mt-px size-3.5 shrink-0" aria-hidden />
                {fact.reviewReason ?? 'Bitte diesen Wert prüfen.'}
              </p>
            )}
            {!own && (
              <div className="mt-2 flex flex-wrap gap-2">
                {fact.needsReview && choice?.mode !== 'accept' && (
                  <Button size="sm" onClick={() => onChoose({ mode: 'accept' })}>
                    <CheckCircle2 className="size-3.5" aria-hidden />
                    Wert übernehmen
                  </Button>
                )}
                {fact.needsReview && choice?.mode === 'accept' && (
                  <Button size="sm" variant="ghost" onClick={() => onChoose(undefined)}>
                    <RotateCcw className="size-3.5" aria-hidden />
                    Rückgängig
                  </Button>
                )}
                <Button size="sm" variant="ghost" onClick={startOwn}>
                  <Pencil className="size-3.5" aria-hidden />
                  {fact.needsReview ? 'Anderen Wert eingeben' : 'Ändern'}
                </Button>
              </div>
            )}
          </div>
        )}

        {own && (
          <div className="mt-3 max-w-xl rounded-lg border border-line bg-surface p-3">
            <FactEditor fact={fact} draft={own.draft} error={error} label="Eigener Wert" onChange={(d) => onChoose({ mode: 'own', draft: d })} />
            {!conflict && (
              <Button size="sm" variant="ghost" className="mt-2" onClick={() => onChoose(undefined)}>
                <RotateCcw className="size-3.5" aria-hidden />
                Abbrechen
              </Button>
            )}
          </div>
        )}
      </div>
    </li>
  )
}
