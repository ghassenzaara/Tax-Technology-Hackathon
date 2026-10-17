-- =====================================================================
-- Tax Company Profile – Datenbankschema (PostgreSQL 14+)
-- Prototyp-Umfang: Blöcke A, B, C, D, Extras E01/E03/E05/E06/G02, K, L, M
-- Fiktive Testdaten: Tax Tech Hackathon
-- =====================================================================
DROP SCHEMA IF EXISTS profil CASCADE;
CREATE SCHEMA profil;
SET search_path = profil, public;

-- ---------------------------------------------------------------------
-- Grundtypen
-- ---------------------------------------------------------------------
-- Drei Zustände: „unbekannt“ ist nie „nein“ (Schweigen der Quelle = unbekannt)
CREATE TYPE tri_state AS ENUM ('ja', 'nein', 'unbekannt');
-- Konfidenz der Extraktion durch den Agenten (Block M)
CREATE TYPE konfidenz AS ENUM ('hoch', 'mittel', 'niedrig');
CREATE TYPE rolle     AS ENUM ('operativ', 'holding', 'immobilien', 'finanzierung', 'ip', 'erwerbsgesellschaft');
CREATE TYPE steuerart AS ENUM ('KSt', 'GewSt', 'USt', 'GrESt', 'ESt', 'LSt');

-- ---------------------------------------------------------------------
-- Vokabular (feste Liste – dieselbe wird für das Urteils-Tagging genutzt)
-- ---------------------------------------------------------------------
CREATE TABLE dokument_klasse (
    code         text PRIMARY KEY,               -- z. B. 'bescheid', 'dd_report'
    bezeichnung  text NOT NULL,
    rang         smallint NOT NULL CHECK (rang BETWEEN 1 AND 9)  -- 1 = höchste Beweiskraft
);

CREATE TABLE norm (
    code         text PRIMARY KEY,               -- z. B. '§ 8c KStG'
    gesetz       text NOT NULL,                  -- 'KStG'
    bezeichnung  text
);

CREATE TABLE merkmal_def (
    code         text PRIMARY KEY,               -- Feld-ID aus dem Feldkatalog, z. B. 'B04'
    block        char(1) NOT NULL,
    bezeichnung  text NOT NULL,
    feldtyp      text NOT NULL CHECK (feldtyp IN ('stammdatum','merkmal','attribut','liste','frist','finding')),
    prio         text NOT NULL CHECK (prio IN ('P1','P2','P3')),
    dynamik      text NOT NULL CHECK (dynamik IN ('statisch','jaehrlich','ereignis')),
    prototyp     boolean NOT NULL DEFAULT true,
    zieltabelle  text                            -- wo die Details liegen
);

CREATE TABLE merkmal_norm (
    merkmal_code text REFERENCES merkmal_def(code) ON DELETE CASCADE,
    norm_code    text REFERENCES norm(code),
    PRIMARY KEY (merkmal_code, norm_code)
);

-- ---------------------------------------------------------------------
-- Deal und Quelldokumente
-- ---------------------------------------------------------------------
CREATE TABLE deal (                               -- A01, A02
    id                     serial PRIMARY KEY,
    projektname            text NOT NULL UNIQUE,
    kaeufer                text NOT NULL,
    verkaeufer             text,
    deal_typ               text NOT NULL CHECK (deal_typ IN ('share_deal','asset_deal')),
    signing_am             date,
    closing_am             date,
    kaufpreis_eur          numeric(15,2),
    locked_box_stichtag    date,
    dd_art                 text CHECK (dd_art IN ('buy_side','vendor')),
    dd_umfang              text CHECK (dd_umfang IN ('full_scope','red_flag')),
    dd_ersteller           text,
    dd_report_am           date,
    dd_datenraum_stichtag  date,
    dd_zeitraum_von        smallint,
    dd_zeitraum_bis        smallint,
    dd_sprache             char(2)
);

CREATE TABLE dokument (
    id               serial PRIMARY KEY,
    deal_id          int NOT NULL REFERENCES deal(id) ON DELETE CASCADE,
    dok_nr           text NOT NULL,              -- '01' … '17'
    titel            text NOT NULL,
    dateiname        text,
    klasse_code      text NOT NULL REFERENCES dokument_klasse(code),
    dokument_datum   date,
    hochgeladen_am   date,
    sprache          char(2) DEFAULT 'de',
    UNIQUE (deal_id, dok_nr)
);

-- Provenienz (Block M) wird in jeder Faktentabelle mit denselben Spalten geführt:
--   quelle_dok, fundstelle, stand, konfidenz, bestaetigt_von, bestaetigt_am, gueltig_bis

-- ---------------------------------------------------------------------
-- Block A – Gesellschaft (ein Profil pro Rechtsträger)
-- ---------------------------------------------------------------------
CREATE TABLE gesellschaft (                       -- A04–A07 + Profil-Metadaten
    id                       serial PRIMARY KEY,
    deal_id                  int NOT NULL REFERENCES deal(id) ON DELETE CASCADE,
    name                     text NOT NULL,
    strasse                  text,
    plz                      text,
    ort                      text,
    land                     char(2) NOT NULL DEFAULT 'DE',
    registergericht          text,
    registernummer           text,
    rechtsform               text NOT NULL,      -- A05
    rolle                    rolle NOT NULL,     -- A06
    wz_code                  text,               -- A07
    wz_bezeichnung           text,
    steuernummer             text,
    finanzamt                text,
    ist_zielgesellschaft     boolean NOT NULL DEFAULT false,
    verantwortlicher_stb     text,               -- M (Profil)
    ansprechpartner_mandant  text,               -- M (Profil)
    profil_version           int NOT NULL DEFAULT 1,
    letzter_refresh          date,
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date,
    UNIQUE (deal_id, name)
);

-- Tri-State-Merkmale je Gesellschaft (B04, B05, B07, C04, C09, E01, E03, E05, E05b, E06, G02)
-- Details stehen in den jeweiligen Listentabellen; hier steht die matchbare Ja/Nein/Unbekannt-Aussage.
CREATE TABLE merkmal_wert (
    id               serial PRIMARY KEY,
    gesellschaft_id  int NOT NULL REFERENCES gesellschaft(id) ON DELETE CASCADE,
    merkmal_code     text NOT NULL REFERENCES merkmal_def(code),
    wert             tri_state NOT NULL,
    kurztext         text,
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date,
    UNIQUE (gesellschaft_id, merkmal_code)
);

-- ---------------------------------------------------------------------
-- Block B – Struktur
-- ---------------------------------------------------------------------
CREATE TABLE beteiligung (                        -- B01 (aktuell = gueltig_bis IS NULL)
    id                        serial PRIMARY KEY,
    gesellschaft_id           int NOT NULL REFERENCES gesellschaft(id) ON DELETE CASCADE,
    gesellschafter            text NOT NULL,
    gesellschafter_art        text NOT NULL CHECK (gesellschafter_art IN ('natuerliche_person','kapitalgesellschaft','personengesellschaft','fonds')),
    gesellschafter_ges_id     int REFERENCES gesellschaft(id),   -- falls Gesellschafter selbst im Profil
    sitz_wohnort              text,
    quote_prozent             numeric(7,4) NOT NULL CHECK (quote_prozent > 0 AND quote_prozent <= 100),
    anteile                   text,              -- lfd. Nummern
    gueltig_ab                date,
    gueltig_bis               date,
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis_meta date
);

CREATE TABLE anteilswechsel (                     -- B02 (10 Jahre, inkl. Deal)
    id               serial PRIMARY KEY,
    gesellschaft_id  int NOT NULL REFERENCES gesellschaft(id) ON DELETE CASCADE,
    datum            date NOT NULL,
    vorgang          text NOT NULL CHECK (vorgang IN ('gruendung','abtretung','kapitalerhoehung','anteilstausch','mittelbarer_wechsel','schenkung')),
    quote_prozent    numeric(7,4),
    abgebender       text,
    erwerber         text,
    mittelbar        boolean NOT NULL DEFAULT false,
    beschreibung     text,
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date
);

CREATE TABLE organschaft (                        -- B04 (kst_gewst), B05 (ust)
    id                   serial PRIMARY KEY,
    art                  text NOT NULL CHECK (art IN ('kst_gewst','ust')),
    status               text NOT NULL CHECK (status IN ('besteht','geplant','beendet')),
    organtraeger_id      int REFERENCES gesellschaft(id),
    organtraeger_name    text NOT NULL,
    organgesellschaft_id int REFERENCES gesellschaft(id),
    organgesellschaft_name text NOT NULL,
    vertrag_am           date,                   -- GAV (nur kst_gewst)
    hr_eintragung_am     date,
    wirksam_ab           date,
    mindestlaufzeit_bis  date,
    bemerkung            text,
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date
);

CREATE TABLE auslandsbezug (                      -- B07
    id               serial PRIMARY KEY,
    gesellschaft_id  int NOT NULL REFERENCES gesellschaft(id) ON DELETE CASCADE,
    art              text NOT NULL CHECK (art IN ('tochtergesellschaft','betriebsstaette')),
    bezeichnung      text NOT NULL,
    land             char(2) NOT NULL,
    quote_prozent    numeric(7,4),
    status           tri_state NOT NULL,         -- 'unbekannt' für ungeklärte Betriebsstätten
    seit             date,
    bemerkung        text,
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date
);

-- ---------------------------------------------------------------------
-- Block C – Verfahrensstand
-- ---------------------------------------------------------------------
CREATE TABLE veranlagung (                        -- C01, C02, C03, C06 (pro Gesellschaft × Jahr × Steuerart)
    id                        serial PRIMARY KEY,
    gesellschaft_id           int NOT NULL REFERENCES gesellschaft(id) ON DELETE CASCADE,
    jahr                      smallint NOT NULL,
    steuerart                 steuerart NOT NULL,
    bescheidart               text NOT NULL CHECK (bescheidart IN ('festsetzung','messbetrag','feststellung_14_5','anmeldung_168')),
    stand                     text NOT NULL CHECK (stand IN ('nicht_erklaert','erklaert','veranlagt')),
    erklaerung_am             date,
    bescheid_vom              date,
    vorbehalt_164             text NOT NULL CHECK (vorbehalt_164 IN ('ja','nein','aufgehoben','entfallen')),
    vorlaeufig_165            boolean NOT NULL DEFAULT false,
    vorlaeufig_punkt          text,
    vorlaeufig_az             text,              -- Aktenzeichen des Musterverfahrens
    abweichung_von_erklaerung text,
    erweiterte_kuerzung       text CHECK (erweiterte_kuerzung IN ('nicht_beantragt','beantragt_gewaehrt','beantragt_versagt')),
    festsetzungsfrist_ende    date,              -- C06
    ablaufhemmung             text CHECK (ablaufhemmung IN ('einspruch_171_3a','aussenpruefung_171_4')),
    verjaehrt                 boolean NOT NULL DEFAULT false,
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand_meta date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date,
    UNIQUE (gesellschaft_id, jahr, steuerart, bescheidart)
);

CREATE TABLE rechtsbehelf (                       -- C04, C05
    id                       serial PRIMARY KEY,
    gesellschaft_id          int NOT NULL REFERENCES gesellschaft(id) ON DELETE CASCADE,
    veranlagung_id           int REFERENCES veranlagung(id),
    art                      text NOT NULL CHECK (art IN ('einspruch','klage')),
    eingelegt_am             date NOT NULL,
    status                   text NOT NULL CHECK (status IN ('anhaengig','ruht','erledigt')),
    ruht_rechtsgrundlage     text,
    musterverfahren_gericht  text,
    musterverfahren_az       text,               -- direkter Matching-Schlüssel
    streitpunkt              text,
    auswirkung_eur           numeric(15,2),
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date
);

CREATE TABLE aussenpruefung (                     -- C07 (letzte), C09 (laufend/angeordnet)
    id               serial PRIMARY KEY,
    gesellschaft_id  int NOT NULL REFERENCES gesellschaft(id) ON DELETE CASCADE,
    status           text NOT NULL CHECK (status IN ('abgeschlossen','angeordnet','laufend')),
    steuerarten      steuerart[] NOT NULL,
    zeitraum_von     smallint NOT NULL,
    zeitraum_bis     smallint NOT NULL,
    anordnung_vom    date,
    beginn_am        date,
    bericht_vom      date,
    schwerpunkte     text[],
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date
);

CREATE TABLE pruefungsfeststellung (              -- C08
    id                 serial PRIMARY KEY,
    aussenpruefung_id  int NOT NULL REFERENCES aussenpruefung(id) ON DELETE CASCADE,
    tz                 text NOT NULL,
    thema              text NOT NULL,
    steuerart          steuerart,
    betrag_eur         numeric(15,2),
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date
);
CREATE TABLE pruefungsfeststellung_norm (
    feststellung_id int REFERENCES pruefungsfeststellung(id) ON DELETE CASCADE,
    norm_code       text REFERENCES norm(code),
    PRIMARY KEY (feststellung_id, norm_code)
);

-- ---------------------------------------------------------------------
-- Block D – Steuerattribute
-- ---------------------------------------------------------------------
CREATE TABLE steuerattribut (                     -- D01, D02, D04
    id               serial PRIMARY KEY,
    gesellschaft_id  int NOT NULL REFERENCES gesellschaft(id) ON DELETE CASCADE,
    art              text NOT NULL CHECK (art IN ('kst_verlustvortrag','gewst_fehlbetrag','zinsvortrag','ebitda_vortrag')),
    stichtag         date NOT NULL,
    betrag_eur       numeric(15,2) NOT NULL,
    festgestellt     boolean NOT NULL,           -- Feststellungsbescheid vorhanden?
    sperre           text,                       -- z. B. 'vororganschaftlich, § 15 S. 1 Nr. 1 KStG'
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date,
    UNIQUE (gesellschaft_id, art, stichtag)
);

-- ---------------------------------------------------------------------
-- Extras E01, E03, E05, E06, G02
-- ---------------------------------------------------------------------
CREATE TABLE darlehen (                           -- E01
    id                serial PRIMARY KEY,
    darlehensnehmer_id int NOT NULL REFERENCES gesellschaft(id) ON DELETE CASCADE,
    darlehensgeber    text NOT NULL,
    beziehung         text NOT NULL CHECK (beziehung IN ('gesellschafter','nahestehend','konzerngesellschaft')),
    betrag_eur        numeric(15,2) NOT NULL,
    zinssatz_prozent  numeric(6,3),
    beginn_am         date,
    faellig_am        date,
    status            text NOT NULL CHECK (status IN ('laufend','zurueckgezahlt')),
    beendet_am        date,
    abgetreten_an     text,
    abgetreten_am     date,
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date
);

CREATE TABLE nutzungsvertrag (                    -- E03 (Gesellschaft als Mieterin/Leasingnehmerin)
    id                  serial PRIMARY KEY,
    gesellschaft_id     int NOT NULL REFERENCES gesellschaft(id) ON DELETE CASCADE,
    art                 text NOT NULL CHECK (art IN ('miete','leasing')),
    vertragspartner     text NOT NULL,
    nahestehend         boolean NOT NULL DEFAULT false,
    objekt              text,
    jahresentgelt_eur   numeric(15,2) NOT NULL,
    davon_unbeweglich   numeric(15,2),           -- Hinzurechnung 50 %
    davon_beweglich     numeric(15,2),           -- Hinzurechnung 20 %
    beginn_am           date,
    ende_am             date,
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date
);

CREATE TABLE grundstueck (                        -- E05
    id                 serial PRIMARY KEY,
    eigentuemer_id     int REFERENCES gesellschaft(id),
    nutzer_id          int REFERENCES gesellschaft(id),
    objekt             text NOT NULL,
    adresse            text,
    eigentum_status    text NOT NULL CHECK (eigentum_status IN ('eigentum','miete','ungeklaert')),
    grundbuch          text,
    flurstueck         text,
    flaeche_qm         numeric(12,2),
    buchwert_eur       numeric(15,2),
    einheitswert_eur   numeric(15,2),
    grundsteuerwert_eur numeric(15,2),
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date
);

CREATE TABLE umwandlung (                         -- E06
    id                     serial PRIMARY KEY,
    gesellschaft_id        int NOT NULL REFERENCES gesellschaft(id) ON DELETE CASCADE,  -- übernehmende Gesellschaft
    einbringungszeitpunkt  date NOT NULL,
    art                    text NOT NULL CHECK (art IN ('einbringung_20','anteilstausch_21','einbringung_24','verschmelzung','spaltung','formwechsel')),
    gegenstand             text NOT NULL,
    einbringende           text,
    wertansatz             text CHECK (wertansatz IN ('buchwert','zwischenwert','gemeiner_wert')),
    buchwert_eur           numeric(15,2),
    gemeiner_wert_eur      numeric(15,2),
    sperrfrist_norm        text,
    sperrfrist_ende        date,
    sperrfrist_status      text CHECK (sperrfrist_status IN ('laeuft','abgelaufen','ausgeloest')),
    ausgeloest_am          date,
    einbringungsgewinn_eur numeric(15,2),
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date
);

CREATE TABLE ust_steuerfrei (                     -- G02
    id                       serial PRIMARY KEY,
    gesellschaft_id          int NOT NULL REFERENCES gesellschaft(id) ON DELETE CASCADE,
    jahr                     smallint NOT NULL,
    norm_code                text NOT NULL REFERENCES norm(code),
    beschreibung             text,
    betrag_eur               numeric(15,2),
    vorsteuerabzug_erhalten  boolean NOT NULL,   -- true = vorsteuerunschädlich
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date
);

-- ---------------------------------------------------------------------
-- Block K – Risikoregister und SPA-Absicherung
-- ---------------------------------------------------------------------
CREATE TABLE finding (                            -- K01–K12
    id                  serial PRIMARY KEY,
    deal_id             int NOT NULL REFERENCES deal(id) ON DELETE CASCADE,
    gesellschaft_id     int REFERENCES gesellschaft(id),
    finding_code        text NOT NULL,           -- K-1 …
    report_ref          text,                    -- 'Feststellung 2', 'TX-01'
    titel               text NOT NULL,
    steuerarten         steuerart[] NOT NULL,
    jahr_von            smallint,
    jahr_bis            smallint,
    kurzsachverhalt     text,
    risiko              text NOT NULL CHECK (risiko IN ('hoch','mittel','gering','sicher','hinweis')),
    richtung            text NOT NULL CHECK (richtung IN ('risiko','chance','beides')),
    betrag_min_eur      numeric(15,2),
    betrag_max_eur      numeric(15,2),
    offene_rechtsfrage  boolean NOT NULL DEFAULT false,
    musterverfahren_az  text,
    status              text NOT NULL CHECK (status IN ('offen','im_spa_gedeckt','mitigiert','realisiert','covenant')),
    empfehlung          text,
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date,
    UNIQUE (deal_id, finding_code)
);
CREATE TABLE finding_norm (
    finding_id int REFERENCES finding(id) ON DELETE CASCADE,
    norm_code  text REFERENCES norm(code),
    PRIMARY KEY (finding_id, norm_code)
);
CREATE TABLE finding_merkmal (
    finding_id   int REFERENCES finding(id) ON DELETE CASCADE,
    merkmal_code text REFERENCES merkmal_def(code),
    PRIMARY KEY (finding_id, merkmal_code)
);

CREATE TABLE spa_absicherung (                    -- K11 + Deal-Fristen für Block L
    id          serial PRIMARY KEY,
    deal_id     int NOT NULL REFERENCES deal(id) ON DELETE CASCADE,
    finding_id  int REFERENCES finding(id),
    art         text NOT NULL CHECK (art IN ('steuerfreistellung','spezialfreistellung','garantie','kaufpreis','escrow','wi_police','covenant')),
    fundstelle_spa text,
    betrag_eur  numeric(15,2),
    frist_ende  date,
    beschreibung text,
    quelle_dok int REFERENCES dokument(id), fundstelle text, stand date, konfidenz konfidenz,
    bestaetigt_von text, bestaetigt_am date, gueltig_bis date
);

-- ---------------------------------------------------------------------
-- Block M – Prüfhinweise (Konflikte, Plausibilität, offene Punkte)
-- ---------------------------------------------------------------------
CREATE TABLE pruefhinweis (
    id               serial PRIMARY KEY,
    gesellschaft_id  int REFERENCES gesellschaft(id) ON DELETE CASCADE,
    art              text NOT NULL CHECK (art IN ('konflikt','plausibilitaet','offen')),
    feld             text,
    beschreibung     text NOT NULL,
    quelle_a         int REFERENCES dokument(id),
    quelle_b         int REFERENCES dokument(id),
    entscheidung     text,
    status           text NOT NULL DEFAULT 'offen' CHECK (status IN ('offen','geklaert')),
    erstellt_am      date DEFAULT current_date
);

-- ---------------------------------------------------------------------
-- Hilfsfunktionen für Seeds und Agenten
-- ---------------------------------------------------------------------
CREATE FUNCTION deal_id(p text) RETURNS int LANGUAGE sql STABLE AS
$$ SELECT id FROM profil.deal WHERE projektname = p $$;
CREATE FUNCTION ges(p text) RETURNS int LANGUAGE sql STABLE AS
$$ SELECT id FROM profil.gesellschaft WHERE name = p $$;
CREATE FUNCTION dok(p_projekt text, p_nr text) RETURNS int LANGUAGE sql STABLE AS
$$ SELECT d.id FROM profil.dokument d JOIN profil.deal x ON x.id = d.deal_id
   WHERE x.projektname = p_projekt AND d.dok_nr = p_nr $$;

-- Indizes für den Abgleich
CREATE INDEX ON veranlagung (vorlaeufig_az) WHERE vorlaeufig_az IS NOT NULL;
CREATE INDEX ON rechtsbehelf (musterverfahren_az) WHERE musterverfahren_az IS NOT NULL;
CREATE INDEX ON finding (musterverfahren_az) WHERE musterverfahren_az IS NOT NULL;
CREATE INDEX ON merkmal_wert (merkmal_code, wert);
