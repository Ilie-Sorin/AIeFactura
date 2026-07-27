# AIeFactura

Registru local de documente RO e-Factura și motor de reconciliere — vezi
[`Specificatie_AIeFactura.md`](Specificatie_AIeFactura.md) pentru specificația completă.

Stare curentă: **Checkpoint C** (ingestie, normalizare, stocare, deduplicare,
consolidare în grupuri, motor de reconciliere, UI de bază) — vezi „Ce urmează"
mai jos pentru ce nu e încă implementat. Acesta e nucleul funcțional descris
în cap. 1 (P1-P3) al specificației: captură + normalizare + consolidare +
reconciliere.

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
  scheduler.py      # APScheduler — monitorizare periodică WATCH_DIRECTORIES
  models/          # ORM — toate tabelele din cap. 11 al specificației
  services/
    xml_parser.py      # parser UBL/RO-CIUS, XXE-safe
    normalize_number.py, normalize_cif.py
    dedup.py            # deduplicare ierarhică (3 niveluri)
    integrity.py        # tripla verificare sumă linii/total/TVA, CIF
    ingest.py           # orchestrare lot de import
    consolidation.py    # relații explicite/deduse, grup + poziție netă (WITH RECURSIVE)
    external_import.py  # profil Excel/CSV -> external_record (tabel de tranzit)
    reconciliation.py   # blocking + scorare ponderată + praguri (cap. 7)
    scanner.py          # scanare recursivă foldere
    audit.py            # jurnal de operații
  routers/         # dashboard, registru, documente, grupuri, relații, reconciliere, importuri, admin, auth
  templates/       # Jinja2 + htmx (vendorizat local, fără CDN)
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

## Ce urmează (nu e implementat încă)

- **Completitudine/integritate programată** (cap. 8) și alertare (dincolo de triple-check-ul rulat la ingestie).
- **Export** (structură de foldere ZIP, Excel) și **PDF** cu foaia de stil oficială ANAF.
- **Sincronizare ANAF** (etapa 3 din specificație) — schema (`anaf_message`) e pregătită, fluxul OAuth nu e implementat.
- Căutare avansată combinată (doar căutarea simplă e implementată).

## Producție (schiță, nu automatizat de acest build)

- **Serviciu Windows**: NSSM, pornind `uvicorn app.main:app` (fără `--reload`)
  cu variabilele din `.env` încărcate în mediul serviciului. Nu s-a instalat
  automat niciun serviciu — e o acțiune la nivel de sistem, de rulat explicit
  de administrator pe mașina de producție.
- **Backup**: `pg_basebackup` + arhivare WAL, cu restaurare de probă periodică
  (cap. 12) — `pg_dump` devine incomod peste câțiva GB (cap. 2). De configurat
  pe serverul de producție, nu documentat mai departe aici.
- **Politica de retenție**: de declarat explicit înainte de punerea în producție.
