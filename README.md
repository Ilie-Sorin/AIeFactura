# AIeFactura

Registru local de documente RO e-Factura și motor de reconciliere — vezi
[`Specificatie_AIeFactura.md`](Specificatie_AIeFactura.md) pentru specificația completă.

Stare curentă: **Checkpoint D** — Etapa 1 (MVP) din specificație e completă:
ingestie, normalizare, stocare, deduplicare, consolidare în grupuri, motor de
reconciliere, monitorizare/alertare, export, vizualizare, căutare avansată.
Singura piesă rămasă neimplementată e sincronizarea ANAF (etapa 3) — vezi
„Ce urmează" mai jos.

## Stack

FastAPI + uvicorn, PostgreSQL, SQLAlchemy 2.x + Alembic, lxml, Jinja2 + htmx
(fără build pipeline JS), APScheduler în procesul aplicației (fără Celery).

## Pornire rapidă (dezvoltare)

Necesită Python 3.13 și Docker Desktop.

```bash
# 1. Postgres de dezvoltare (port 5433 pe host -- 5432 poate fi ocupat de o
#    instalare Postgres nativa pe masina)
docker compose -f docker-compose.dev.yml up -d

# 2. Mediu virtual + dependente
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt

# 3. Configurare locala
cp .env.example .env
# genereaza o cheie proprie pentru SECRET_KEY:
.venv/Scripts/python -c "import secrets; print(secrets.token_hex(32))"

# 4. Schema bazei de date
.venv/Scripts/python -m alembic upgrade head

# 5. Primul utilizator administrator
.venv/Scripts/python scripts/create_admin.py admin <parola-aleasa>

# 6. Pornire aplicatie
.venv/Scripts/python -m uvicorn app.main:app --reload
```

Aplicația e disponibilă la `http://127.0.0.1:8000`.

## Teste

```bash
.venv/Scripts/python -m pytest
```

Testele creează și distrug o bază separată `aiefactura_test` pe același
container Postgres de dezvoltare (vezi `tests/conftest.py`).

## Arhitectură

```
app/
  main.py          # FastAPI app, sesiune, routere, lifespan (scheduler)
  config.py        # setări din .env (pydantic-settings)
  db.py            # engine/sesiune SQLAlchemy
  security.py      # autentificare (2 roluri), hashing parole
  scheduler.py     # APScheduler — scanare periodică + verificări de integritate
  models/          # ORM — toate tabelele din cap. 11 al specificației (+ integrity_alert)
  services/
    xml_parser.py      # parser UBL/RO-CIUS, XXE-safe
    normalize_number.py, normalize_cif.py
    dedup.py            # deduplicare ierarhică (3 niveluri)
    integrity.py        # tripla verificare sumă linii/total/TVA, CIF (la ingestie)
    ingest.py           # orchestrare lot de import
    consolidation.py    # relații explicite/deduse, grup + poziție netă (WITH RECURSIVE)
    external_import.py  # profil Excel/CSV -> external_record (tabel de tranzit)
    reconciliation.py   # blocking + scorare ponderată + praguri (cap. 7)
    monitoring.py       # verificări periodice de completitudine/integritate (cap. 8)
    alerting.py         # fișier de stare + email (alertare activă)
    export.py           # structură de foldere ZIP + Excel (cap. 3, 9)
    pdf.py               # vizualizare XML->HTML cu XSLT-ul oficial ANAF
    scanner.py          # scanare recursivă foldere
    audit.py            # jurnal de operații
  routers/         # dashboard, registru, documente, grupuri, relații, reconciliere, monitorizare, importuri, admin, auth
  templates/       # Jinja2 + htmx (vendorizat local, fără CDN)
  resources/       # stylesheet ANAF (fișier extern, de adăugat manual — vezi README propriu)
migrations/        # Alembic — o migrare per schimbare de schemă, de la primul commit
tests/
  unit/            # parser, normalizare, deduplicare, integritate
  integration/     # capăt-la-capăt (import → reimport → anulare), scanner, auth, consolidare, reconciliere
  fixtures/        # facturi UBL sintetice (nu există eșantioane reale ANAF disponibile)
db/init/           # rol Postgres de runtime, cu privilegii restrânse (GRANT)
```

### Imutabilitatea `source_object`

Aplicația se conectează cu rolul `aiefactura_app`, care **nu** are drept de
`UPDATE`/`DELETE` pe tabelul `source_object` — impus prin `GRANT`/`REVOKE`
(vezi `db/init/01-app-role.sql` și migrarea inițială), nu doar prin convenție
în cod. Migrările Alembic rulează cu rolul proprietar `aiefactura`
(`DATABASE_URL_MIGRATIONS`), niciodată cu rolul de runtime.

### Deduplicare

Trei niveluri, în ordine descrescătoare de certitudine — vezi `app/services/dedup.py`:
1. `id_descarcare` ANAF (schema pregătită; sincronizarea ANAF propriu-zisă e etapa 3)
2. SHA-256 pe XML-ul facturii
3. Tuplul (CIF emitent, număr normalizat, dată, total) — semnalat, nu blocant

### Consolidare (cap. 6)

Vezi `app/services/consolidation.py`. Relații **explicite** (din `cac:BillingReference`
al XML-ului, `sursa='xml'`, auto-confirmate) și relații **deduse** (aceeași
valoare + tip complementar factură/credit în fereastra de 90 zile, sau aceeași
comandă/contract — `sursa='regula'`, rămân `propusa` până la decizie umană din
UI). Referințele care nu se pot rezolva imediat (documentul-țintă nu a sosit
încă) se rețin în `invoice.referinte_xml` și se reîncearcă la fiecare import
ulterior de la același furnizor.

Grupul (`invoice_group`) e componenta conexă peste relațiile **confirmate**,
calculată cu o interogare recursivă (`WITH RECURSIVE`) — se recalculează
automat la fiecare import, la fiecare confirmare/respingere de relație și la
anularea unui lot (gestionează corect atât unirea cât și despărțirea grupurilor).

### Reconciliere (cap. 7)

Vezi `app/services/external_import.py` și `app/services/reconciliation.py`.
Un **profil de import** (`import_profile`) mapează coloanele unui Excel/CSV
la câmpuri canonice și definește regulile de curățare (format dată, separator
zecimal); fișierele importate devin `external_record`, într-un lot anulabil
ca oricare altul.

O **regulă de reconciliere** (`reconciliation_rule.definitie`, JSONB) descrie
grupare (blocking — momentan `cif_furnizor`/`luna_document`), componente
ponderate (`numar_normalizat`, `cif_furnizor`, `total`, `data_document`, cu
toleranțe) și praguri de decizie. **Grupul, nu factura individuală**, e
unitatea comparată — poziția netă și numerele tuturor membrilor intră în scor.

Peste pragul de acceptare automată (și fără ambiguitate) → `rezolvata` automat.
Sub pragul de excepție → nu se reține (prea slab ca să conteze). Între praguri,
sau candidați multipli peste prag → rămâne `noua`, pentru decizie umană
(confirmare / acceptare ca diferență / ignorare — ultimele două cu motiv
obligatoriu, impus și la nivel de serviciu). O rulare nouă recalculează
scorul și diferențele pentru toată lumea, dar **copiază mai departe** orice
decizie deja luată de un om (identificată prin `utilizator_id IS NOT NULL`).

### Monitorizare și alertare (cap. 8)

Vezi `app/services/monitoring.py` și `app/services/alerting.py`. Șapte
verificări rulează periodic (interval configurabil, `INTEGRITY_CHECK_INTERVAL_MINUTES`,
implicit 60 min) sau la cerere (buton „Rulează verificările acum" pe
dashboard): discontinuități în seriile de facturi emise, scanare automată
neexecutată de N zile, documente cu eroare de integritate, TVA neobișnuit
sau incoerent cu categoria, CIF invalid (emitent și beneficiar), storno
orfan, recalcularea SHA-256 pe un eșantion de surse binare (coruperea
silențioasă). Rezultatele devin `integrity_alert` — niciodată șterse, doar
marcate rezolvate (manual sau automat, când condiția dispare la o rulare
ulterioară).

Alertarea e **activă**, nu doar pasivă pe dashboard: la fiecare rulare se
scrie un fișier de stare (`STATUS_FILE_PATH`, implicit
`./data/status_monitorizare.json`) verificabil de un instrument extern (task
programat Windows, Nagios/Zabbix etc.), iar dacă există alerte critice
deschise și SMTP e configurat (`SMTP_*` în `.env`), se trimite un email —
best-effort, o eroare de trimitere nu oprește verificările.

Rămân documentat neimplementate (depind de sincronizarea ANAF, etapa 3):
documente prezente în lista de mesaje ANAF dar lipsă local, proximitatea de
expirarea ferestrei de 60 de zile.

### Export și vizualizare (cap. 3, 9)

`app/services/export.py` reconstituie structura de foldere din cap. 3 ca
ZIP, la cerere — pentru un singur document sau pentru rezultatele curente
ale registrului (inclusiv cu filtrele de căutare avansată aplicate); același
ecran oferă și export Excel al listei. Structura de stocare (bază de date)
rămâne separată de formatul de export/schimb (foldere).

Vizualizarea documentului cu foaia de stil oficială ANAF (`app/services/pdf.py`)
transformă XML-ul cu XSLT în HTML identic cu al vizualizatorului oficial;
fișierul `.xsl` **nu e distribuit în acest depozit** (proprietate ANAF, se
adaugă manual — vezi `app/resources/README.md`). PDF-ul propriu-zis se obține
din browser (Print → Salvează ca PDF) — s-a preferat această variantă unei
dependențe suplimentare de randare HTML→PDF (weasyprint/wkhtmltopdf), care ar
fi ieșit din stack-ul impus prin specificație și ar fi adus fragilitate pe
Windows.

## Ce urmează (nu e implementat încă)

- **Sincronizare ANAF** (etapa 3 din specificație): autorizare OAuth, gestiunea
  ciclului de viață al tokenurilor cu alertare la expirare, listare/descărcare
  mesaje, confruntarea listei ANAF cu conținutul local, monitorizarea ferestrei
  de 60 de zile. Schema (`anaf_message`) e pregătită, fluxul nu e implementat.
- Etapa 2 din specificație (reconciliere față de gestiune/comenzi/contracte,
  relații deduse cu prag de confirmare mai sofisticat, rapoarte consolidate).

## Producție

- **Serviciu Windows (NSSM)** — nu s-a instalat automat niciun serviciu (acțiune
  de sistem, de rulat explicit de administrator pe mașina de producție):

  ```powershell
  # Din directorul unde a fost descarcat nssm.exe (https://nssm.cc)
  nssm install AIeFactura "D:\AIeFactura\.venv\Scripts\python.exe" "-m uvicorn app.main:app --host 0.0.0.0 --port 8000"
  nssm set AIeFactura AppDirectory "D:\AIeFactura"
  nssm set AIeFactura AppEnvironmentExtra "PYTHONUNBUFFERED=1"
  # variabilele din .env se incarca de pydantic-settings la pornire -- .env
  # trebuie sa existe in AppDirectory; NU se pun secrete in comanda NSSM insasi
  nssm set AIeFactura AppStdout "D:\AIeFactura\logs\stdout.log"
  nssm set AIeFactura AppStderr "D:\AIeFactura\logs\stderr.log"
  nssm set AIeFactura Start SERVICE_AUTO_START
  nssm start AIeFactura
  ```

  Migrările (`alembic upgrade head`) se rulează manual înainte de a porni
  serviciul, la fiecare actualizare de versiune — nu automat la pornire (o
  migrare eșuată nu trebuie să lase serviciul într-o buclă de restart).

- **Backup fizic** (`pg_dump` devine incomod peste câțiva GB — cap. 2):

  ```powershell
  # Bază completă, o dată (sau la fiecare recreare de mediu)
  pg_basebackup -h localhost -U aiefactura -D D:\Backup\base -Fp -Xs -P

  # Arhivare WAL continuă -- in postgresql.conf:
  #   archive_mode = on
  #   archive_command = 'copy "%p" "D:\\Backup\\wal\\%f"'
  ```

  **Restaurarea de probă e obligatorie periodic** (cap. 12: „backup verificat
  prin restaurare de probă, nu prin existența fișierului") — un backup
  netestat nu e un backup verificat.

- **Politica de retenție**: de declarat explicit înainte de punerea în
  producție (cât timp se păstrează sursele binare, loturile anulate, jurnalul
  de audit) — implementarea ei efectivă (retenție automată) nu face parte din
  acest build.
