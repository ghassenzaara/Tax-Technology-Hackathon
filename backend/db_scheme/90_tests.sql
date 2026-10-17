-- =====================================================================
-- Tests: Abgleich der Seeds mit den Lösungsschlüsseln + Fallstricke
-- Bricht bei der ersten Abweichung mit einer Fehlermeldung ab.
-- =====================================================================
SET search_path = profil, public;

CREATE OR REPLACE FUNCTION pg_temp.ok(cond boolean, msg text) RETURNS void LANGUAGE plpgsql AS
$$ BEGIN IF cond IS NOT TRUE THEN RAISE EXCEPTION 'TEST FEHLGESCHLAGEN: %', msg; END IF; RAISE NOTICE 'ok  – %', msg; END $$;

-- ---------- Nordhafen ----------
SELECT pg_temp.ok((SELECT closing_am = '2026-05-12' FROM deal WHERE projektname='Hafenkante'), 'A01 Closing Nordhafen');
SELECT pg_temp.ok((SELECT wz_code = '52.10' AND rolle = 'operativ' FROM gesellschaft WHERE name='Nordhafen Logistik GmbH'), 'A06/A07 Nordhafen');
SELECT pg_temp.ok((SELECT gesellschafter = 'Weserlinie Logistics BidCo GmbH' AND quote_prozent = 100 FROM v_gesellschafter_aktuell WHERE gesellschaft='Nordhafen Logistik GmbH'), 'B01 Nordhafen 100 % BidCo');
SELECT pg_temp.ok((SELECT wert = 'nein' FROM merkmal_wert WHERE gesellschaft_id=ges('Nordhafen Logistik GmbH') AND merkmal_code='B04'), 'Fallstrick: GAV-Entwurf → B04 nein');
SELECT pg_temp.ok((SELECT status = 'unbekannt' FROM auslandsbezug WHERE land='PL'), 'Fallstrick: Betriebsstätte Polen unbekannt');
SELECT pg_temp.ok((SELECT bescheid_vom = '2026-02-03' FROM veranlagung WHERE gesellschaft_id=ges('Nordhafen Logistik GmbH') AND jahr=2024 AND steuerart='KSt'), 'Fallstrick: Bescheid 2024 nach Report-Stichtag');
SELECT pg_temp.ok((SELECT verjaehrt FROM veranlagung WHERE gesellschaft_id=ges('Nordhafen Logistik GmbH') AND jahr=2020 AND steuerart='KSt'), 'C06 2020 verjährt');
SELECT pg_temp.ok((SELECT betrag_eur = 2412380 FROM steuerattribut WHERE gesellschaft_id=ges('Nordhafen Logistik GmbH') AND art='kst_verlustvortrag' AND stichtag='2024-12-31'), 'D01 Nordhafen exakt aus Bescheid');
SELECT pg_temp.ok((SELECT betrag_eur = 1790328 FROM steuerattribut WHERE gesellschaft_id=ges('Nordhafen Logistik GmbH') AND art='gewst_fehlbetrag' AND stichtag='2024-12-31'), 'D02 Nordhafen');
SELECT pg_temp.ok((SELECT betrag_eur = 0 AND NOT festgestellt FROM steuerattribut WHERE gesellschaft_id=ges('Nordhafen Logistik GmbH') AND art='zinsvortrag'), 'Fallstrick: Zinsvortrag belegt 0 (nicht unbekannt)');
SELECT pg_temp.ok((SELECT count(*) = 2 FROM darlehen WHERE darlehensnehmer_id=ges('Nordhafen Logistik GmbH')), 'Fallstrick: E01 zwei Darlehen (alt + neu)');
SELECT pg_temp.ok((SELECT sperrfrist_status = 'ausgeloest' AND einbringungsgewinn_eur = 1001429 FROM umwandlung WHERE gesellschaft_id=ges('Nordhafen Logistik GmbH')), 'Fallstrick: Sperrfrist ausgelöst');
SELECT pg_temp.ok((SELECT count(*) = 6 FROM finding WHERE deal_id=deal_id('Hafenkante')), 'K Nordhafen 6 Findings');

-- ---------- Block L ----------
SELECT pg_temp.ok((SELECT count(*) = 0 FROM v_fristen WHERE frist LIKE 'Mindestlaufzeit GAV Weserlinie%'), 'L: geplante Organschaft erzeugt keine Frist');

-- ---------- Direkter Treffer (Matching-Regel 1) mit fiktivem Urteil ----------
BEGIN;
INSERT INTO urteil (gericht, aktenzeichen, datum, status, steuerarten, richtung, kernaussage)
VALUES ('BFH','IV R 88/24','2026-10-15','rechtskraeftig','{GewSt}','zugunsten','(fiktiv) Regalanlage in Silobauweise ist bewegliches Wirtschaftsgut (Hinzurechnung 20 %).');
SELECT pg_temp.ok((SELECT count(*) = 4 FROM v_direkte_treffer WHERE aktenzeichen='IV R 88/24' AND gesellschaft='Nordhafen Logistik GmbH'),
                  'Direkttreffer IV R 88/24 → Einspruch 2022, Vorläufigkeit 2023 und 2024, Finding K-3');
ROLLBACK;
