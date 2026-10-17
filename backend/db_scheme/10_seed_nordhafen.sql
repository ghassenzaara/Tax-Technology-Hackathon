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
