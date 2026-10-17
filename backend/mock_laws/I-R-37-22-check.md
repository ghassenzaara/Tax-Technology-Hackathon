# Matching live check: BFH I R 37/22

Archived: the user clarified this PDF belongs to another test company. The current Nordhafen acceptance input is BFH I R 1/23; see [README.md](README.md). The open-acceptance notes below describe only this earlier run.

Run date: 2026-10-17. Input: the user's local `BFH-Urteil-I-R-37-22.pdf`, copied here from the repository root. No PDF was downloaded. The document states a decision date of 5 November 2025 and ECLI:DE:BFH:2025:U.051125.IR37.22.0.

SHA-256: `b274f2a3419110c42590ae3515c6d18dbea487e733d6180ae87ba2bcc6a11e8b`.

## Ingestion

Five text pages, 32 sections, heading strategy; 9.8 s, metadata tokens 4,032 input / 311 output. Vectors: text-embedding-3-small, 1536 dimensions. Source kind `other`, status `in_kraft` under the existing source-status vocabulary. Publication/effective dates stay null: a judgment date is not either date. No rows are written to `profil.urteil`.

The splitter follows sequential BFH margin numbers, including subheadings; it does not mistake the literature citation ‘75 Jahre …’ for a paragraph. Tiny parts merge and page references survive. Five page-number lines were dropped. Vocabulary norm found: § 14 KStG. Unmatched norm: § 17 S. 1 KStG. The vocabulary was not changed.

## Dry run and calibration

Profile `Hafenkante-live`: 13 real stored mock-case features. Legal form removes 0 pairs; tax type removes 116; 300 pairs survive. Dry run makes no model calls and stores no matches.

Best section scores range from 0.421 to 0.575. At threshold 0.40 all 32 sections clear the cutoff, and the cap of 20 decides which reach the judge. Keep 0.40 and 20 as provisional defaults: this single source/profile pair supplies no reliable positive/negative boundary. Increasing the cutoff would discard substantive paragraphs while retaining several factual passages. Numeric and temporal rule work remains deferred as planned.

| Section | Pages | Max cosine | Norm match | Candidate | Best feature sentence |
|---|---|---:|---|---|---|
| Rn. 6 | 2–2 | 0.5752 | no | yes | Das Einspruchsverfahren gegen den Gewerbesteuermessbescheid ruht. |
| Tenor bis Rn. 1 | 1–1 | 0.5626 | no | yes | Die Finanzverwaltung behandelt die gesamte Miete für die Regalanlage seit Beginn des streitigen Zeitraums als Miete unbeweglicher Wirtschaftsgüter; der Einspruch ruht und die Messbescheide sind insoweit vorläufig. |
| Rn. 2 | 1–1 | 0.5622 | no | yes | Aus dem zum Closing zurückgezahlten Gesellschafterdarlehen besteht ein Risiko verdeckter Gewinnausschüttungen für die Vorjahre mit möglichen Folgen für Körperschaft- und Gewerbesteuer. |
| Rn. 3 | 1–2 | 0.5574 | no | yes | Aus dem zum Closing zurückgezahlten Gesellschafterdarlehen besteht ein Risiko verdeckter Gewinnausschüttungen für die Vorjahre mit möglichen Folgen für Körperschaft- und Gewerbesteuer. |
| Leitsätze | 1–1 | 0.5569 | no | yes | Aus dem zum Closing zurückgezahlten Gesellschafterdarlehen besteht ein Risiko verdeckter Gewinnausschüttungen für die Vorjahre mit möglichen Folgen für Körperschaft- und Gewerbesteuer. |
| Rn. 33 bis Rn. 34 | 5–5 | 0.5516 | no | yes | Die Finanzverwaltung behandelt die gesamte Miete für die Regalanlage seit Beginn des streitigen Zeitraums als Miete unbeweglicher Wirtschaftsgüter; der Einspruch ruht und die Messbescheide sind insoweit vorläufig. |
| Rn. 31 | 5–5 | 0.5501 | no | yes | Die Finanzverwaltung behandelt die gesamte Miete für die Regalanlage seit Beginn des streitigen Zeitraums als Miete unbeweglicher Wirtschaftsgüter; der Einspruch ruht und die Messbescheide sind insoweit vorläufig. |
| Rn. 7 | 2–2 | 0.5469 | no | yes | Aus dem zum Closing zurückgezahlten Gesellschafterdarlehen besteht ein Risiko verdeckter Gewinnausschüttungen für die Vorjahre mit möglichen Folgen für Körperschaft- und Gewerbesteuer. |
| Rn. 4 bis Rn. 5 | 2–2 | 0.5430 | no | yes | Aus dem zum Closing zurückgezahlten Gesellschafterdarlehen besteht ein Risiko verdeckter Gewinnausschüttungen für die Vorjahre mit möglichen Folgen für Körperschaft- und Gewerbesteuer. |
| Rn. 30 | 5–5 | 0.5423 | no | yes | Aus dem zum Closing zurückgezahlten Gesellschafterdarlehen besteht ein Risiko verdeckter Gewinnausschüttungen für die Vorjahre mit möglichen Folgen für Körperschaft- und Gewerbesteuer. |
| Rn. 17 | 3–3 | 0.5416 | no | yes | Die Veräußerung sperrfristbehafteter Anteile kann rückwirkend einen Einbringungsgewinn beim Einbringenden auslösen und der Gesellschaft bei Vorliegen der Voraussetzungen eine Buchwertaufstockung ermöglichen. |
| Rn. 8 | 2–2 | 0.5404 | no | yes | Gegen den Gewerbesteuermessbescheid ist ein Einspruch als laufendes Rechtsbehelfsverfahren anhängig. |
| Rn. 29 | 5–5 | 0.5379 | no | yes | Aus dem zum Closing zurückgezahlten Gesellschafterdarlehen besteht ein Risiko verdeckter Gewinnausschüttungen für die Vorjahre mit möglichen Folgen für Körperschaft- und Gewerbesteuer. |
| Rn. 15 | 3–3 | 0.5317 | no | yes | Einzelunternehmen wurden im Wege einer steuerlichen Umwandlung in die Gesellschaft eingebracht. |
| Rn. 19 | 3–4 | 0.5308 | no | yes | Die Veräußerung sperrfristbehafteter Anteile kann rückwirkend einen Einbringungsgewinn beim Einbringenden auslösen und der Gesellschaft bei Vorliegen der Voraussetzungen eine Buchwertaufstockung ermöglichen. |
| Rn. 11 | 2–2 | 0.5293 | no | yes | Bei geplantem Erwerb sämtlicher Anteile können körperschaftsteuerliche Verlustvorträge und gewerbesteuerliche Fehlbeträge untergehen, soweit inländische stille Reserven keinen Erhalt ermöglichen. |
| Rn. 18 | 3–3 | 0.5261 | no | yes | Aus dem zum Closing zurückgezahlten Gesellschafterdarlehen besteht ein Risiko verdeckter Gewinnausschüttungen für die Vorjahre mit möglichen Folgen für Körperschaft- und Gewerbesteuer. |
| Einleitung | 1–1 | 0.5159 | no | yes | Bei geplantem Erwerb sämtlicher Anteile können körperschaftsteuerliche Verlustvorträge und gewerbesteuerliche Fehlbeträge untergehen, soweit inländische stille Reserven keinen Erhalt ermöglichen. |
| Rn. 26 bis Rn. 27 | 5–5 | 0.5019 | no | yes | Aus dem zum Closing zurückgezahlten Gesellschafterdarlehen besteht ein Risiko verdeckter Gewinnausschüttungen für die Vorjahre mit möglichen Folgen für Körperschaft- und Gewerbesteuer. |
| Rn. 12 | 2–3 | 0.5011 | no | yes | Aus dem zum Closing zurückgezahlten Gesellschafterdarlehen besteht ein Risiko verdeckter Gewinnausschüttungen für die Vorjahre mit möglichen Folgen für Körperschaft- und Gewerbesteuer. |
| Rn. 23 | 4–4 | 0.4986 | no | no | Die Veräußerung sperrfristbehafteter Anteile kann rückwirkend einen Einbringungsgewinn beim Einbringenden auslösen und der Gesellschaft bei Vorliegen der Voraussetzungen eine Buchwertaufstockung ermöglichen. |
| Rn. 13 | 3–3 | 0.4959 | no | no | Aus dem zum Closing zurückgezahlten Gesellschafterdarlehen besteht ein Risiko verdeckter Gewinnausschüttungen für die Vorjahre mit möglichen Folgen für Körperschaft- und Gewerbesteuer. |
| Rn. 21 | 4–4 | 0.4947 | no | no | Bei geplantem Erwerb sämtlicher Anteile können körperschaftsteuerliche Verlustvorträge und gewerbesteuerliche Fehlbeträge untergehen, soweit inländische stille Reserven keinen Erhalt ermöglichen. |
| Rn. 24 | 4–4 | 0.4911 | no | no | Die Veräußerung sperrfristbehafteter Anteile kann rückwirkend einen Einbringungsgewinn beim Einbringenden auslösen und der Gesellschaft bei Vorliegen der Voraussetzungen eine Buchwertaufstockung ermöglichen. |
| Rn. 32 | 5–5 | 0.4876 | no | no | Die Veräußerung sperrfristbehafteter Anteile kann rückwirkend einen Einbringungsgewinn beim Einbringenden auslösen und der Gesellschaft bei Vorliegen der Voraussetzungen eine Buchwertaufstockung ermöglichen. |
| Rn. 25 | 4–4 | 0.4864 | no | no | Die Veräußerung sperrfristbehafteter Anteile kann rückwirkend einen Einbringungsgewinn beim Einbringenden auslösen und der Gesellschaft bei Vorliegen der Voraussetzungen eine Buchwertaufstockung ermöglichen. |
| Rn. 22 | 4–4 | 0.4804 | no | no | Die Veräußerung sperrfristbehafteter Anteile kann rückwirkend einen Einbringungsgewinn beim Einbringenden auslösen und der Gesellschaft bei Vorliegen der Voraussetzungen eine Buchwertaufstockung ermöglichen. |
| Rn. 28 | 5–5 | 0.4746 | no | no | Aus dem zum Closing zurückgezahlten Gesellschafterdarlehen besteht ein Risiko verdeckter Gewinnausschüttungen für die Vorjahre mit möglichen Folgen für Körperschaft- und Gewerbesteuer. |
| Rn. 9 bis Rn. 10 | 2–2 | 0.4677 | no | no | Gegen den Gewerbesteuermessbescheid ist ein Einspruch als laufendes Rechtsbehelfsverfahren anhängig. |
| Rn. 16 | 3–3 | 0.4497 | no | no | Aus dem zum Closing zurückgezahlten Gesellschafterdarlehen besteht ein Risiko verdeckter Gewinnausschüttungen für die Vorjahre mit möglichen Folgen für Körperschaft- und Gewerbesteuer. |
| Rn. 14 | 3–3 | 0.4249 | no | no | Die Veräußerung sperrfristbehafteter Anteile kann rückwirkend einen Einbringungsgewinn beim Einbringenden auslösen und der Gesellschaft bei Vorliegen der Voraussetzungen eine Buchwertaufstockung ermöglichen. |
| Rn. 20 | 4–4 | 0.4205 | no | no | Die Veräußerung sperrfristbehafteter Anteile kann rückwirkend einen Einbringungsgewinn beim Einbringenden auslösen und der Gesellschaft bei Vorliegen der Voraussetzungen eine Buchwertaufstockung ermöglichen. |

## Judge and rerun

The initial judge run returned one yes for Rn. 3 against a shareholder-loan feature (score 0.5574). Review found that this was a factual-passage false positive: similar bookkeeping does not establish a tax consequence for the feature. The prompt's relevance rule was clarified to reject factual accounts, party assertions and procedural outcomes without a substantive tax rule. A force re-screen replaced those results.

Final run: 20 stored results, 0 hits, 0 unjudged; 24.3 s, tokens 47,394 input / 6,525 output. An unchanged rerun skipped 20 sections in 0.1 s, with 0 input / 0 output tokens. Duplicate ingestion was refused before any model call. `--laws` and `--matches --only-hits` both succeed; the latter is empty after the corrected run.

## Remaining acceptance limit

The plan's live acceptance criterion of at least one valid stored hit is **not met** by this source/profile combination. The ruling concerns actual performance of a profit-transfer agreement (GAV/Organschaft), but the current selected feature vectors contain no GAV/Organschaft feature. The profile-selection policy embeds only ja Merkmale, findings and appeals; the Nordhafen seed describes its GAV as a draft and B04 as nein. A lower similarity threshold cannot supply a missing feature. Existing profile facts, selection policy and the supplied PDF were kept as provided. A valid positive example is still needed before claiming positive-hit acceptance or final threshold calibration.

## Reproduce

Run from `backend/` with the configured key and local Postgres URL. The company must already be embedded or cleaned. Use the id returned by ingest instead of assuming the id from this database (3).

```powershell
uv run python -m app.cli --ingest-law mock_laws/BFH-Urteil-I-R-37-22.pdf --repository postgres
uv run python -m app.cli --laws --repository postgres
uv run python -m app.cli --screen --company-id Hafenkante-live --law-id LAW_ID --dry-run --repository postgres
uv run python -m app.cli --screen --company-id Hafenkante-live --law-id LAW_ID --repository postgres
uv run python -m app.cli --matches --company-id Hafenkante-live --only-hits --repository postgres
```

`--force` applies prompt changes to already judged sections; `--replace` re-ingests the same PDF and cascades deletion of its old sections/matches. Raw command summaries: `live-ingest.json`, `live-dry-run.json`, `live-screen.json`, `live-rerun.json`, `live-hits.json`. The final hit file is empty. Tokens shown here cover reported metadata/judge usage; embedding token usage is not reported by the current embedding helper.
