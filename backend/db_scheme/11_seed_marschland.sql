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
-- =====================================================================
-- Festes Vokabular: Dokumentklassen (Rang), Normen, Merkmale (Prototyp)
-- =====================================================================
SET search_path = profil, public;

INSERT INTO dokument_klasse (code, bezeichnung, rang) VALUES
 ('bescheid',         'Steuer- und Feststellungsbescheide',                     1),
 ('register',         'Handelsregister, Gesellschafterlisten',                  1),
 ('vertrag',          'Gewinnabführungs-, Darlehens-, Miet-, Einbringungsverträge', 1),
 ('spa',              'Kaufvertrag (SPA) und Vollzugsprotokoll',                1),
 ('grundbuch',        'Grundbuchauszüge, Immobilienliste',                      1),
 ('aussenpruefung',   'Prüfungsanordnungen und Prüfungsberichte',               2),
 ('rechtsbehelf',     'Einsprüche, Ruhensmitteilungen, Korrespondenz FA',       2),
 ('jahresabschluss',  'Jahresabschlüsse, Steuerbilanzen',                       2),
 ('organigramm',      'Konzernorganigramme',                                    3),
 ('dd_report',        'Tax-Due-Diligence-Bericht',                              3),
 ('erklaerung',       'Steuererklärungen, ELSTER-Protokolle',                   4),
 ('qa_index',         'Datenraum-Index, Q&A-Log',                               5),
 ('entwurf',          'Entwürfe (nicht unterzeichnet) – nie als Beleg für Bestand', 9);

INSERT INTO norm (code, gesetz, bezeichnung) VALUES
 ('§ 8c KStG','KStG','Verlustuntergang bei Anteilsübertragung'),
 ('§ 8b KStG','KStG','Beteiligungserträge'),
 ('§ 8 Abs. 3 KStG','KStG','Verdeckte Gewinnausschüttung'),
 ('§ 14 KStG','KStG','Ertragsteuerliche Organschaft'),
 ('§ 15 KStG','KStG','Einkommensermittlung bei Organschaft'),
 ('§ 8a KStG','KStG','Zinsschranke (KSt)'),
 ('§ 10d EStG','EStG','Verlustabzug'),
 ('§ 4h EStG','EStG','Zinsschranke'),
 ('§ 6 Abs. 1 Nr. 1a EStG','EStG','Anschaffungsnahe Herstellungskosten'),
 ('§ 15 EStG','EStG','Einkünfte aus Gewerbebetrieb / Mitunternehmerschaft'),
 ('§ 50d Abs. 3 EStG','EStG','Missbrauchsabwehr Quellensteuer'),
 ('§ 2 Abs. 2 GewStG','GewStG','Gewerbesteuerliche Organschaft'),
 ('§ 8 Nr. 1 Buchst. d GewStG','GewStG','Hinzurechnung Miete bewegliche WG'),
 ('§ 8 Nr. 1 Buchst. e GewStG','GewStG','Hinzurechnung Miete unbewegliche WG'),
 ('§ 9 Nr. 1 S. 1 GewStG','GewStG','Einfache Grundstückskürzung'),
 ('§ 9 Nr. 1 S. 2 GewStG','GewStG','Erweiterte Grundstückskürzung'),
 ('§ 9 Nr. 1 S. 5 Nr. 1 GewStG','GewStG','Ausschluss erweiterte Kürzung (Gesellschafter)'),
 ('§ 10a GewStG','GewStG','Gewerbeverlust'),
 ('§ 68 Abs. 2 Nr. 2 BewG','BewG','Betriebsvorrichtungen'),
 ('§ 20 UmwStG','UmwStG','Einbringung in Kapitalgesellschaft'),
 ('§ 21 UmwStG','UmwStG','Anteilstausch'),
 ('§ 22 Abs. 1 UmwStG','UmwStG','Einbringungsgewinn I'),
 ('§ 22 Abs. 2 UmwStG','UmwStG','Einbringungsgewinn II'),
 ('§ 23 Abs. 2 UmwStG','UmwStG','Buchwertaufstockung'),
 ('§ 1 AStG','AStG','Verrechnungspreise'),
 ('§ 1 Abs. 2b GrEStG','GrEStG','Anteilsübertragung Kapitalgesellschaft ≥ 90 %'),
 ('§ 1 Abs. 3 GrEStG','GrEStG','Anteilsvereinigung'),
 ('§ 13 Nr. 7 GrEStG','GrEStG','Steuerschuldner bei § 1 Abs. 2b'),
 ('§ 2 Abs. 2 Nr. 2 UStG','UStG','Umsatzsteuerliche Organschaft'),
 ('§ 4 Nr. 3 UStG','UStG','Steuerbefreiung grenzüberschreitende Güterbeförderung'),
 ('§ 4 Nr. 12 UStG','UStG','Steuerbefreiung Vermietung'),
 ('§ 4 Nr. 16 UStG','UStG','Steuerbefreiung Pflege'),
 ('§ 15 Abs. 4 UStG','UStG','Vorsteueraufteilung'),
 ('§ 302 AktG','AktG','Verlustübernahme'),
 ('§ 90 Abs. 3 AO','AO','Verrechnungspreisdokumentation'),
 ('§ 162 Abs. 4 AO','AO','Zuschläge bei Dokumentationsmängeln'),
 ('§ 149 AO','AO','Abgabe der Steuererklärung'),
 ('§ 164 AO','AO','Vorbehalt der Nachprüfung'),
 ('§ 165 AO','AO','Vorläufige Steuerfestsetzung'),
 ('§ 169 AO','AO','Festsetzungsfrist'),
 ('§ 171 AO','AO','Ablaufhemmung'),
 ('§ 173 Abs. 2 AO','AO','Änderungssperre nach Außenprüfung'),
 ('§ 196 AO','AO','Prüfungsanordnung'),
 ('§ 347 AO','AO','Einspruch'),
 ('§ 363 Abs. 2 AO','AO','Ruhen des Verfahrens'),
 ('DBA','DBA','Doppelbesteuerungsabkommen');

INSERT INTO merkmal_def (code, block, bezeichnung, feldtyp, prio, dynamik, zieltabelle) VALUES
 ('A01','A','Deal-Typ, Signing, Closing','stammdatum','P1','statisch','deal'),
 ('A02','A','DD-Art und Stand','stammdatum','P1','statisch','deal'),
 ('A04','A','Name, Sitz, Land','stammdatum','P1','statisch','gesellschaft'),
 ('A05','A','Rechtsform','stammdatum','P1','ereignis','gesellschaft'),
 ('A06','A','Rolle im Konzern','stammdatum','P1','ereignis','gesellschaft'),
 ('A07','A','Branche (WZ-Code)','stammdatum','P1','statisch','gesellschaft'),
 ('B01','B','Gesellschafter mit Quote','liste','P1','ereignis','beteiligung'),
 ('B02','B','Anteilswechsel der letzten 10 Jahre','liste','P1','ereignis','anteilswechsel'),
 ('B04','B','KSt-/GewSt-Organschaft','merkmal','P1','ereignis','organschaft'),
 ('B05','B','USt-Organschaft','merkmal','P1','ereignis','organschaft'),
 ('B07','B','Ausländische Töchter und Betriebsstätten','merkmal','P1','ereignis','auslandsbezug'),
 ('C01','C','Veranlagungsstand','liste','P1','jaehrlich','veranlagung'),
 ('C02','C','Vorbehalt der Nachprüfung','merkmal','P1','jaehrlich','veranlagung'),
 ('C03','C','Vorläufigkeitsvermerke','liste','P1','jaehrlich','veranlagung'),
 ('C04','C','Offene Einsprüche und Klagen','merkmal','P1','ereignis','rechtsbehelf'),
 ('C05','C','Ruhende Verfahren mit Musterverfahren','liste','P1','ereignis','rechtsbehelf'),
 ('C06','C','Festsetzungsverjährung','frist','P1','jaehrlich','veranlagung'),
 ('C07','C','Letzte Betriebsprüfung','liste','P1','ereignis','aussenpruefung'),
 ('C08','C','Feststellungen der letzten Prüfung','liste','P1','ereignis','pruefungsfeststellung'),
 ('C09','C','Laufende oder angeordnete Prüfung','merkmal','P1','ereignis','aussenpruefung'),
 ('D01','D','KSt-Verlustvortrag','attribut','P1','jaehrlich','steuerattribut'),
 ('D02','D','GewSt-Fehlbetrag','attribut','P1','jaehrlich','steuerattribut'),
 ('D04','D','Zins- und EBITDA-Vortrag','attribut','P1','jaehrlich','steuerattribut'),
 ('E01','E','Darlehen von Gesellschaftern/Nahestehenden','merkmal','P1','jaehrlich','darlehen'),
 ('E03','E','Wesentliche Mieten, Pachten, Leasing','merkmal','P1','jaehrlich','nutzungsvertrag'),
 ('E05','E','Grundbesitz im Betriebsvermögen','merkmal','P1','ereignis','grundstueck'),
 ('E05b','E','Erweiterte Kürzung beansprucht','merkmal','P1','jaehrlich','veranlagung'),
 ('E06','E','Umwandlungen/Einbringungen 7 Jahre','merkmal','P1','ereignis','umwandlung'),
 ('G02','G','Steuerfreie Ausgangsumsätze','merkmal','P1','jaehrlich','ust_steuerfrei'),
 ('K','K','Risikoregister','finding','P1','ereignis','finding');

INSERT INTO merkmal_norm (merkmal_code, norm_code) VALUES
 ('A05','§ 15 EStG'),('A06','§ 8b KStG'),('A06','§ 9 Nr. 1 S. 2 GewStG'),
 ('B01','§ 8c KStG'),('B01','§ 1 Abs. 2b GrEStG'),('B02','§ 8c KStG'),('B02','§ 1 Abs. 2b GrEStG'),('B02','§ 1 Abs. 3 GrEStG'),
 ('B04','§ 14 KStG'),('B04','§ 2 Abs. 2 GewStG'),('B04','§ 302 AktG'),('B05','§ 2 Abs. 2 Nr. 2 UStG'),('B07','DBA'),('B07','§ 1 AStG'),
 ('C01','§ 149 AO'),('C02','§ 164 AO'),('C03','§ 165 AO'),('C04','§ 347 AO'),('C05','§ 363 Abs. 2 AO'),('C06','§ 169 AO'),('C06','§ 171 AO'),
 ('C07','§ 173 Abs. 2 AO'),('C09','§ 196 AO'),
 ('D01','§ 10d EStG'),('D01','§ 8c KStG'),('D02','§ 10a GewStG'),('D04','§ 4h EStG'),('D04','§ 8a KStG'),
 ('E01','§ 8 Abs. 3 KStG'),('E01','§ 1 AStG'),('E03','§ 8 Nr. 1 Buchst. d GewStG'),('E03','§ 8 Nr. 1 Buchst. e GewStG'),
 ('E05','§ 9 Nr. 1 S. 1 GewStG'),('E05b','§ 9 Nr. 1 S. 2 GewStG'),('E05b','§ 9 Nr. 1 S. 5 Nr. 1 GewStG'),('E05b','§ 68 Abs. 2 Nr. 2 BewG'),
 ('E06','§ 20 UmwStG'),('E06','§ 21 UmwStG'),('E06','§ 22 Abs. 1 UmwStG'),('E06','§ 22 Abs. 2 UmwStG'),
 ('G02','§ 4 Nr. 3 UStG'),('G02','§ 4 Nr. 12 UStG'),('G02','§ 4 Nr. 16 UStG'),('G02','§ 15 Abs. 4 UStG');
-- =====================================================================
-- Scan-Teil (Urteile, Embeddings, Alerts) und Sichten (Block L u. a.)
-- =====================================================================
SET search_path = profil, public;

-- ---------------------------------------------------------------------
-- Urteile – verschlagwortet mit DEMSELBEN Vokabular wie das Profil
-- ---------------------------------------------------------------------
CREATE TABLE urteil (
    id                   serial PRIMARY KEY,
    gericht              text NOT NULL CHECK (gericht IN ('BFH','EuGH','BVerfG','FG')),
    aktenzeichen         text NOT NULL UNIQUE,
    datum                date,
    status               text NOT NULL CHECK (status IN ('anhaengig','rechtskraeftig','vorlage')),
    steuerarten          steuerart[] NOT NULL,
    richtung             text CHECK (richtung IN ('zugunsten','zulasten','neutral')),
    zeitliche_anwendung  text,
    verwaltung_reaktion  text CHECK (verwaltung_reaktion IN ('bstbl_veroeffentlicht','nichtanwendungserlass','bmf_schreiben','keine')),
    rechtsformen         text[],
    rollen               rolle[],
    kernaussage          text,
    quelle_url           text,
    erfasst_am           date DEFAULT current_date
);
CREATE TABLE urteil_norm (
    urteil_id int REFERENCES urteil(id) ON DELETE CASCADE,
    norm_code text REFERENCES norm(code),
    PRIMARY KEY (urteil_id, norm_code)
);
CREATE TABLE urteil_merkmal (
    urteil_id    int REFERENCES urteil(id) ON DELETE CASCADE,
    merkmal_code text REFERENCES merkmal_def(code),
    PRIMARY KEY (urteil_id, merkmal_code)
);
-- Ein Urteil enthält oft mehrere Rechtsfragen → ein Vektor pro Rechtsfrage
CREATE TABLE urteil_rechtsfrage (
    id          serial PRIMARY KEY,
    urteil_id   int NOT NULL REFERENCES urteil(id) ON DELETE CASCADE,
    nr          smallint NOT NULL,
    leitsatz    text NOT NULL,
    normalisiert text            -- Sachverhalt + Norm + Rechtsfolge, gleiches Format wie Profilseite
);

-- ---------------------------------------------------------------------
-- Embeddings auf Feldebene (Findings, Merkmale, Rechtsfragen).
-- Hinweis: Mit pgvector die Spalte als vector(1536) anlegen und einen HNSW-Index setzen.
-- Merkmale mit Wert 'nein' werden NICHT embedded.
-- ---------------------------------------------------------------------
CREATE TABLE embedding (
    id           serial PRIMARY KEY,
    objekt_typ   text NOT NULL CHECK (objekt_typ IN ('finding','merkmal_wert','rechtsbehelf','urteil_rechtsfrage')),
    objekt_id    int NOT NULL,
    gesellschaft_id int REFERENCES gesellschaft(id) ON DELETE CASCADE,  -- Filter-Metadaten
    steuerarten  steuerart[],
    normen       text[],
    jahre        int4range,
    offen        boolean,                         -- Jahr noch änderbar?
    modell       text NOT NULL,
    vektor       real[] NOT NULL,
    text_basis   text NOT NULL,
    erstellt_am  timestamptz DEFAULT now(),
    UNIQUE (objekt_typ, objekt_id, modell)
);

-- ---------------------------------------------------------------------
-- Alerts (Dashboard) und Alert-Historie (Block M)
-- ---------------------------------------------------------------------
CREATE TABLE alert (
    id                 serial PRIMARY KEY,
    urteil_id          int REFERENCES urteil(id),
    gesellschaft_id    int NOT NULL REFERENCES gesellschaft(id) ON DELETE CASCADE,
    finding_id         int REFERENCES finding(id),
    ausloeser          text NOT NULL CHECK (ausloeser IN ('urteil','profil_update','frist')),
    match_typ          text CHECK (match_typ IN ('aktenzeichen','regel','semantisch')),
    aehnlichkeit       numeric(5,4),
    handlungsfaehig    boolean,
    frist              date,
    materialitaet_eur  numeric(15,2),
    begruendung        text,
    status             text NOT NULL DEFAULT 'neu' CHECK (status IN ('neu','bestaetigt','verworfen','versendet')),
    verworfen_grund    text,
    bearbeitet_von     text,
    bearbeitet_am      timestamptz,
    termin_vorschlag   timestamptz,
    erstellt_am        timestamptz DEFAULT now(),
    UNIQUE (urteil_id, gesellschaft_id, finding_id)
);

-- ---------------------------------------------------------------------
-- Sichten
-- ---------------------------------------------------------------------
-- B01: aktuelle Gesellschafter
CREATE VIEW v_gesellschafter_aktuell AS
SELECT g.name AS gesellschaft, b.gesellschafter, b.quote_prozent, b.gueltig_ab
FROM beteiligung b JOIN gesellschaft g ON g.id = b.gesellschaft_id
WHERE b.gueltig_bis IS NULL;

-- Block L: alle Fristen aus den anderen Blöcken berechnet, mit Alert-Vorlauf
CREATE VIEW v_fristen AS
WITH f AS (
  SELECT v.gesellschaft_id, 'Festsetzungsverjährung ' || v.steuerart || ' ' || v.jahr AS frist,
         v.festsetzungsfrist_ende AS datum, interval '6 months' AS vorlauf,
         v.ablaufhemmung AS hinweis, 'C06' AS feld
  FROM veranlagung v WHERE v.festsetzungsfrist_ende IS NOT NULL AND NOT v.verjaehrt
  UNION ALL
  SELECT u.gesellschaft_id, 'Sperrfrist ' || u.sperrfrist_norm || ' – ' || u.gegenstand, u.sperrfrist_ende,
         interval '12 months', u.sperrfrist_status, 'E06'
  FROM umwandlung u WHERE u.sperrfrist_ende IS NOT NULL
  UNION ALL
  SELECT o.organgesellschaft_id, 'Mindestlaufzeit GAV ' || o.organtraeger_name || ' / ' || o.organgesellschaft_name,
         o.mindestlaufzeit_bis, interval '12 months', o.status, 'B04'
  FROM organschaft o WHERE o.mindestlaufzeit_bis IS NOT NULL AND o.status = 'besteht'
  UNION ALL
  SELECT f.gesellschaft_id, 'SPA: ' || s.art || coalesce(' (' || f.finding_code || ')', ''), s.frist_ende,
         interval '6 months', s.fundstelle_spa, 'K11'
  FROM spa_absicherung s LEFT JOIN finding f ON f.id = s.finding_id WHERE s.frist_ende IS NOT NULL
)
SELECT g.name AS gesellschaft, f.feld, f.frist, f.datum, (f.datum - f.vorlauf)::date AS alert_ab, f.hinweis,
       CASE WHEN f.datum < current_date THEN 'abgelaufen'
            WHEN f.hinweis IN ('einspruch_171_3a','aussenpruefung_171_4') THEN 'gehemmt'
            WHEN f.hinweis = 'ausgeloest' THEN 'ausgeloest'
            WHEN current_date >= f.datum - f.vorlauf THEN 'alert'
            ELSE 'laeuft' END AS status
FROM f LEFT JOIN gesellschaft g ON g.id = f.gesellschaft_id;

-- Offene Jahre: Grundlage für die Handlungsfähigkeit
CREATE VIEW v_offene_jahre AS
SELECT g.name AS gesellschaft, v.jahr, v.steuerart, v.bescheidart, v.vorbehalt_164, v.vorlaeufig_165, v.vorlaeufig_az,
       v.festsetzungsfrist_ende, v.ablaufhemmung
FROM veranlagung v JOIN gesellschaft g ON g.id = v.gesellschaft_id
WHERE NOT v.verjaehrt AND (v.vorbehalt_164 = 'ja' OR v.vorlaeufig_165 OR v.ablaufhemmung IS NOT NULL OR v.stand <> 'veranlagt');

-- Alle Aktenzeichen im Profil, gegen die ein neues Urteil exakt geprüft wird (Matching-Regel 1)
CREATE VIEW v_aktenzeichen_im_profil AS
SELECT r.gesellschaft_id, r.musterverfahren_az AS aktenzeichen, 'C05 Einspruch ruht' AS fundort, r.id AS objekt_id
FROM rechtsbehelf r WHERE r.musterverfahren_az IS NOT NULL
UNION ALL
SELECT v.gesellschaft_id, v.vorlaeufig_az, 'C03 Vorläufigkeit ' || v.steuerart || ' ' || v.jahr, v.id
FROM veranlagung v WHERE v.vorlaeufig_az IS NOT NULL
UNION ALL
SELECT f.gesellschaft_id, f.musterverfahren_az, 'K ' || f.finding_code, f.id
FROM finding f WHERE f.musterverfahren_az IS NOT NULL;

-- Direkte Treffer: neues Urteil ↔ Aktenzeichen im Profil
CREATE VIEW v_direkte_treffer AS
SELECT u.aktenzeichen, u.status AS urteil_status, g.name AS gesellschaft, a.fundort
FROM urteil u JOIN v_aktenzeichen_im_profil a ON a.aktenzeichen = u.aktenzeichen
JOIN gesellschaft g ON g.id = a.gesellschaft_id;

-- Offene Punkte für den Steuerberater: unbekannte Merkmale + Prüfhinweise
CREATE VIEW v_offene_punkte AS
SELECT g.name AS gesellschaft, m.merkmal_code AS feld, 'unbekannt' AS art, m.kurztext AS beschreibung
FROM merkmal_wert m JOIN gesellschaft g ON g.id = m.gesellschaft_id WHERE m.wert = 'unbekannt'
UNION ALL
SELECT g.name, p.feld, p.art, p.beschreibung
FROM pruefhinweis p LEFT JOIN gesellschaft g ON g.id = p.gesellschaft_id WHERE p.status = 'offen';
