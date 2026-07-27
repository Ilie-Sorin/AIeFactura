# Registru local de documente RO e-Factura și motor de reconciliere

> Specificație funcțională și tehnică — versiunea 2
> Document de referință pentru dezvoltare. Se citește integral înainte de prima modificare de cod.

---

## Context pentru agent

**Ce construim:** o aplicație web locală care captează facturi electronice RO e-Factura (XML/ZIP), le normalizează până la nivel de linie, le consolidează în grupuri de documente legate și le confruntă cu date provenite din alte sisteme.

**Ce NU construim:**

- arhivare electronică cu valoare legală (activitate reglementată separat, nu o revendicăm);
- produs comercial — fără multi-tenancy comercial, licențiere, telemetrie;
- înlocuitor pentru programul de contabilitate;
- emitere sau transmitere de facturi către ANAF;
- validare sintactică în locul ANAF (a avut deja loc înainte de publicare).

**Stack impus:**

| Componentă | Alegere | Motiv |
|---|---|---|
| Backend | Python + Flask sau FastAPI | familiaritate; ambele acceptabile, se alege una și nu se mai schimbă |
| Server WSGI/ASGI | Waitress (Flask) sau uvicorn (FastAPI) | rulare stabilă pe Windows |
| Serviciu Windows | NSSM | fără dependențe externe |
| Bază de date | PostgreSQL | BYTEA, JSONB, full-text, partiționare, CTE recursive |
| Migrări | Alembic, de la primul commit | baza va traversa ani și versiuni RO-CIUS |
| Sarcini programate | APScheduler, în procesul serviciului | **NU Celery** — suport parțial și fragil pe Windows |
| Parsare XML | lxml, `resolve_entities=False`, `no_network=True` | XXE |
| Rapoarte | openpyxl | |
| Împachetare | **niciuna** — nu se produce executabil | elimină complet falsele pozitive de antivirus |

**Convenții de cod:**

- toate datele/orele stocate cu fus orar, în UTC; conversia se face doar la afișare;
- valorile monetare: `NUMERIC(18,2)`, niciodată `float`;
- fiecare valoare extrasă din XML păstrează XPath-ul de proveniență;
- câmpurile derivate (normalizate, calculate) stau în coloane distincte, niciodată suprascriind sursa;
- nimic nu se șterge din interfață — retragerea se face prin marcare.

---

## 0. Modificări față de versiunea 1 a specificației

- Produsul nu mai este definit ca „arhivă”, ci ca registru operațional intern.
- Destinația este uz intern și platformă de testare tehnică, nu produs comercial.
- Fișierele se stochează în baza de date; structura de foldere devine format de export.
- Premisele sunt enunțate explicit și fiecare capitol derivă din ele.
- MVP-ul include consolidarea și reconcilierea, nu doar arhivarea și căutarea.
- Adăugate: dimensionare pe volume reale, normalizarea numerelor de factură, versionarea schemelor, anularea loturilor, controlul de completitudine, monitorizarea.
- *(față de varianta .docx)* adăugat capitolul 11 — model de date explicit — și criteriile de terminat din capitolul 14.

---

## 1. Premise

Trei constatări justifică existența aplicației. Toate deciziile se raportează la ele.

### P1 — Fereastra de disponibilitate de 60 de zile

Documentele publicate în SPV nu rămân direct descărcabile la nesfârșit; după expirare, recuperarea presupune o cerere separată către arhiva ANAF. Consecința nu este „descărcăm local”, ci ceva mai exigent: **captura trebuie să fie completă și verificabilă, iar lipsurile trebuie detectate cât timp mai pot fi recuperate ieftin.**

→ capitolul 8 (control de completitudine). Un import reușit nu spune nimic despre ce nu s-a importat.

### P2 — Valorificarea informației din XML

XML-ul conține date la nivel de linie — cod articol furnizor, cantitate, UM, preț unitar, cotă TVA, referință de comandă — pe care nici contabilitatea, nici gestiunea nu le păstrează în aceeași formă. Informația există deja, plătită, dar nu este interogabilă.

→ normalizare completă până la linie și cotă TVA, cu trasabilitate către XML.

### P3 — Confruntarea cu date din alte sisteme

Valoarea apare la intersecție. O factură singură nu spune nimic; una care nu se regăsește în contabilitate, sau al cărei preț diferă de cel din comandă, spune ceva imediat util.

→ motor de reconciliere configurabil + model generic de import extern (capitolul 7). **Acesta este nucleul produsului, nu o extensie.**

---

## 2. Dimensionare

| Mărime | Estimare |
|---|---|
| Documente / lună | ~4.000 intrări + ~4.000 ieșiri ≈ 8.000 |
| Documente / an | ~100.000 |
| Linii / an | 0,5 – 1,5 milioane (5–15 linii/factură) |
| ZIP e-Factura | 5–30 KB (XML factură + XML semnătură) |
| Creștere binară / an | ~2 GB fără atașamente încorporate |
| Orizont 10 ani | 20 GB+ binar, 10–15 mil. linii |

Consecințe de proiectare:

- **BYTEA este suficient**, nu Large Objects — valorile sunt cu mult sub 1 GB și TOAST comprimă automat.
- **Conținutul binar se izolează într-un tabel propriu.** Tabelele interogate rămân mici și în cache.
- **Partiționare declarativă pe an** pentru `invoice` și `invoice_line` — se proiectează de la început, se activează în anul 2.
- **`pg_dump` devine incomod peste câțiva GB.** Backup fizic (`pg_basebackup` + arhivare WAL) se stabilește din prima zi.
- La 1,5 mil. linii/an, indexarea contează mai mult decât hardware-ul. Căutarea pe descriere de linie are nevoie de full-text, nu de `LIKE`.

---

## 3. Stocarea documentelor în baza de date

### Ce se stochează

| Obiect | Rol |
|---|---|
| ZIP-ul original | octeții exacți, așa cum au fost primiți; **nu se recreează niciodată** |
| XML-ul facturii | extras din ZIP, stocat separat, ca să nu fie nevoie de dezarhivare la fiecare procesare |
| XML-ul semnăturii ANAF | trasabilitate și verificare |
| Atașamentele încorporate | extrase din XML, obiecte proprii |
| PDF-ul de vizualizare | generat la cerere cu foaia de stil oficială ANAF, **nu** cu șablon propriu; poate rămâne necachat |

Fiecare obiect are propriul **SHA-256**, calculat la import și indexat.

### Reguli de imutabilitate

- Pe tabelul binar nu există `UPDATE`, doar `INSERT` — impus prin `GRANT`, nu prin convenție în cod.
- Ștergerea nu este expusă în interfață.
- Orice acces la conținut binar se jurnalizează.

### De ce în bază și ce se pierde

**Câștig:** backup unic și consistent; integritate tranzacțională între fișier și date (imposibil „rândul există, fișierul nu”); imposibilitatea reorganizării accidentale; acces uniform.

**Cost asumat:** baza crește și devine ea obiectul de protejat; restaurarea parțială e mai grea; nu poți da cuiva „directorul cu facturile din martie”.

Ultimul punct se mitighează printr-o **funcție de export**, care reconstituie la cerere structura de foldere și o livrează ca ZIP:

```
Export/
└── RO12345678/2026/Primite/2026-07/
    └── ID_Descarcare_123456789/
        ├── original.zip
        ├── factura.xml
        ├── semnatura.xml
        ├── factura.pdf
        └── metadata.json
```

Structura de foldere devine format de schimb, nu format de stocare.

### Cheia de deduplicare

SHA-256 pe ZIP nu ajunge: același XML poate sosi în ambalaje diferite. Deduplicare ierarhică, cu certitudine descrescătoare:

1. **ID descărcare ANAF** — identificator autoritar. Potrivire → duplicat cert, importul se oprește.
2. **SHA-256 pe XML-ul facturii** — conținut identic, ambalaj diferit. Duplicat cert; se înregistrează sursa suplimentară, nu un document nou.
3. **(CIF emitent, număr normalizat, dată, total)** — surse eterogene. Potrivire → **duplicat probabil**: se semnalează, nu se blochează. Aici intră și cazul patologic „același număr, valori diferite”, care trebuie să ajungă la om.

---

## 4. Ingestia

### A. Scanner local (MVP)

Scanează recursiv, indiferent de structura directoarelor. Trebuie să:

- identifice ZIP și XML;
- ignore ce a mai importat (vezi cheia de deduplicare);
- semnaleze arhivele corupte și XML-urile neinterpretabile, fără să oprească lotul;
- păstreze calea originală ca metadată;
- accepte import manual prin drag-and-drop;
- monitorizeze automat unul sau mai multe directoare.

### B. Sincronizare ANAF (etapa 3)

Punctele care decid dacă integrarea funcționează în producție sau doar la demonstrație:

- **Expirarea tokenului de reîmprospătare.** Are viață limitată, iar reînnoirea presupune certificatul calificat. Nicio sincronizare „complet automată” nu supraviețuiește un an fără intervenție umană. Trebuie flux de re-autorizare cu **alertă cu săptămâni înainte de expirare**. Acesta este punctul cel mai frecvent de eșec.
- **Toate tipurile de mesaje**, nu doar facturile: transmise, primite, erori, răspunsuri. O eroare la o factură emisă este exact ce vrei în tabloul de bord.
- **Reluarea descărcărilor eșuate**, cu limită de încercări și marcarea celor rămase nedescărcate.
- **Confruntarea listei de mesaje cu ce există local** (capitolul 8) — singura verificare care onorează P1.

### Loturi de import și anulare

Fiecare operație de import este un **lot** cu identitate proprie, anulabil integral. Anularea elimină datele normalizate produse de lot, dar **nu** fișierele sursă, care rămân marcate „importate și retrase”.

*Motiv:* un import greșit trebuie refăcut fără redescărcare, iar dovada primirii nu dispare odată cu o greșeală de procesare.

**Stările unui document:** `primit → parsat → normalizat → validat → indexat`, cu ramură `eroare` la fiecare pas, păstrând mesajul și poziția în XML.

---

## 5. Normalizarea datelor

Se extrag: date generale ale documentului, părți (furnizor, client, reprezentant fiscal), valori și totaluri pe cote TVA, linii de factură. Trei cerințe transversale:

- **XPath de proveniență** pentru fiecare valoare extrasă.
- **Versiunea schemei** (EN 16931 / RO-CIUS) stocată pe document. O bază de zece ani traversează mai multe versiuni; fără acest câmp, o reprelucrare viitoare este oarbă.
- **Marcarea explicită a câmpurilor derivate**, în coloane distincte.

### Normalizarea numerelor de factură

Problema grea a oricărei reconcilieri. Reconcilierile eșuează aici mai des decât din orice altă cauză.

Același document poate apărea ca: `FCT 0001234`, `FCT1234`, `0001234`, `1234`, `FCT-1234/2026`. Nu există regulă universală — la unii emitenți seria chiar diferențiază documente.

Se păstrează **trei forme**, iar potrivirea încearcă în ordine descrescătoare de certitudine:

| Formă | Conținut | Scor |
|---|---|---|
| Brută | exact cum apare în sursă | maxim |
| Normalizată | majuscule, fără spații/separatoare, fără zerouri de umplere | mediu |
| Componente | serie detectată + parte numerică, comparate separat | minim |

Regulile de normalizare sunt **configurabile pe furnizor**, cu o regulă implicită globală.

Pentru CUI: eliminarea prefixului de țară, a spațiilor și a zerourilor inițiale; validare cifră de control.

---

## 6. Consolidarea documentelor legate

**Face parte din MVP.** Fără ea, reconcilierea produce excepții false în masă: orice factură stornată apare ca diferență față de contabilitate, deși nu este.

### Relații explicite, din XML

Referința la factura corectată/stornată, referința de comandă, de contract, de aviz sau recepție, către documente atașate, legătura factură finală ↔ facturi de avans.

*Realitate de asumat:* aceste câmpuri sunt opționale și mulți emitenți nu le completează. De aceea al doilea mecanism nu e un lux.

### Relații deduse

Când referințele lipsesc: același furnizor cu valori egale și de semn opus; aceeași comandă sau contract; descrieri și articole similare; date apropiate; aceeași perioadă facturată; aceeași combinație furnizor–beneficiar–valoare.

Fiecare legătură are **tip, sursă, scor, stare**:

| Sursă | Stări posibile |
|---|---|
| XML (declarată de emitent) | confirmată |
| Regulă automată | propusă / confirmată manual / respinsă manual |
| Introdusă manual | confirmată |

**O rulare ulterioară a regulilor nu suprascrie niciodată o decizie manuală.**

### Grupul de documente ca unitate de lucru

Consolidarea nu produce doar o vizualizare arborescentă, ci o **entitate proprie**: grupul, cu valoare netă calculată, membri și istoric.

```
Grup #4412  —  poziție netă: 12.400,00 lei
Factura inițială 125 / 10.03.2026        18.600,00
├── Storno 182 / 25.03.2026             -18.600,00
└── Factură corectată 183 / 25.03.2026   12.400,00
```

**Grupul este unitatea comparată la reconciliere, nu factura individuală.** Această singură decizie elimină categoria cea mai numeroasă de false excepții.

Lanțul complet se interoghează cu `WITH RECURSIVE`, ceea ce permite corecții succesive de orice lungime.

---

## 7. Motorul de reconciliere

### Modelul de import extern

Un singur mecanism generic. Un **profil de import** conține: format (Excel, CSV, interogare SQL), maparea coloanelor sursă → câmpuri canonice, reguli de curățare (formate de dată, separatori zecimali, antete repetate) și tipul sursei (registru contabil, gestiune, comenzi, plăți).

Datele intră într-un tabel de tranzit, unde se validează înainte de confruntare. Un import extern este și el un lot anulabil.

### Algoritmul de potrivire

1. **Grupare (blocking).** Chei ieftine de restrângere — `CIF + lună`, `CIF + total rotunjit` — ca să nu se compare fiecare cu fiecare. La 8.000 documente/lună, comparația exhaustivă e inutilizabilă.
2. **Scorare.** Fiecare pereche candidată primește scor compus din componente ponderate. Ponderile sunt parte din regulă, **nu din cod**.
3. **Rezolvare.** Peste pragul superior → potrivire automată. Sub pragul inferior → lipsă. Între praguri, sau mai mulți candidați peste prag → **ambiguu**, decizie umană.

### Formatul unei reguli

```yaml
regula: efactura_vs_registru_contabil
sursa_a: efactura.grup            # grup, nu factura individuala
sursa_b: import.registru_contabil
grupare: [cif_furnizor, luna_document]
componente:
  - camp: numar_normalizat   pondere: 40   tip: exact_apoi_normalizat
  - camp: cif_furnizor       pondere: 25   tip: exact
  - camp: total              pondere: 25   toleranta: 0.02
  - camp: data_document      pondere: 10   toleranta_zile: 3
praguri:
  acceptare_automata: 90
  exceptie_sub: 60
```

### Prezentarea rezultatului

Pentru fiecare potrivire sau excepție: valorile din ambele surse alăturate, diferențele calculate, regula aplicată și scorul, explicația în cuvinte, starea verificării, observația utilizatorului.

**Stări:** `nouă`, `în lucru`, `rezolvată`, `acceptată ca diferență`, `ignorată` — ultimele două cu motiv obligatoriu și autor.

Rezultatele se persistă. O rulare ulterioară actualizează potrivirile, dar **păstrează deciziile umane**.

### Scenarii

**Față de contabilitate (MVP)** — document în e-Factura neînregistrat contabil; înregistrare contabilă fără document corespondent; diferențe de valoare fără TVA / TVA / total; furnizor sau CIF diferit; factură înregistrată de două ori; storno necorelat.

**Față de gestiune (etapa 2)** — cantitate facturată ≠ recepționată; articol fără corespondent în nomenclator; cod furnizor nemapat; preț facturat ≠ preț din comandă; factură fără recepție și recepție fără factură.

**Față de contracte, comenzi, plăți (etapele 2–4)** — depășirea valorii contractate; facturare în afara perioadei; facturi multiple pentru aceeași perioadă; facturi scadente neachitate; plăți fără document identificat; plată parțială sau dublă; sold furnizor; diferențe de curs.

---

## 8. Controale de completitudine și integritate

Capitolul care onorează P1. Se execută programat, iar rezultatele **generează alerte**, nu doar rânduri în tabloul de bord.

### Completitudine (esențial)

- documente prezente în lista de mesaje ANAF, **lipsă local** — singura verificare care contează cu adevărat;
- documente apropiate de expirarea ferestrei de 60 de zile, cu preaviz configurabil;
- discontinuități în seriile de numere la facturile emise;
- sincronizare eșuată sau neexecutată de N zile.

### Integritate și coerență

- suma liniilor vs. total document vs. total pe cote TVA — triplă verificare;
- cote TVA neobișnuite sau categorii incoerente cu valorile;
- CIF invalid la cifra de control;
- storno fără document de referință;
- ZIP fără documentul așteptat; XML neinterpretabil;
- recalcularea periodică a SHA-256 pe eșantion, pentru coruperea silențioasă.

> **Un tablou de bord nu este o alertă.** Dacă sincronizarea a picat de trei zile, cineva trebuie să afle fără să deschidă aplicația — e-mail sau fișier de stare monitorizat.

---

## 9. Interfața

- **Tablou de bord** — documente noi, nereconciliate, erori de import, documente apropiate de limita de 60 de zile, diferențe deschise.
- **Registru** — listă, cu căutare simplă (un singur câmp: număr, CIF, denumire, descriere de linie, contract, comandă, ID ANAF) și căutare avansată pe criterii combinate.
- **Document** — date structurate, linii, PDF, acces la ZIP și XML, atașamente, istoric, relații.
- **Grupuri** — documente consolidate și poziția netă.
- **Reconciliere** — profiluri de import, rulare, listă de excepții cu filtre, rezolvare.
- **Importuri** — loturi, jurnal, anulare.
- **Administrare** — reguli, mapări, utilizatori, backup.

Interogarea în limbaj natural iese din primele etape. Modelul de limbaj ar trebui folosit doar pentru traducerea întrebării în filtre, valorile rămânând calculate determinist — dar față de o căutare avansată bine făcută aduce demonstrații reușite și puțină valoare operațională.

---

## 10. Arhitectura tehnică

```
Browser (rețea locală)
        │
        ▼
Aplicație web locală  (Flask/FastAPI, servită prin Waitress/uvicorn)
        │           rulează ca serviciu Windows prin NSSM
        ├── Scanner foldere
        ├── Parser XML (lxml, entități externe dezactivate)
        ├── Motor de consolidare
        ├── Motor de reconciliere
        ├── Generator PDF / Excel (openpyxl)
        ├── Planificator intern (APScheduler)
        └── PostgreSQL  ← inclusiv conținutul binar
```

Trei precizări:

- **Celery pe Windows este de evitat** — suport parțial și fragil. APScheduler în procesul serviciului acoperă integral nevoia, fără broker.
- **Nu se distribuie executabil împachetat.** Aplicația rulează pe un server și se accesează din browser — dispare complet problema falselor pozitive de antivirus la fișiere produse cu PyInstaller.
- **Alembic de la prima zi.** O bază care va trăi ani și va traversa versiuni de CIUS nu se administrează cu modificări manuale.

---

## 11. Model de date

Schiță de referință. Numele coloanelor sunt orientative; structura și relațiile nu sunt.

### Ingestie

**`import_batch`** — `id`, `tip` (`scan_local` | `anaf` | `import_extern`), `sursa`, `pornit_la`, `terminat_la`, `stare`, `utilizator`, `nr_fisiere`, `nr_documente`, `nr_erori`, `anulat_la`, `motiv_anulare`

**`source_object`** *(tabel izolat, doar INSERT)* — `id`, `batch_id`, `tip` (`zip` | `xml_factura` | `xml_semnatura` | `atasament` | `pdf`), `continut BYTEA`, `sha256` (indexat), `marime`, `mime`, `nume_original`, `cale_originala`, `creat_la`

**`anaf_message`** — `id`, `cif`, `id_descarcare` (unic), `id_incarcare`, `tip_mesaj`, `data_publicare`, `data_descarcare`, `stare`, `expira_la`

### Document

**`invoice`** — `id`, `batch_id`, `source_object_id`, `anaf_message_id`, `directie` (`intrare` | `iesire`), `cif_emitent`, `cif_beneficiar`, `numar_brut`, `numar_normalizat` (indexat), `serie`, `numar_numeric`, `data_emitere`, `data_scadenta`, `tip_document`, `moneda`, `curs`, `total_fara_tva`, `total_tva`, `total_document`, `total_de_plata`, `nr_contract`, `nr_comanda`, `perioada_start`, `perioada_sfarsit`, `versiune_cius`, `stare`, `creat_la`

- index de avertizare (nu constrângere) pe `(cif_emitent, numar_normalizat, data_emitere)`;
- partiționare pe `data_emitere`, din anul 2.

**`invoice_party`** — `id`, `invoice_id`, `rol` (`furnizor` | `client` | `reprezentant_fiscal`), `denumire`, `cif_brut`, `cif_normalizat`, `nr_reg_com`, `adresa`, `tara`, `cod_tva`, `cont_bancar`, `contact`

**`invoice_line`** — `id`, `invoice_id`, `nr_crt`, `cod_articol_furnizor`, `cod_articol_client`, `descriere`, `cantitate`, `um`, `pret_unitar`, `valoare_fara_tva`, `cota_tva`, `categorie_tva`, `reducere`, `nr_comanda`, `centru_cost`, `xpath`

- index full-text (`tsvector`, configurație română) pe `descriere`.

**`tax_summary`** — `id`, `invoice_id`, `cota`, `categorie`, `baza`, `tva`

**`attachment`** — `id`, `invoice_id`, `source_object_id`, `nume`, `mime`, `descriere`

### Consolidare

**`invoice_relation`** — `id`, `invoice_from`, `invoice_to`, `tip`, `sursa` (`xml` | `regula` | `manual`), `scor`, `stare` (`confirmata` | `propusa` | `respinsa`), `motiv`, `utilizator`, `creat_la`

**`invoice_group`** — `id`, `tip`, `pozitie_neta`, `calculat_la`
**`invoice_group_member`** — `group_id`, `invoice_id`, `semn`

### Reconciliere

**`import_profile`** — `id`, `denumire`, `tip_sursa`, `format`, `mapare JSONB`, `reguli_curatare JSONB`, `activ`

**`external_record`** — `id`, `batch_id`, `profil_id`, `cif`, `numar_brut`, `numar_normalizat`, `data`, `total_fara_tva`, `total_tva`, `total`, `date_brute JSONB`

**`reconciliation_rule`** — `id`, `denumire`, `definitie JSONB` (structura YAML din capitolul 7), `activa`

**`reconciliation_run`** — `id`, `rule_id`, `rulat_la`, `nr_potriviri`, `nr_exceptii`, `nr_ambigue`

**`reconciliation_result`** — `id`, `run_id`, `group_id`, `external_record_id`, `scor`, `stare`, `diferente JSONB`, `decizie`, `motiv`, `utilizator`, `decis_la`

- deciziile umane supraviețuiesc rulărilor ulterioare: la re-rulare se actualizează scorul și diferențele, nu `decizie`/`motiv`/`utilizator`.

### Audit

**`audit_log`** — `id`, `moment`, `utilizator`, `actiune`, `entitate`, `entitate_id`, `detalii JSONB`

---

## 12. Securitate și audit

Redimensionate la realitatea unui administrator unic și a unei rețele locale. Matricea de șase roluri din v1 presupunea o organizație pe care aplicația nu o deservește.

Două roluri: **administrator** și **consultare**. Rămân obligatorii:

- jurnal de operații: cine, când, ce a importat, anulat, exportat, reconciliat;
- imposibilitatea ștergerii documentelor sursă din interfață, aplicată prin `GRANT`;
- credențiale și tokenuri ANAF criptate, în afara codului și în afara depozitului de cod;
- acces limitat la rețeaua locală;
- backup verificat prin **restaurare de probă**, nu prin existența fișierului;
- politică de retenție declarată explicit, chiar dacă implementarea vine mai târziu.

---

## 13. Obiective tehnice de testare

Aplicația este și un banc de probă. O soluție care funcționează, dar din care nu s-a învățat nimic transferabil, este un rezultat pe jumătate.

- stocare binară în PostgreSQL la scară de zeci de GB și efectul asupra backup-ului;
- interogări recursive pe graf de documente (`WITH RECURSIVE`) și limitele lor practice;
- partiționare declarativă și strategii de indexare pe milioane de rânduri;
- full-text search nativ PostgreSQL pe descrieri de linie, în limba română;
- potrivire probabilistică: grupare prealabilă, scorare ponderată, calibrarea pragurilor pe date reale;
- parsare XML sigură și rezistentă la variații de schemă între emitenți;
- serviciu Windows cu planificator intern, fără componente externe;
- migrări de schemă versionate pe o bază cu date de producție.

---

## 14. Etapizare

### Etapa 1 — MVP

- scanner de foldere locale și import manual;
- parsare XML: antet + linii + totaluri pe cote TVA;
- stocare ZIP și XML în bază, cu SHA-256 și deduplicare ierarhică;
- registru cu căutare simplă și avansată; export Excel;
- consolidare: relații explicite din XML + relația factură–storno; grupuri cu poziție netă;
- import registru contabil din Excel/CSV, cu profil configurabil;
- motor de reconciliere cu o regulă completă (e-Factura vs. contabilitate), praguri, excepții;
- loturi de import cu anulare; jurnal de operații;
- controale de coerență: sumă linii vs. total, duplicat, storno orfan.

**Ordinea de execuție contează.** Ingestia și normalizarea sunt partea previzibilă; consolidarea și reconcilierea consumă majoritatea timpului și a surprizelor.

> Ingestia trebuie să fie funcțională și validată pe câteva mii de documente reale **înainte** de prima regulă de reconciliere — altfel pragurile se calibrează pe presupuneri despre date, nu pe date.

**Criterii de terminat pentru MVP:**

1. Un lot de ≥ 1.000 de documente reale se importă fără intervenție, iar erorile sunt toate explicate în jurnal.
2. Reimportarea aceluiași lot nu creează niciun document nou.
3. Pentru fiecare document, suma liniilor = totalul documentului = suma totalurilor pe cote TVA (sau diferența este raportată explicit).
4. Un lot poate fi anulat, iar starea bazei revine identică — verificat prin comparație de sume de control.
5. O pereche factură–storno reală produce automat un grup cu poziția netă corectă.
6. Un registru contabil real importat din Excel produce o listă de excepții pe care un om o poate parcurge — nu mii de false pozitive.

### Etapa 2 — Extinderea reconcilierii

Relații deduse cu propunere și confirmare manuală; reconciliere față de gestiune (recepții, cantități, prețuri, mapare coduri articol); contracte și comenzi, controlul depășirii valorilor; rapoarte de excepții și situații consolidate pe furnizor, contract, proiect.

### Etapa 3 — Integrarea ANAF

Autorizare OAuth și gestiunea ciclului de viață al tokenurilor, cu alertare înainte de expirare; listare mesaje și descărcare pentru unul sau mai multe CIF-uri; sincronizare programată cu reluarea eșecurilor; confruntarea listei ANAF cu conținutul local; monitorizarea ferestrei de 60 de zile.

### Etapa 4 — Opțional

Conectare directă la ERP, reconciliere de plăți, export pentru instrumente de analiză, interogare în limbaj natural, fluxuri de aprobare. Niciuna nu condiționează valoarea produsului.

---

## 15. Riscuri

| Risc | Efect | Atenuare |
|---|---|---|
| Calitatea datelor externe (registru contabil exportat manual) | cea mai probabilă cauză de eșec al reconcilierii | validare la import, raport de calitate a sursei înainte de comparație |
| Emitenți care nu completează referințele opționale | relațiile explicite acoperă puțin din realitate | regulile de deducere devin obligatorii, nu opționale |
| Expirarea tokenului ANAF (etapa 3) | sincronizare oprită tăcut | alertă cu preaviz + control de completitudine independent |
| Creșterea bazei peste capacitatea strategiei de backup | recuperare imposibilă la nevoie | backup fizic din prima zi, restaurare de probă periodică |
| Efort subestimat la reconciliere | MVP care nu se termină | 80% din excepții provin de la 20% dintre furnizori — se tratează întâi aceștia |
| Schimbare de versiune RO-CIUS | extrageri incoerente între ani | versiunea schemei stocată pe document; reprelucrare selectivă |

---

## 16. Concluzie

Produsul nu este un depozit de fișiere și nici un descărcător de facturi. Este un **registru local al documentelor electronice și al relațiilor dintre ele**, construit pe trei premise: fereastra de disponibilitate limitată, informația neexploatată de la nivel de linie și necesitatea confruntării cu alte sisteme.

Componentele care produc efectiv valoare, în ordine:

1. captura completă și verificabilă, cu control de completitudine — nu doar import reușit;
2. normalizarea până la nivel de linie, cu trasabilitate către XML;
3. consolidarea documentelor legate în grupuri cu poziție netă, ca unitate de comparație;
4. reconcilierea configurabilă, cu scorare și praguri, față de surse externe;
5. explicarea fiecărei diferențe și păstrarea istoricului rezolvării ei.

Restul — interfața, exporturile, integrările directe, limbajul natural — sunt utile, dar niciuna nu justifică singură existența aplicației.
