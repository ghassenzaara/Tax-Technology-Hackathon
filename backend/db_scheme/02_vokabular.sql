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

-- ---------- Marschland ----------
SELECT pg_temp.ok((SELECT count(*) = 3 FROM gesellschaft WHERE deal_id=deal_id('Marsh')), 'Marschland: drei Profile');
SELECT pg_temp.ok((SELECT string_agg(gesellschafter || ' ' || quote_prozent, '; ' ORDER BY quote_prozent DESC) = 'Marschland Holding GmbH 94.9000; Anke Hinrichs-Wolter 5.1000'
                   FROM v_gesellschafter_aktuell WHERE gesellschaft='Marschland Immobilien GmbH'), 'Fallstrick: Immobilien 94,9 / 5,1');
SELECT pg_temp.ok((SELECT wert = 'nein' FROM merkmal_wert WHERE gesellschaft_id=ges('Marschland Immobilien GmbH') AND merkmal_code='B04')
              AND (SELECT wert = 'ja' FROM merkmal_wert WHERE gesellschaft_id=ges('Marschland Immobilien GmbH') AND merkmal_code='B05'), 'Fallstrick: Immobilien B04 nein / B05 ja');
SELECT pg_temp.ok((SELECT wert = 'unbekannt' FROM merkmal_wert WHERE gesellschaft_id=ges('Marschland Seniorenzentren GmbH') AND merkmal_code='E05'), 'Fallstrick: Parkplatz → E05 unbekannt');
SELECT pg_temp.ok((SELECT betrag_eur = 312400 FROM steuerattribut WHERE gesellschaft_id=ges('Marschland Seniorenzentren GmbH') AND art='kst_verlustvortrag'), 'Fallstrick: 312.400 statt „314k“');
SELECT pg_temp.ok((SELECT vorlaeufig_az = 'IV R 21/25' AND bescheid_vom = '2026-03-18' FROM veranlagung WHERE gesellschaft_id=ges('Marschland Immobilien GmbH') AND jahr=2024 AND steuerart='GewSt'), 'Fallstrick: GewSt 2024 nach Cut-off, vorläufig');
SELECT pg_temp.ok((SELECT count(*) = 3 FROM aussenpruefung WHERE status='angeordnet' AND beginn_am='2026-11-02'), 'Fallstrick: BP nach Closing (C09 ja, alle 3)');
SELECT pg_temp.ok((SELECT abgetreten_an = 'Moorhaven Care BidCo GmbH' FROM darlehen WHERE darlehensnehmer_id=ges('Marschland Immobilien GmbH')), 'Fallstrick: Gläubigerwechsel Darlehen');
SELECT pg_temp.ok((SELECT array_agg(sperrfrist_status ORDER BY einbringungszeitpunkt) = '{abgelaufen,laeuft}' FROM umwandlung WHERE gesellschaft_id=ges('Marschland Holding GmbH')), 'Fallstrick: zwei Sperrfristen');
SELECT pg_temp.ok((SELECT count(*) = 7 FROM finding WHERE deal_id=deal_id('Marsh')), 'K Marschland 7 Findings');

-- ---------- Block L ----------
SELECT pg_temp.ok((SELECT count(*) >= 1 FROM v_fristen WHERE feld='E06' AND datum='2027-03-31' AND status='alert'), 'L: Sperrfrist 31.03.2027 löst Alert aus');
SELECT pg_temp.ok((SELECT count(*) = 0 FROM v_fristen WHERE frist LIKE 'Mindestlaufzeit GAV Weserlinie%'), 'L: geplante Organschaft erzeugt keine Frist');

-- ---------- Direkter Treffer (Matching-Regel 1) mit fiktivem Urteil ----------
BEGIN;
INSERT INTO urteil (gericht, aktenzeichen, datum, status, steuerarten, richtung, kernaussage)
VALUES ('BFH','IV R 21/25','2026-10-15','rechtskraeftig','{GewSt}','zugunsten','(fiktiv) Vermietung an Schwester-Organgesellschaft schließt erweiterte Kürzung nicht aus.');
SELECT pg_temp.ok((SELECT count(*) = 3 FROM v_direkte_treffer WHERE aktenzeichen='IV R 21/25' AND gesellschaft='Marschland Immobilien GmbH'),
                  'Direkttreffer IV R 21/25 → Einspruch 2023, Vorläufigkeit 2024, Finding K-1');
ROLLBACK;
