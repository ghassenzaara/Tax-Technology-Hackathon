"""German labels of the profile schema for the UI (handout section 7).

Written by hand, umlauts included: they are never derived from field names ("steuer"
would break). A field or column the schema adds later and this map does not know yet is
shown with its technical name, so a schema change never breaks the UI. Merkmal names come
from the vocabulary (`profil.merkmal_def.bezeichnung`).
"""

FIELD_LABELS: dict[str, str] = {
    # deal
    "deal_typ": "Transaktionsart",
    "kaeufer": "Käufer",
    "verkaeufer": "Verkäufer",
    "signing_am": "Signing",
    "closing_am": "Closing",
    "kaufpreis_eur": "Kaufpreis",
    "locked_box_stichtag": "Locked-Box-Stichtag",
    "dd_art": "DD-Art",
    "dd_umfang": "DD-Umfang",
    "dd_ersteller": "DD erstellt von",
    "dd_report_am": "DD-Bericht vom",
    "dd_datenraum_stichtag": "Datenraum-Stichtag",
    "dd_zeitraum_von": "DD-Zeitraum von",
    "dd_zeitraum_bis": "DD-Zeitraum bis",
    # gesellschaft
    "name": "Firma",
    "strasse": "Straße",
    "plz": "PLZ",
    "ort": "Ort",
    "land": "Land",
    "registergericht": "Registergericht",
    "registernummer": "Registernummer",
    "rechtsform": "Rechtsform",
    "rolle": "Rolle",
    "wz_code": "WZ-Code",
    "wz_bezeichnung": "Branche",
    "steuernummer": "Steuernummer",
    "finanzamt": "Finanzamt",
    "ansprechpartner_email": "E-Mail Ansprechpartner",
    # lists
    "merkmal": "Merkmale",
    "beteiligung": "Gesellschafter",
    "anteilswechsel": "Anteilswechsel",
    "organschaft": "Organschaft",
    "auslandsbezug": "Auslandsbezug",
    "veranlagung": "Veranlagungen",
    "rechtsbehelf": "Rechtsbehelfe",
    "aussenpruefung": "Außenprüfungen",
    "pruefungsfeststellung": "Prüfungsfeststellungen",
    "steuerattribut": "Steuerattribute (Verlustvorträge)",
    "darlehen": "Darlehen",
    "nutzungsvertrag": "Miet- und Leasingverträge",
    "grundstueck": "Grundstücke",
    "umwandlung": "Umwandlungen",
    "ust_steuerfrei": "Steuerfreie Umsätze",
    "finding": "Feststellungen der DD",
    "spa_absicherung": "Absicherung im SPA",
}

# Groups of the scalar tables; every list field is its own group (its label).
TABLE_GROUPS: dict[str, str] = {"deal": "Transaktion", "gesellschaft": "Stammdaten"}

# Columns per list field (the same column name can mean different things in two lists).
COLUMN_LABELS: dict[str, dict[str, str]] = {
    "merkmal": {"merkmal_code": "Merkmal", "wert": "Wert", "kurztext": "Kurztext"},
    "beteiligung": {
        "gesellschafter": "Gesellschafter",
        "gesellschafter_art": "Art",
        "sitz_wohnort": "Sitz / Wohnort",
        "quote_prozent": "Quote (%)",
        "anteile": "Anteile",
        "gueltig_ab": "Gültig ab",
        "gueltig_bis": "Gültig bis",
    },
    "anteilswechsel": {
        "datum": "Datum",
        "vorgang": "Vorgang",
        "quote_prozent": "Quote (%)",
        "abgebender": "Abgebender",
        "erwerber": "Erwerber",
        "mittelbar": "Mittelbar",
        "beschreibung": "Beschreibung",
    },
    "organschaft": {
        "art": "Art",
        "status": "Status",
        "organtraeger_name": "Organträger",
        "vertrag_am": "Vertrag vom",
        "hr_eintragung_am": "HR-Eintragung am",
        "wirksam_ab": "Wirksam ab",
        "mindestlaufzeit_bis": "Mindestlaufzeit bis",
        "bemerkung": "Bemerkung",
    },
    "auslandsbezug": {
        "art": "Art",
        "bezeichnung": "Bezeichnung",
        "land": "Land",
        "quote_prozent": "Quote (%)",
        "status": "Status",
        "seit": "Seit",
        "bemerkung": "Bemerkung",
    },
    "veranlagung": {
        "jahr": "Jahr",
        "steuerart": "Steuerart",
        "bescheidart": "Bescheidart",
        "stand": "Stand",
        "erklaerung_am": "Erklärung am",
        "bescheid_vom": "Bescheid vom",
        "vorbehalt_164": "Vorbehalt § 164 AO",
        "vorlaeufig_165": "Vorläufig § 165 AO",
        "vorlaeufig_punkt": "Vorläufigkeit (Punkt)",
        "vorlaeufig_az": "Vorläufigkeit (Aktenzeichen)",
        "abweichung_von_erklaerung": "Abweichung von der Erklärung",
        "erweiterte_kuerzung": "Erweiterte Kürzung",
        "festsetzungsfrist_ende": "Ende der Festsetzungsfrist",
        "ablaufhemmung": "Ablaufhemmung",
        "verjaehrt": "Verjährt",
    },
    "rechtsbehelf": {
        "art": "Art",
        "eingelegt_am": "Eingelegt am",
        "status": "Status",
        "ruht_rechtsgrundlage": "Ruht nach",
        "musterverfahren_gericht": "Musterverfahren (Gericht)",
        "musterverfahren_az": "Musterverfahren (Aktenzeichen)",
        "streitpunkt": "Streitpunkt",
        "auswirkung_eur": "Auswirkung (EUR)",
        "veranlagung_jahr": "Veranlagungsjahr",
        "veranlagung_steuerart": "Steuerart der Veranlagung",
    },
    "aussenpruefung": {
        "status": "Status",
        "steuerarten": "Steuerarten",
        "zeitraum_von": "Zeitraum von",
        "zeitraum_bis": "Zeitraum bis",
        "anordnung_vom": "Anordnung vom",
        "beginn_am": "Beginn am",
        "bericht_vom": "Bericht vom",
        "schwerpunkte": "Schwerpunkte",
    },
    "pruefungsfeststellung": {
        "tz": "Tz.",
        "thema": "Thema",
        "steuerart": "Steuerart",
        "betrag_eur": "Betrag (EUR)",
        "pruefung_zeitraum_von": "Prüfungszeitraum von",
        "pruefung_zeitraum_bis": "Prüfungszeitraum bis",
    },
    "steuerattribut": {
        "art": "Art",
        "stichtag": "Stichtag",
        "betrag_eur": "Betrag (EUR)",
        "festgestellt": "Festgestellt",
        "sperre": "Sperre",
    },
    "darlehen": {
        "darlehensgeber": "Darlehensgeber",
        "beziehung": "Beziehung",
        "betrag_eur": "Betrag (EUR)",
        "zinssatz_prozent": "Zinssatz (%)",
        "beginn_am": "Beginn am",
        "faellig_am": "Fällig am",
        "status": "Status",
        "beendet_am": "Beendet am",
        "abgetreten_an": "Abgetreten an",
        "abgetreten_am": "Abgetreten am",
    },
    "nutzungsvertrag": {
        "art": "Art",
        "vertragspartner": "Vertragspartner",
        "nahestehend": "Nahestehend",
        "objekt": "Objekt",
        "jahresentgelt_eur": "Jahresentgelt (EUR)",
        "davon_unbeweglich": "davon unbeweglich (EUR)",
        "davon_beweglich": "davon beweglich (EUR)",
        "beginn_am": "Beginn am",
        "ende_am": "Ende am",
    },
    "grundstueck": {
        "objekt": "Objekt",
        "adresse": "Adresse",
        "eigentum_status": "Eigentum",
        "grundbuch": "Grundbuch",
        "flurstueck": "Flurstück",
        "flaeche_qm": "Fläche (m²)",
        "buchwert_eur": "Buchwert (EUR)",
        "einheitswert_eur": "Einheitswert (EUR)",
        "grundsteuerwert_eur": "Grundsteuerwert (EUR)",
    },
    "umwandlung": {
        "einbringungszeitpunkt": "Einbringungszeitpunkt",
        "art": "Art",
        "gegenstand": "Gegenstand",
        "einbringende": "Einbringende",
        "wertansatz": "Wertansatz",
        "buchwert_eur": "Buchwert (EUR)",
        "gemeiner_wert_eur": "Gemeiner Wert (EUR)",
        "sperrfrist_norm": "Sperrfrist (Norm)",
        "sperrfrist_ende": "Sperrfrist bis",
        "sperrfrist_status": "Sperrfrist (Status)",
        "ausgeloest_am": "Ausgelöst am",
        "einbringungsgewinn_eur": "Einbringungsgewinn (EUR)",
    },
    "ust_steuerfrei": {
        "jahr": "Jahr",
        "norm_code": "Norm",
        "beschreibung": "Beschreibung",
        "betrag_eur": "Betrag (EUR)",
        "vorsteuerabzug_erhalten": "Vorsteuerabzug erhalten",
    },
    "finding": {
        "finding_code": "Nr.",
        "report_ref": "Fundstelle im Bericht",
        "titel": "Titel",
        "steuerarten": "Steuerarten",
        "jahr_von": "Jahr von",
        "jahr_bis": "Jahr bis",
        "kurzsachverhalt": "Kurzsachverhalt",
        "risiko": "Risiko",
        "richtung": "Richtung",
        "betrag_min_eur": "Betrag min. (EUR)",
        "betrag_max_eur": "Betrag max. (EUR)",
        "offene_rechtsfrage": "Offene Rechtsfrage",
        "musterverfahren_az": "Musterverfahren (Aktenzeichen)",
        "status": "Status",
        "empfehlung": "Empfehlung",
        "normen": "Normen",
    },
    "spa_absicherung": {
        "art": "Art",
        "fundstelle_spa": "Fundstelle im SPA",
        "betrag_eur": "Betrag (EUR)",
        "frist_ende": "Frist bis",
        "beschreibung": "Beschreibung",
        "finding_code": "Feststellung (Nr.)",
    },
}

# Profile fields that fill the thin company core instead of becoming facts.
CORE_FIELDS: dict[str, str] = {
    "name": "name",
    "rechtsform": "legalForm",
    "wz_bezeichnung": "industry",
    "ansprechpartner_email": "contact.email",
}

# Client data kept next to the profile (pipeline.client_info), with audit labels.
CLIENT_LABELS: dict[str, str] = {
    "customerNumber": "Kundennummer",
    "clientSince": "Mandant seit",
    "contact.salutation": "Anrede Ansprechpartner",
    "contact.name": "Ansprechpartner",
    "contact.phone": "Telefon Ansprechpartner",
}


def field_label(name: str) -> str:
    return FIELD_LABELS.get(name, name)


def column_label(field: str, column: str) -> str:
    return COLUMN_LABELS.get(field, {}).get(column, column)


def column_for_label(field: str, label: str) -> str:
    """The column of a display label (the label itself when it is a column name)."""
    for column, text in COLUMN_LABELS.get(field, {}).items():
        if text == label:
            return column
    return label
