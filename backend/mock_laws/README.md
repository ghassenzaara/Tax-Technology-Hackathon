# Matching live check: BFH I R 1/23

Run date: 2026-10-17. The user supplied `BFH-Urteil-I-R-1-23.pdf` and confirmed it replaces the earlier 22 ruling for this company. This is a byte-identical copy of the local root PDF; no PDF was downloaded. The source states a decision date of 16 July 2025 and ECLI:DE:BFH:2025:U.160725.IR1.23.0.

SHA-256: `1fa9c885726df14bb826cab678b81f849256a4e701c85a32ea258910b1a7d672`.

## Result

The source also has a separate **Was ändert sich?** field (`change_summary`), generated from the same local PDF's first three pages:

> Der BFH stellt klar, dass § 8c KStG den Verlustrücktrag negativer Einkünfte, die vor dem schädlichen Beteiligungserwerb im selben Wirtschaftsjahr entstanden sind, in das Vorjahr nicht ausschließt. Diese Einkünfte können mit einem im Vorjahr erwirtschafteten positiven Gesamtbetrag der Einkünfte verrechnet werden.

`summary` remains the general Zusammenfassung; `change_summary` explains the source's clarification independently of any company; the stored match reasons below explain company-specific relevance and effects. The metadata-only backfill preserved all section vectors and judged matches, including the five hits. The snapshot is in `23-source-details.json`; `--laws --json laws.json` also exports both fields. Earlier sources remain null until refreshed. HTTP/frontend wiring is still deferred.

**5 stored hits** against `Hafenkante-live` (Nordhafen). Every hit identifies the existing loss/shareholder-change finding. For example, Rn. 23 discusses loss restrictions upon a controlling share transfer, and the Leitsatz discusses the remaining loss-carryback opportunity. The plan's positive-hit live acceptance criterion is met.

## Ingestion and screening

Seven text pages, 32 sections, heading strategy; 6.0 s, metadata tokens 4,187 input / 163 output. Source kind `other`, existing source-status value `in_kraft`; publication/effective dates remain null. Vectors use text-embedding-3-small, 1536 dimensions. Sequential BFH margin numbers are retained; seven page-number lines were dropped. `profil.urteil` and the vocabulary were not changed.

Known norms: § 8c KStG, § 10d EStG, § 164 AO.

Unmatched vocabulary codes: § 8 Abs. 1 KStG, § 2 Abs. 2 EStG, § 2 Abs. 4 UmwStG, § 4 Abs. 2 S. 2 UmwStG, § 2 Abs. 4 S. 2 UmwStG, § 2 Abs. 4 S. 1 UmwStG, § 2 UmwStG, § 2 Abs. 1 UmwStG, § 2 Abs. 1 S. 1 UmwStG, § 31 Abs. 1 KStG, § 25 Abs. 1 S. 1 EStG, § 7 Abs. 3 S. 3 KStG.

The profile has 13 features: legal form removed 0 pairs; tax type removed 192; 224 pairs survived. Initial threshold 0.40/cap 20 sent 20 sections to the judge: 20 stored results, 5 hits, 0 unjudged; 25.7 s, tokens 51,496 input / 7,175 output. The judge prompt is the same clarified prompt used at the end of the 22 check; no further prompt changes were made for this PDF.

## Initial threshold calibration

Set `MATCH_SCORE_THRESHOLD` to **0.54**, keeping the cap at 20. This retains all five observed positive sections and reduces candidates from 20 to 17, excluding three sections already judged no. A cutoff of 0.55 loses a positive. This is an initial single-case calibration; validate on more labelled pairs before generalising. High-scoring negatives remain, so the judge is still required.

Cutoffs below use the **maximum score per section**, not the stored hit pair's score: the judge can choose a lower-ranked feature.

| Cutoff | Candidate sections (cap 20) | Observed hits retained |
|---:|---:|---:|
| 0.40 | 20 | 5 / 5 |
| 0.50 | 20 | 5 / 5 |
| 0.54 | 17 | 5 / 5 |
| 0.55 | 12 | 4 / 5 |
| 0.60 | 2 | 1 / 5 |

The following table records every section's best score. Results marked unjudged were outside the initial 20-candidate set; dry run stores nothing.

| Section | Pages | Max cosine | Norm match | Pass at 0.54 | Stored verdict |
|---|---|---:|---|---|---|
| Rn. 32 | 6–6 | 0.6443 | yes | yes | no |
| Rn. 34 bis Rn. 35 | 6–7 | 0.6187 | yes | yes | yes |
| Rn. 4 | 1–2 | 0.5819 | no | yes | no |
| Rn. 23 | 4–4 | 0.5805 | no | yes | yes |
| Rn. 24 | 4–4 | 0.5683 | yes | yes | yes |
| Leitsätze | 1–1 | 0.5670 | yes | yes | yes |
| Rn. 5 | 2–2 | 0.5648 | yes | yes | no |
| Rn. 7 | 2–2 | 0.5638 | yes | yes | no |
| Rn. 18 | 3–3 | 0.5626 | no | yes | no |
| Rn. 31 | 5–6 | 0.5624 | yes | yes | no |
| Rn. 8 | 2–2 | 0.5529 | no | yes | no |
| Rn. 25 | 4–4 | 0.5519 | no | yes | no |
| Rn. 27 | 4–5 | 0.5477 | yes | yes | yes |
| Rn. 15 | 3–3 | 0.5453 | no | yes | no |
| Rn. 33 | 6–6 | 0.5434 | yes | yes | no |
| Rn. 6 | 2–2 | 0.5431 | no | yes | no |
| Rn. 17 | 3–3 | 0.5415 | no | yes | no |
| Rn. 30 | 5–5 | 0.5381 | yes | no | no |
| Rn. 3 | 1–1 | 0.5349 | no | no | no |
| Rn. 21 | 4–4 | 0.5272 | no | no | no |
| Rn. 9 bis Rn. 10 | 2–2 | 0.5246 | no | no | unjudged |
| Tenor bis Rn. 1 | 1–1 | 0.5240 | no | no | unjudged |
| Rn. 28 bis Rn. 29 | 5–5 | 0.5197 | yes | no | unjudged |
| Rn. 2 | 1–1 | 0.5197 | no | no | unjudged |
| Rn. 19 | 3–3 | 0.5110 | no | no | unjudged |
| Rn. 26 | 4–4 | 0.5102 | yes | no | unjudged |
| Rn. 16 | 3–3 | 0.5014 | no | no | unjudged |
| Rn. 22 | 4–4 | 0.5004 | yes | no | unjudged |
| Rn. 20 | 3–4 | 0.4979 | yes | no | unjudged |
| Einleitung | 1–1 | 0.4790 | no | no | unjudged |
| Rn. 11 bis Rn. 13 | 2–3 | 0.4547 | no | no | unjudged |
| Rn. 14 | 3–3 | 0.4392 | yes | no | unjudged |

## Stored hits

### Leitsätze — chance

Matched feature `finding:120` (pair score 0.5550): Bei geplantem Erwerb sämtlicher Anteile können körperschaftsteuerliche Verlustvorträge und gewerbesteuerliche Fehlbeträge untergehen, soweit inländische stille Reserven keinen Erhalt ermöglichen.

Der Leitsatz stellt klar, dass § 8c KStG den Verlustrücktrag negativer Einkünfte aus dem Erwerbsjahr in das Vorjahr nicht ausschließt. Das kann trotz möglicher Untergangsrisiken für Verlustvorträge bei einem Anteilserwerb eine zusätzliche Verrechnungsmöglichkeit für die KSt eröffnen; eine entsprechende Aussage zu gewerbesteuerlichen Fehlbeträgen enthält der Abschnitt nicht.

### Rn. 34 bis Rn. 35 — risiko

Matched feature `finding:120` (pair score 0.5526): Bei geplantem Erwerb sämtlicher Anteile können körperschaftsteuerliche Verlustvorträge und gewerbesteuerliche Fehlbeträge untergehen, soweit inländische stille Reserven keinen Erhalt ermöglichen.

Rn. 34 stellt klar, dass § 8c KStG Verluste zeitpunktgenau beim schädlichen Beteiligungserwerb erfasst und das Jahressteuerprinzip daran nichts ändert. Das betrifft die Prüfung, welche körperschaftsteuerlichen Verluste bei dem geplanten Anteilserwerb gefährdet sind; ob Verluste im Erwerbsjahr anfallen, ist im Sachverhalt allerdings nicht angegeben.

### Rn. 23 — risiko

Matched feature `finding:120` (pair score 0.5493): Bei geplantem Erwerb sämtlicher Anteile können körperschaftsteuerliche Verlustvorträge und gewerbesteuerliche Fehlbeträge untergehen, soweit inländische stille Reserven keinen Erhalt ermöglichen.

Der Abschnitt ordnet bei einer Übertragung von mehr als 50 % der Anteile an, dass bis dahin nicht genutzte Verluste vollständig nicht mehr abziehbar sind. Das betrifft den geplanten Erwerb sämtlicher Anteile und die beschriebenen Verlustvorträge; stille Reserven werden in diesem Abschnitt nicht behandelt.

### Rn. 27 — chance

Matched feature `finding:120` (pair score 0.5437): Bei geplantem Erwerb sämtlicher Anteile können körperschaftsteuerliche Verlustvorträge und gewerbesteuerliche Fehlbeträge untergehen, soweit inländische stille Reserven keinen Erhalt ermöglichen.

Der Abschnitt behandelt, dass § 8c KStG einen Rücktrag unterjähriger Verluste aus dem Erwerbsjahr in das Vorjahr nicht verbietet. Das kann die im Sachverhalt befürchteten Folgen eines Anteilserwerbs für die Verlustnutzung mindern; ob ein solcher Rücktrag für die Gesellschaft möglich ist, ist nicht angegeben.

### Rn. 24 — risiko

Matched feature `finding:120` (pair score 0.5321): Bei geplantem Erwerb sämtlicher Anteile können körperschaftsteuerliche Verlustvorträge und gewerbesteuerliche Fehlbeträge untergehen, soweit inländische stille Reserven keinen Erhalt ermöglichen.

Der Abschnitt bestätigt, dass ein Erwerb sämtlicher Anteile an einer GmbH im steuerlichen Rückwirkungszeitraum trotz Verschmelzung als schädlicher Beteiligungserwerb nach § 8c Abs. 1 Satz 1 KStG gilt. Das betrifft das im Sachverhalt angenommene Risiko für körperschaftsteuerliche Verlustvorträge; zur Gewerbesteuer oder zu stillen Reserven trifft der Abschnitt keine Aussage.

## Rerun and duplicate checks

At the calibrated cutoff an unchanged rerun skipped all 17 candidates in 0.1 s, with 0 input / 0 output tokens. The original five hits remain stored. Duplicate ingest was refused before model calls. No profiles were edited and no new feature types were introduced.

## Reproduce

Run from `backend/`, using the configured key, Postgres URL and an already embedded/cleaned mock profile. Replace LAW_ID with the id printed by ingest (9 on this local database).

```powershell
uv run python -m app.cli --ingest-law mock_laws/BFH-Urteil-I-R-1-23.pdf --repository postgres
uv run python -m app.cli --screen --company-id Hafenkante-live --law-id LAW_ID --dry-run --repository postgres
uv run python -m app.cli --screen --company-id Hafenkante-live --law-id LAW_ID --repository postgres
uv run python -m app.cli --matches --company-id Hafenkante-live --only-hits --repository postgres
```

Raw summaries: `23-ingest.json`, `23-dry-run.json` (initial 0.40), `23-screen.json`, `23-calibrated-dry-run.json` (0.54), `23-rerun.json`, `23-hits.json`. Reported tokens cover metadata/judging; the embedding helper does not report embedding token usage.

The earlier I R 37/22 PDF and its records remain stored. The user clarified that it belongs to another test company not present here; it is no longer the matching acceptance input. Its previous report is archived as [I-R-37-22-check.md](I-R-37-22-check.md); references there to an unmet positive-hit acceptance criterion describe that superseded run only.
