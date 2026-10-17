-- =====================================================================
-- Seed: Lösungsprofil Nordhafen Logistik GmbH (Projekt „Hafenkante“) – fiktiv
-- =====================================================================
SET search_path = profil, public;
BEGIN;

INSERT INTO deal (projektname, kaeufer, verkaeufer, deal_typ, signing_am, closing_am, kaufpreis_eur, locked_box_stichtag,
                  dd_art, dd_umfang, dd_ersteller, dd_report_am, dd_datenraum_stichtag, dd_zeitraum_von, dd_zeitraum_bis, dd_sprache)
VALUES ('Hafenkante', 'Weserlinie Logistics BidCo GmbH', 'Henrik Albers (60 %), Hansekontor Beteiligungen GmbH (40 %)', 'share_deal',
        '2026-03-18', '2026-05-12', 38400000, '2025-12-31', 'buy_side', 'full_scope', 'Brandt Kessler Tax Partners mbB',
        '2026-03-06', '2026-01-31', 2021, 2024, 'de');

INSERT INTO dokument (deal_id, dok_nr, titel, dateiname, klasse_code, dokument_datum, hochgeladen_am, sprache)
SELECT deal_id('Hafenkante'), x.* FROM (VALUES
 ('01','Tax-DD-Report Brandt Kessler','01_Tax-DD-Report.pdf','dd_report','2026-03-06'::date,'2026-03-06'::date,'de'),
 ('02','SPA UR-Nr. 412/2026 (Auszug) und Vollzugsprotokoll','02_SPA-Auszug.pdf','spa','2026-03-18','2026-05-13','de'),
 ('03','Handelsregisterauszug HRB 31842 HB','03_Handelsregisterauszug.pdf','register','2026-05-20','2026-05-21','de'),
 ('04','Gesellschafterlisten 2009–2026','04_Gesellschafterlisten.pdf','register','2026-05-19','2026-05-21','de'),
 ('05','Konzernorganigramm vor/nach Vollzug','05_Konzernorganigramm.pdf','organigramm','2026-09-30','2026-09-30','de'),
 ('06','Entwurf Gewinnabführungsvertrag BidCo/Nordhafen','06_Gewinnabfuehrungsvertrag_ENTWURF.pdf','entwurf','2026-09-22','2026-09-22','de'),
 ('07','Steuerbescheide KSt/GewSt 2020–2024','07_Steuerbescheide.pdf','bescheid','2026-02-03','2026-02-05','de'),
 ('08','Feststellungsbescheide Verluste','08_Feststellungsbescheide.pdf','bescheid','2026-02-03','2026-02-05','de'),
 ('09','ELSTER-Protokolle KSt/GewSt/USt','09_Steuererklaerungen.pdf','erklaerung','2025-09-30','2026-01-14','de'),
 ('10','Jahresabschluss 31.12.2024','10_Jahresabschluss-2024.pdf','jahresabschluss','2025-04-02','2026-01-09','de'),
 ('11','Einspruch GewSt 2022 und Ruhensmitteilung','11_Einspruch-Ruhensmitteilung.pdf','rechtsbehelf','2024-08-02','2026-01-16','de'),
 ('12','Außenprüfung 2016–2019, Bestätigung StB','12_Betriebspruefung.pdf','aussenpruefung','2022-06-30','2026-01-14','de'),
 ('13','Darlehensverträge Gesellschafter','13_Darlehensvertraege.pdf','vertrag','2026-05-11','2026-05-21','de'),
 ('14','Miet- und Leasingverträge','14_Miet-und-Leasingvertraege.pdf','vertrag','2018-09-14','2026-01-16','de'),
 ('15','Grundbuch Bremen-Hafen Bl. 10457, Immobilienliste','15_Grundbuch-Immobilienliste.pdf','grundbuch','2026-01-22','2026-01-22','de'),
 ('16','Einbringung Albers Spedition 2020','16_Einbringung-2020.pdf','vertrag','2020-06-30','2026-01-16','de'),
 ('17','Datenraum-Index und Q&A-Log','17_Datenraum-Index-QA-Log.pdf','qa_index','2026-09-30','2026-09-30','de')
) AS x(dok_nr, titel, dateiname, klasse_code, dokument_datum, hochgeladen_am, sprache);

-- Block A
INSERT INTO gesellschaft (deal_id, name, strasse, plz, ort, land, registergericht, registernummer, rechtsform, rolle, wz_code, wz_bezeichnung,
                          steuernummer, finanzamt, ist_zielgesellschaft, verantwortlicher_stb, ansprechpartner_mandant, letzter_refresh,
                          quelle_dok, fundstelle, stand, konfidenz)
VALUES (deal_id('Hafenkante'), 'Nordhafen Logistik GmbH', 'Hafenkante 14', '28217', 'Bremen', 'DE', 'Amtsgericht Bremen', 'HRB 31842 HB',
        'GmbH', 'operativ', '52.10', 'Lagerei (Nebentätigkeit 52.29 Spedition)', '60 214 07831', 'Finanzamt Bremen', true,
        'StB Dr. Maren Kessler (Brandt Kessler)', 'Dr. Katrin Wessels (Geschäftsführerin)', '2026-10-17',
        dok('Hafenkante','03'), 'Aktueller Ausdruck Nr. 2–6; WZ aus Report Abschn. 3.1', '2026-05-20', 'hoch');

-- Tri-State-Merkmale
INSERT INTO merkmal_wert (gesellschaft_id, merkmal_code, wert, kurztext, quelle_dok, fundstelle, stand, konfidenz) VALUES
 (ges('Nordhafen Logistik GmbH'),'B04','nein','Keine Organschaft; GAV mit BidCo nur als Entwurf (ab 01.01.2027 geplant)',dok('Hafenkante','03'),'HR: kein Unternehmensvertrag eingetragen; Dok 06 = Entwurf','2026-09-22','hoch'),
 (ges('Nordhafen Logistik GmbH'),'B05','nein','Keine USt-Organschaft',dok('Hafenkante','17'),'Q&A 08','2026-01-15','hoch'),
 (ges('Nordhafen Logistik GmbH'),'B07','ja','Nordhafen Logistics B.V. (NL); Betriebsstätte Polen ungeklärt',dok('Hafenkante','05'),'Organigramm A/B','2026-09-30','hoch'),
 (ges('Nordhafen Logistik GmbH'),'C04','ja','Einspruch GewSt 2022 ruht (BFH IV R 88/24)',dok('Hafenkante','11'),'Ruhensmitteilung 02.08.2024','2026-01-31','hoch'),
 (ges('Nordhafen Logistik GmbH'),'C09','nein','Keine Prüfungsanordnung ab 2020',dok('Hafenkante','12'),'Teil C, Bestätigung 30.09.2026','2026-09-30','hoch'),
 (ges('Nordhafen Logistik GmbH'),'E01','ja','Gesellschafterdarlehen Albers (bis 12.05.2026), seit Closing Darlehen BidCo',dok('Hafenkante','13'),'Übersicht','2026-05-12','hoch'),
 (ges('Nordhafen Logistik GmbH'),'E03','ja','Miete Halle 3 1,1 Mio. €, Leasing Flurförderzeuge 0,34 Mio. €',dok('Hafenkante','14'),'Übersicht, Anlage 3','2026-01-31','hoch'),
 (ges('Nordhafen Logistik GmbH'),'E05','ja','Grundstück Hafenkante 14 (Eigentum)',dok('Hafenkante','15'),'Grundbuch Abt. I','2026-01-22','hoch'),
 (ges('Nordhafen Logistik GmbH'),'E05b','nein','Nur einfache Kürzung § 9 Nr. 1 S. 1 GewStG',dok('Hafenkante','09'),'GewSt-Erklärungen Zeile 49','2025-09-30','hoch'),
 (ges('Nordhafen Logistik GmbH'),'E06','ja','Einbringung § 20 UmwStG zum 30.06.2020; Sperrfrist durch Verkauf ausgelöst',dok('Hafenkante','16'),'Übersicht, Abschn. 5','2026-06-03','hoch'),
 (ges('Nordhafen Logistik GmbH'),'G02','ja','§ 4 Nr. 3 UStG, vorsteuerunschädlich',dok('Hafenkante','09'),'USt-Erklärungen Zeile 27','2025-07-31','hoch');

-- Block B
INSERT INTO beteiligung (gesellschaft_id, gesellschafter, gesellschafter_art, sitz_wohnort, quote_prozent, anteile, gueltig_ab, gueltig_bis, quelle_dok, fundstelle, konfidenz) VALUES
 (ges('Nordhafen Logistik GmbH'),'Weserlinie Logistics BidCo GmbH','kapitalgesellschaft','Hamburg (HRB 178203)',100,'1–250.000','2026-05-12',NULL,dok('Hafenkante','04'),'Liste vom 19.05.2026','hoch'),
 (ges('Nordhafen Logistik GmbH'),'Henrik Albers','natuerliche_person','Bremen',60,'1–15.000; 25.001–160.000','2019-08-15','2026-05-12',dok('Hafenkante','04'),'Listen 2019/2020','hoch'),
 (ges('Nordhafen Logistik GmbH'),'Hansekontor Beteiligungen GmbH','kapitalgesellschaft','Hamburg (HRB 151377)',40,'15.001–25.000; 160.001–250.000','2019-08-15','2026-05-12',dok('Hafenkante','04'),'Listen 2019/2020','hoch'),
 (ges('Nordhafen Logistik GmbH'),'Henrik Albers','natuerliche_person','Bremen',100,'1–25.000','2009-05-21','2019-08-15',dok('Hafenkante','04'),'Gründungsliste','hoch');

INSERT INTO anteilswechsel (gesellschaft_id, datum, vorgang, quote_prozent, abgebender, erwerber, mittelbar, beschreibung, quelle_dok, fundstelle, konfidenz) VALUES
 (ges('Nordhafen Logistik GmbH'),'2019-08-15','abtretung',40,'Henrik Albers','Hansekontor Beteiligungen GmbH',false,'Abtretung von 10.000 Geschäftsanteilen',dok('Hafenkante','04'),'Liste vom 15.08.2019','hoch'),
 (ges('Nordhafen Logistik GmbH'),'2020-06-30','kapitalerhoehung',NULL,NULL,'Henrik Albers (Sacheinlage), Hansekontor (Bareinlage)',false,'Kapitalerhöhung 25.000 → 250.000 €, Quoten unverändert 60/40',dok('Hafenkante','16'),'Teil A Gesellschafterbeschluss','hoch'),
 (ges('Nordhafen Logistik GmbH'),'2026-05-12','abtretung',100,'Henrik Albers, Hansekontor Beteiligungen GmbH','Weserlinie Logistics BidCo GmbH',false,'Deal: Erwerb sämtlicher Anteile',dok('Hafenkante','02'),'Vollzugsprotokoll Ziff. 2','hoch');

INSERT INTO organschaft (art, status, organtraeger_name, organgesellschaft_id, organgesellschaft_name, wirksam_ab, mindestlaufzeit_bis, bemerkung, quelle_dok, fundstelle, konfidenz) VALUES
 ('kst_gewst','geplant','Weserlinie Logistics BidCo GmbH',ges('Nordhafen Logistik GmbH'),'Nordhafen Logistik GmbH','2027-01-01','2031-12-31',
  'Nur Entwurf vom 22.09.2026 – nicht unterzeichnet, keine Zustimmungsbeschlüsse, nicht eingetragen',dok('Hafenkante','06'),'§ 3 Entwurf','hoch');

INSERT INTO auslandsbezug (gesellschaft_id, art, bezeichnung, land, quote_prozent, status, seit, bemerkung, quelle_dok, fundstelle, konfidenz) VALUES
 (ges('Nordhafen Logistik GmbH'),'tochtergesellschaft','Nordhafen Logistics B.V., Venlo (KvK 71935520)','NL',100,'ja','2018-01-01','Spedition/Disposition Benelux',dok('Hafenkante','05'),'Organigramm','hoch'),
 (ges('Nordhafen Logistik GmbH'),'betriebsstaette','Disponent im Homeoffice Szczecin','PL',NULL,'unbekannt','2023-03-01','Q&A 19 offen – Prüfung durch polnischen Berater ausstehend',dok('Hafenkante','17'),'Q&A 19','mittel');

-- Block C
INSERT INTO veranlagung (gesellschaft_id, jahr, steuerart, bescheidart, stand, erklaerung_am, bescheid_vom, vorbehalt_164, vorlaeufig_165, vorlaeufig_punkt, vorlaeufig_az,
                         abweichung_von_erklaerung, erweiterte_kuerzung, festsetzungsfrist_ende, ablaufhemmung, verjaehrt, quelle_dok, fundstelle, konfidenz) VALUES
 (ges('Nordhafen Logistik GmbH'),2020,'KSt','festsetzung','veranlagt','2021-11-10','2022-02-15','entfallen',false,NULL,NULL,NULL,NULL,'2025-12-31',NULL,true,dok('Hafenkante','07'),'Bescheid 1','hoch'),
 (ges('Nordhafen Logistik GmbH'),2021,'KSt','festsetzung','veranlagt','2022-09-14','2022-12-07','ja',false,NULL,NULL,NULL,NULL,'2026-12-31',NULL,false,dok('Hafenkante','07'),'Bescheid 2','hoch'),
 (ges('Nordhafen Logistik GmbH'),2021,'GewSt','messbetrag','veranlagt','2022-09-14','2022-12-07','ja',false,NULL,NULL,NULL,'nicht_beantragt','2026-12-31',NULL,false,dok('Hafenkante','07'),'Bescheid 6','hoch'),
 (ges('Nordhafen Logistik GmbH'),2022,'KSt','festsetzung','veranlagt','2023-11-28','2024-06-21','ja',false,NULL,NULL,NULL,NULL,'2027-12-31',NULL,false,dok('Hafenkante','07'),'Bescheid 3','hoch'),
 (ges('Nordhafen Logistik GmbH'),2022,'GewSt','messbetrag','veranlagt','2023-11-28','2024-06-21','ja',false,NULL,NULL,'Hinzurechnung Regalanlage mit 50 % statt 20 % (+37.500 €)','nicht_beantragt','2027-12-31','einspruch_171_3a',false,dok('Hafenkante','07'),'Bescheid 7','hoch'),
 (ges('Nordhafen Logistik GmbH'),2023,'KSt','festsetzung','veranlagt','2024-10-16','2024-12-18','ja',false,NULL,NULL,NULL,NULL,'2028-12-31',NULL,false,dok('Hafenkante','07'),'Bescheid 4','hoch'),
 (ges('Nordhafen Logistik GmbH'),2023,'GewSt','messbetrag','veranlagt','2024-10-16','2024-12-18','ja',true,'Hinzurechnung Miete Regalanlage als unbewegliches WG (§ 8 Nr. 1 Buchst. e GewStG)','IV R 88/24','Hinzurechnung Regalanlage 50 % (+37.500 €)','nicht_beantragt','2028-12-31',NULL,false,dok('Hafenkante','07'),'Bescheid 8','hoch'),
 (ges('Nordhafen Logistik GmbH'),2024,'KSt','festsetzung','veranlagt','2025-09-30','2026-02-03','ja',false,NULL,NULL,NULL,NULL,'2029-12-31',NULL,false,dok('Hafenkante','07'),'Bescheid 5','hoch'),
 (ges('Nordhafen Logistik GmbH'),2024,'GewSt','messbetrag','veranlagt','2025-09-30','2026-02-03','ja',true,'Hinzurechnung Miete Regalanlage als unbewegliches WG (§ 8 Nr. 1 Buchst. e GewStG)','IV R 88/24','Hinzurechnung Regalanlage 50 % (+37.500 €)','nicht_beantragt','2029-12-31',NULL,false,dok('Hafenkante','07'),'Bescheid 9','hoch'),
 (ges('Nordhafen Logistik GmbH'),2021,'USt','anmeldung_168','veranlagt','2022-07-29',NULL,'ja',false,NULL,NULL,NULL,NULL,'2026-12-31',NULL,false,dok('Hafenkante','07'),'Deckblatt Hinweis USt','hoch'),
 (ges('Nordhafen Logistik GmbH'),2022,'USt','anmeldung_168','veranlagt','2023-07-31',NULL,'ja',false,NULL,NULL,NULL,NULL,'2027-12-31',NULL,false,dok('Hafenkante','07'),'Deckblatt Hinweis USt','hoch'),
 (ges('Nordhafen Logistik GmbH'),2023,'USt','anmeldung_168','veranlagt','2024-07-26',NULL,'ja',false,NULL,NULL,NULL,NULL,'2028-12-31',NULL,false,dok('Hafenkante','07'),'Deckblatt Hinweis USt','hoch'),
 (ges('Nordhafen Logistik GmbH'),2024,'USt','anmeldung_168','veranlagt','2025-07-31',NULL,'ja',false,NULL,NULL,NULL,NULL,'2029-12-31',NULL,false,dok('Hafenkante','07'),'Deckblatt Hinweis USt','hoch');

INSERT INTO rechtsbehelf (gesellschaft_id, veranlagung_id, art, eingelegt_am, status, ruht_rechtsgrundlage, musterverfahren_gericht, musterverfahren_az, streitpunkt, auswirkung_eur, quelle_dok, fundstelle, konfidenz)
SELECT ges('Nordhafen Logistik GmbH'), v.id, 'einspruch', '2024-07-15', 'ruht', '§ 363 Abs. 2 S. 2 AO', 'BFH', 'IV R 88/24',
       'Regalanlage in Silobauweise: bewegliches (20 %) oder unbewegliches (50 %) Wirtschaftsgut für die Hinzurechnung', 37500,
       dok('Hafenkante','11'), 'Einspruch 15.07.2024; Mitteilung FA 02.08.2024', 'hoch'
FROM veranlagung v WHERE v.gesellschaft_id = ges('Nordhafen Logistik GmbH') AND v.jahr = 2022 AND v.steuerart = 'GewSt';

INSERT INTO aussenpruefung (gesellschaft_id, status, steuerarten, zeitraum_von, zeitraum_bis, anordnung_vom, beginn_am, bericht_vom, quelle_dok, fundstelle, konfidenz) VALUES
 (ges('Nordhafen Logistik GmbH'),'abgeschlossen','{KSt,GewSt,USt}',2016,2019,'2021-04-12','2021-06-07','2022-06-30',dok('Hafenkante','12'),'Teil A/B','hoch');
INSERT INTO pruefungsfeststellung (aussenpruefung_id, tz, thema, steuerart, betrag_eur, quelle_dok, fundstelle, konfidenz)
SELECT a.id, x.tz, x.thema, x.st::steuerart, x.betrag, dok('Hafenkante','12'), 'Bericht Abschn. 2', 'hoch'
FROM aussenpruefung a, (VALUES ('Tz. 1','vGA Zinsen Gesellschafterdarlehen (fremdüblich 4,0 % statt 6,0 %), 2017–2019','KSt',250000),
                               ('Tz. 2','Anschaffungsnahe Herstellungskosten Hafenkante 14','KSt',412000),
                               ('Tz. 3','Vorsteuerkürzung Bewirtungsaufwendungen','USt',3200)) x(tz, thema, st, betrag)
WHERE a.gesellschaft_id = ges('Nordhafen Logistik GmbH');
INSERT INTO pruefungsfeststellung_norm SELECT id, '§ 8 Abs. 3 KStG' FROM pruefungsfeststellung WHERE tz = 'Tz. 1' AND thema LIKE 'vGA%';
INSERT INTO pruefungsfeststellung_norm SELECT id, '§ 6 Abs. 1 Nr. 1a EStG' FROM pruefungsfeststellung WHERE thema LIKE 'Anschaffungsnahe%';

-- Block D
INSERT INTO steuerattribut (gesellschaft_id, art, stichtag, betrag_eur, festgestellt, sperre, quelle_dok, fundstelle, konfidenz) VALUES
 (ges('Nordhafen Logistik GmbH'),'kst_verlustvortrag','2024-12-31',2412380,true,NULL,dok('Hafenkante','08'),'Bescheid 1 vom 03.02.2026','hoch'),
 (ges('Nordhafen Logistik GmbH'),'gewst_fehlbetrag','2024-12-31',1790328,true,NULL,dok('Hafenkante','08'),'Bescheid 2 vom 03.02.2026','hoch'),
 (ges('Nordhafen Logistik GmbH'),'kst_verlustvortrag','2023-12-31',2637810,true,NULL,dok('Hafenkante','08'),'Bescheid 3','hoch'),
 (ges('Nordhafen Logistik GmbH'),'gewst_fehlbetrag','2023-12-31',2221442,true,NULL,dok('Hafenkante','08'),'Bescheid 4','hoch'),
 (ges('Nordhafen Logistik GmbH'),'zinsvortrag','2024-12-31',0,false,'Zinsschranke nicht anwendbar (Nettozins 480 T€ < 3 Mio. €)',dok('Hafenkante','08'),'Aktennotiz StB; Q&A 11','hoch');

-- Extras
INSERT INTO darlehen (darlehensnehmer_id, darlehensgeber, beziehung, betrag_eur, zinssatz_prozent, beginn_am, faellig_am, status, beendet_am, quelle_dok, fundstelle, konfidenz) VALUES
 (ges('Nordhafen Logistik GmbH'),'Henrik Albers','gesellschafter',5000000,6.0,'2017-07-01','2027-06-30','zurueckgezahlt','2026-05-12',dok('Hafenkante','13'),'Vertrag 26.06.2017; Payoff Letter','hoch'),
 (ges('Nordhafen Logistik GmbH'),'Weserlinie Logistics BidCo GmbH','gesellschafter',5260000,5.25,'2026-05-12','2031-12-31','laufend',NULL,dok('Hafenkante','13'),'Gesellschafterdarlehen 11.05.2026','hoch');

INSERT INTO nutzungsvertrag (gesellschaft_id, art, vertragspartner, nahestehend, objekt, jahresentgelt_eur, davon_unbeweglich, davon_beweglich, beginn_am, ende_am, quelle_dok, fundstelle, konfidenz) VALUES
 (ges('Nordhafen Logistik GmbH'),'miete','Weser Logistikimmobilien GmbH & Co. KG',false,'Hochregallager Halle 3, Hafenkante 18',1100000,600000,500000,'2019-01-01','2033-12-31',dok('Hafenkante','14'),'§ 4, Anlage 3','hoch'),
 (ges('Nordhafen Logistik GmbH'),'leasing','NordLease GmbH',false,'46 Flurförderzeuge',340000,NULL,340000,'2022-01-01','2027-12-31',dok('Hafenkante','14'),'Leasingrahmenvertrag','hoch');

INSERT INTO grundstueck (eigentuemer_id, nutzer_id, objekt, adresse, eigentum_status, grundbuch, flurstueck, flaeche_qm, buchwert_eur, einheitswert_eur, grundsteuerwert_eur, quelle_dok, fundstelle, konfidenz) VALUES
 (ges('Nordhafen Logistik GmbH'),ges('Nordhafen Logistik GmbH'),'Verwaltung und Lagerhalle 1','Hafenkante 14, 28217 Bremen','eigentum','AG Bremen, Bremen-Hafen Blatt 10457','Flur 18, Flurstück 112/4',21480,4200000,1120000,6240000,dok('Hafenkante','15'),'Grundbuch Abt. I; Immobilienliste','hoch'),
 (NULL,ges('Nordhafen Logistik GmbH'),'Hochregallager Halle 3','Hafenkante 18, 28217 Bremen','miete',NULL,NULL,23720,NULL,NULL,NULL,dok('Hafenkante','15'),'Immobilienliste','hoch');

INSERT INTO umwandlung (gesellschaft_id, einbringungszeitpunkt, art, gegenstand, einbringende, wertansatz, buchwert_eur, gemeiner_wert_eur, sperrfrist_norm, sperrfrist_ende, sperrfrist_status, ausgeloest_am, einbringungsgewinn_eur, quelle_dok, fundstelle, konfidenz) VALUES
 (ges('Nordhafen Logistik GmbH'),'2020-06-30','einbringung_20','Einzelunternehmen „Albers Spedition e. K.“ (HRA 25519 HB)','Henrik Albers','buchwert',1395000,4900000,'§ 22 Abs. 1 UmwStG','2027-06-30','ausgeloest','2026-05-12',1001429,dok('Hafenkante','16'),'Teil B § 2; Abschn. 5','hoch');

INSERT INTO ust_steuerfrei (gesellschaft_id, jahr, norm_code, beschreibung, betrag_eur, vorsteuerabzug_erhalten, quelle_dok, fundstelle, konfidenz)
SELECT ges('Nordhafen Logistik GmbH'), j, '§ 4 Nr. 3 UStG', 'Grenzüberschreitende Güterbeförderung, Umschlag Ausfuhr', b, true, dok('Hafenkante','09'), 'USt-Erklärung Zeile 27', 'hoch'
FROM (VALUES (2021,7200000),(2022,7600000),(2023,8100000),(2024,8700000)) x(j,b);

-- Block K
INSERT INTO finding (deal_id, gesellschaft_id, finding_code, report_ref, titel, steuerarten, jahr_von, jahr_bis, kurzsachverhalt, risiko, richtung,
                     betrag_min_eur, betrag_max_eur, offene_rechtsfrage, musterverfahren_az, status, empfehlung, quelle_dok, fundstelle, konfidenz)
SELECT deal_id('Hafenkante'), ges('Nordhafen Logistik GmbH'), x.finding_code, x.report_ref, x.titel, x.steuerarten, x.jahr_von, x.jahr_bis, x.kurzsachverhalt, x.risiko, x.richtung, x.betrag_min_eur, x.betrag_max_eur,
       x.offene_rechtsfrage, x.musterverfahren_az, x.status, x.empfehlung, dok('Hafenkante','01'), x.ref2, 'hoch'::konfidenz FROM (VALUES
 ('K-1','Feststellung 1','Verlustuntergang durch Closing (Stille-Reserven-Klausel)','{KSt,GewSt}'::steuerart[],2026,NULL::int,
  'Erwerb von 100 % der Anteile löst § 8c KStG / § 10a S. 10 GewStG aus; Verluste bleiben nur in Höhe der inländischen stillen Reserven erhalten.','mittel','risiko',0,680000,false,NULL,'offen','Stille Reserven dokumentieren; Verluste im Kaufpreis mit 0 bewerten','Abschn. 5, Feststellung 1'),
 ('K-2','Feststellung 2','vGA durch überhöhten Darlehenszins (6,0 % statt 4,0 %)','{KSt,GewSt}',2021,2026,
  'Gesellschafterdarlehen Albers 5 Mio. € zu 6,0 %; BP 2016–2019 hat 4,0 % angesetzt; Zins unverändert fortgeführt.','hoch','risiko',120000,180000,false,NULL,'im_spa_gedeckt','Steuerfreistellung; Rückzahlung zum Closing','Abschn. 5, Feststellung 2'),
 ('K-3','Feststellung 3','GewSt-Hinzurechnung Regalanlage (20 % vs. 50 %)','{GewSt}',2021,2025,
  'FA setzt Miete Regalanlage (500.000 €) ab 2022 mit 50 % an; Einspruch 2022 ruht, 2023/2024 vorläufig; 2021 unter Vorbehalt änderbar.','gering','beides',6000,25000,true,'IV R 88/24','offen','Einspruch/Vorläufigkeit offenhalten','Abschn. 5, Feststellung 3'),
 ('K-4','Feststellung 4','Einbringungsgewinn I aus Anteilsverkauf; Chance Buchwertaufstockung','{KSt,ESt}',2020,2026,
  'Verkauf sperrfristbehafteter Anteile im 6. Zeitjahr: Einbringungsgewinn I 1.001.429 € (2/7) beim Verkäufer; Aufstockung nach § 23 Abs. 2 UmwStG möglich.','mittel','chance',0,320000,false,NULL,'offen','Bescheinigung § 22 Abs. 5 UmwStG vom Verkäufer einfordern','Abschn. 5, Feststellung 4'),
 ('K-5','Feststellung 5','Verrechnungspreise Nordhafen B.V. (Kostenumlage ohne Aufschlag)','{KSt,GewSt}',2021,2025,
  'Dienstleistungen an die B.V. ohne Gewinnaufschlag (rd. 420 T€ p. a.); keine Verrechnungspreisdokumentation.','gering','risiko',25000,60000,false,NULL,'im_spa_gedeckt','Dokumentation nachholen; Aufschlag ab 2026','Abschn. 5, Feststellung 5'),
 ('K-6','Feststellung 6','Grunderwerbsteuer durch Anteilsübertragung ≥ 90 %','{GrESt}',2026,2026,
  '§ 1 Abs. 2b GrEStG; Steuerschuldnerin ist die Gesellschaft (§ 13 Nr. 7 GrEStG); 5 % von 6,8 Mio. €.','sicher','risiko',340000,340000,false,NULL,'realisiert','Kosten im Kaufpreis berücksichtigt','Abschn. 7, Feststellung 6')
) AS x(finding_code, report_ref, titel, steuerarten, jahr_von, jahr_bis, kurzsachverhalt, risiko, richtung, betrag_min_eur, betrag_max_eur,
       offene_rechtsfrage, musterverfahren_az, status, empfehlung, ref2);

INSERT INTO finding_norm (finding_id, norm_code)
SELECT f.id, n FROM finding f JOIN (VALUES
 ('K-1','§ 8c KStG'),('K-1','§ 10a GewStG'),('K-2','§ 8 Abs. 3 KStG'),('K-3','§ 8 Nr. 1 Buchst. d GewStG'),('K-3','§ 8 Nr. 1 Buchst. e GewStG'),
 ('K-3','§ 68 Abs. 2 Nr. 2 BewG'),('K-4','§ 22 Abs. 1 UmwStG'),('K-4','§ 23 Abs. 2 UmwStG'),('K-5','§ 1 AStG'),('K-5','§ 90 Abs. 3 AO'),('K-5','§ 162 Abs. 4 AO'),
 ('K-6','§ 1 Abs. 2b GrEStG'),('K-6','§ 13 Nr. 7 GrEStG')) x(c, n) ON x.c = f.finding_code WHERE f.deal_id = deal_id('Hafenkante');
INSERT INTO finding_merkmal (finding_id, merkmal_code)
SELECT f.id, m FROM finding f JOIN (VALUES ('K-1','D01'),('K-1','D02'),('K-1','B02'),('K-2','E01'),('K-3','E03'),('K-3','C05'),('K-4','E06'),('K-5','B07'),('K-6','E05'),('K-6','B02'))
 x(c, m) ON x.c = f.finding_code WHERE f.deal_id = deal_id('Hafenkante');

INSERT INTO spa_absicherung (deal_id, finding_id, art, fundstelle_spa, betrag_eur, frist_ende, beschreibung, quelle_dok, fundstelle, konfidenz) VALUES
 (deal_id('Hafenkante'),NULL,'steuerfreistellung','§ 9.2, § 9.5',NULL,'2031-06-30','Steuern bis Locked-Box-Stichtag 31.12.2025; Verjährung 6 Monate nach Bestandskraft, spätestens 30.06.2031; deckt K-2, K-3 (2021), K-5',dok('Hafenkante','02'),'§ 9','hoch'),
 (deal_id('Hafenkante'),(SELECT id FROM finding WHERE finding_code='K-6' AND deal_id=deal_id('Hafenkante')),'kaufpreis','§ 9.4',340000,NULL,'Grunderwerbsteuer trägt die Käuferin; im Kaufpreis berücksichtigt',dok('Hafenkante','02'),'§ 9.4','hoch'),
 (deal_id('Hafenkante'),(SELECT id FROM finding WHERE finding_code='K-4' AND deal_id=deal_id('Hafenkante')),'covenant','§ 9.6',NULL,NULL,'Verkäufer erklärt Einbringungsgewinn I und liefert Bescheinigung § 22 Abs. 5 UmwStG',dok('Hafenkante','02'),'§ 9.6','hoch'),
 (deal_id('Hafenkante'),NULL,'wi_police','§ 11.1',NULL,'2033-05-12','Buy-Side W&I, Steuergarantien 7 Jahre ab Vollzug; DD-Feststellungen ausgeschlossen',dok('Hafenkante','02'),'§ 11','hoch');

-- Block M: Prüfhinweise
INSERT INTO pruefhinweis (gesellschaft_id, art, feld, beschreibung, quelle_a, quelle_b, entscheidung, status) VALUES
 (ges('Nordhafen Logistik GmbH'),'konflikt','C01 2024','Report (Stichtag 31.01.2026): 2024 „erklärt, Veranlagung ausstehend“; Bescheide vom 03.02.2026 nachgeladen',dok('Hafenkante','01'),dok('Hafenkante','07'),'Bescheid (Rang 1) gewinnt: 2024 veranlagt, GewSt vorläufig','geklaert'),
 (ges('Nordhafen Logistik GmbH'),'plausibilitaet','B04','GAV liegt nur als Entwurf vor',dok('Hafenkante','06'),dok('Hafenkante','03'),'Entwurf (Rang 9) ist kein Beleg; B04 = nein','geklaert'),
 (ges('Nordhafen Logistik GmbH'),'konflikt','D01','Report nennt Verlustvortrag 31.12.2024 „rd. 2,4 Mio. €“',dok('Hafenkante','01'),dok('Hafenkante','08'),'Bescheid: 2.412.380 €','geklaert'),
 (ges('Nordhafen Logistik GmbH'),'offen','B07','Mögliche Betriebsstätte Polen (Disponent Szczecin) – Q&A 19 offen',dok('Hafenkante','17'),NULL,NULL,'offen');

COMMIT;
