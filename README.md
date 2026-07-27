# AIeFactura

Registru local de documente RO e-Factura și motor de reconciliere — vezi
[`Specificatie_AIeFactura.md`](Specificatie_AIeFactura.md) pentru specificația completă.

Stare curentă: **Checkpoint A** (ingestie, normalizare, stocare, deduplicare,
UI de bază) — vezi „Ce urmează" mai jos pentru ce nu e încă implementat.

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
    scanner.py          # scanare recursivă foldere
    audit.py            # jurnal de operații
  routers/         # dashboard, registru, documente, importuri, admin, auth
  templates/       # Jinja2 + htmx (vendorizat local, fără CDN)
migrations/        # Alembic — o singură migrare inițială deocamdată
tests/
  unit/            # parser, normalizare, deduplicare, integritate
  integration/     # capăt-la-capăt (import → reimport → anulare), scanner, auth
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

## Ce urmează (nu e implementat încă)

- **Consolidare** (cap. 6): relații deduse, grupuri cu poziție netă, interogare `WITH RECURSIVE`.
- **Reconciliere** (cap. 7): profiluri de import extern, motor de scorare/praguri.
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
