/** Display vocabulary only. Stored values and form submissions retain their original codes. */
const valueLabels: Record<string, string> = {
  share_deal: 'Share Deal', asset_deal: 'Asset Deal', buy_side: 'Käuferseite', vendor: 'Verkäuferseite',
  full_scope: 'Vollumfängliche Prüfung', red_flag: 'Red-Flag-Prüfung',
  natuerliche_person: 'Natürliche Person', kapitalgesellschaft: 'Kapitalgesellschaft', personengesellschaft: 'Personengesellschaft',
  betriebsstaette: 'Betriebsstätte', anhaengig: 'Anhängig', ausgeloest: 'Ausgelöst',
  beantragt_gewaehrt: 'Beantragt und gewährt', beantragt_versagt: 'Beantragt und versagt',
  nicht_beantragt: 'Nicht beantragt', nicht_erklaert: 'Nicht erklärt', erklaert: 'Erklärt',
  laeuft: 'Läuft', ungeklaert: 'Ungeklärt', zurueckgezahlt: 'Zurückgezahlt', gruendung: 'Gründung',
  kapitalerhoehung: 'Kapitalerhöhung', mittelbarer_wechsel: 'Mittelbarer Wechsel',
  feststellung_14_5: 'Feststellung nach § 14 Abs. 5 KStG', anmeldung_168: 'Anmeldung nach § 168 AO',
  aussenpruefung_171_4: 'Außenprüfung (§ 171 Abs. 4 AO)', einspruch_171_3a: 'Einspruch (§ 171 Abs. 3a AO)',
  anteilstausch_21: 'Anteilstausch (§ 21 UmwStG)', einbringung_20: 'Einbringung (§ 20 UmwStG)',
  einbringung_24: 'Einbringung (§ 24 UmwStG)', gemeiner_wert: 'Gemeiner Wert',
  kst_verlustvortrag: 'Körperschaftsteuerlicher Verlustvortrag', gewst_fehlbetrag: 'Gewerbesteuerlicher Fehlbetrag',
  ebitda_vortrag: 'EBITDA-Vortrag', kst_gewst: 'Körperschaft- und Gewerbesteuer',
  im_spa_gedeckt: 'Im SPA abgesichert', wi_police: 'W&I-Versicherung',
}

const plainValues = new Set([
  'abgelaufen', 'abgeschlossen', 'abtretung', 'angeordnet', 'anteilstausch', 'aufgehoben', 'beendet', 'beides',
  'besteht', 'buchwert', 'chance', 'covenant', 'eigentum', 'einspruch', 'entfallen', 'erledigt', 'erwerbsgesellschaft',
  'escrow', 'festsetzung', 'finanzierung', 'fonds', 'formwechsel', 'garantie', 'geplant', 'gering', 'gesellschafter',
  'hinweis', 'hoch', 'holding', 'immobilien', 'kaufpreis', 'klage', 'konzerngesellschaft', 'laufend', 'leasing',
  'messbetrag', 'miete', 'mitigiert', 'mittel', 'nahestehend', 'offen', 'operativ', 'realisiert', 'risiko', 'ruht',
  'schenkung', 'sicher', 'spaltung', 'spezialfreistellung', 'steuerfreistellung', 'tochtergesellschaft', 'unbekannt',
  'veranlagt', 'verschmelzung', 'zinsvortrag', 'ja', 'nein',
])

export function valueLabel(value: string): string {
  return valueLabels[value] ?? (plainValues.has(value) ? value[0].toUpperCase() + value.slice(1) : value)
}
