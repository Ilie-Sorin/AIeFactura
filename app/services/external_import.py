"""Import extern generic (cap. 7): un profil configurabil (format, mapare
coloane sursă → câmpuri canonice, reguli de curățare) transformă un fișier
Excel/CSV într-un tabel de tranzit (`external_record`). Un import extern e
și el un lot anulabil, ca oricare altul (cap. 4)."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import openpyxl
from sqlalchemy.orm import Session

from app.models.ingestion import ImportBatch
from app.models.reconciliation import ExternalRecord, ImportProfile
from app.services.audit import write_audit
from app.services.normalize_cif import normalize_cif
from app.services.normalize_number import normalize_invoice_number, resolve_numbering_config

CAMPURI_CANONICE = ("cif", "numar_brut", "data", "total_fara_tva", "total_tva", "total")


class ExternalImportError(Exception):
    pass


def _cell_to_str(valoare) -> str | None:
    if valoare is None:
        return None
    if isinstance(valoare, float) and valoare.is_integer():
        return str(int(valoare))
    text = str(valoare).strip()
    return text or None


def _parse_decimal(valoare, separator_zecimal: str) -> Decimal | None:
    if valoare is None:
        return None
    if isinstance(valoare, (int, float, Decimal)):
        return Decimal(str(valoare))
    text = str(valoare).strip()
    if not text:
        return None
    text = text.replace(" ", "")
    if separator_zecimal == ",":
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _parse_date(valoare, format_data: str) -> date | None:
    if valoare is None:
        return None
    if isinstance(valoare, datetime):
        return valoare.date()
    if isinstance(valoare, date):
        return valoare
    text = str(valoare).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, format_data).date()
    except ValueError:
        return None


def _json_safe(rand: dict) -> dict:
    out = {}
    for cheie, valoare in rand.items():
        if isinstance(valoare, (datetime, date)):
            out[cheie] = valoare.isoformat()
        elif isinstance(valoare, Decimal):
            out[cheie] = str(valoare)
        else:
            out[cheie] = valoare
    return out


def _read_rows(profile: ImportProfile, continut: bytes) -> list[dict]:
    curatare = profile.reguli_curatare or {}
    randuri_de_sarit = int(curatare.get("randuri_antet_de_sarit", 0))

    if profile.format == "excel":
        workbook = openpyxl.load_workbook(io.BytesIO(continut), data_only=True, read_only=True)
        foaie_nume = curatare.get("foaie")
        worksheet = workbook[foaie_nume] if foaie_nume else workbook.worksheets[0]
        rows = list(worksheet.iter_rows(values_only=True))
        workbook.close()
    elif profile.format == "csv":
        separator = curatare.get("separator_csv", ",")
        text = continut.decode(curatare.get("encoding", "utf-8-sig"))
        rows = list(csv.reader(io.StringIO(text), delimiter=separator))
    else:
        raise ExternalImportError(f"format necunoscut în profil: {profile.format!r}")

    rows = rows[randuri_de_sarit:]
    if not rows:
        return []
    antet = [str(c).strip() if c is not None else "" for c in rows[0]]
    randuri = []
    for r in rows[1:]:
        if r is None or all(c is None or str(c).strip() == "" for c in r):
            continue
        randuri.append({antet[i]: r[i] for i in range(min(len(antet), len(r)))})
    return randuri


def import_external_records(
    session: Session,
    profile: ImportProfile,
    batch: ImportBatch,
    continut: bytes,
    *,
    utilizator_id: int | None = None,
) -> list[ExternalRecord]:
    """Nu oprește lotul la un rând nevalid (cap. 4A) — rândul se sare, se
    numără ca eroare și se auditează cu conținutul brut, pentru diagnostic."""
    curatare = profile.reguli_curatare or {}
    format_data = curatare.get("format_data", "%d.%m.%Y")
    separator_zecimal = curatare.get("separator_zecimal", ",")
    mapare = profile.mapare

    randuri = _read_rows(profile, continut)
    batch.nr_fisiere += 1

    create: list[ExternalRecord] = []
    for idx, rand in enumerate(randuri, start=1):
        def val(camp: str):
            coloana = mapare.get(camp)
            return rand.get(coloana) if coloana else None

        cif = normalize_cif(_cell_to_str(val("cif")))
        numar_brut = _cell_to_str(val("numar_brut"))
        data_document = _parse_date(val("data"), format_data)
        total_fara_tva = _parse_decimal(val("total_fara_tva"), separator_zecimal)
        total_tva = _parse_decimal(val("total_tva"), separator_zecimal)
        total = _parse_decimal(val("total"), separator_zecimal)

        if not cif or not numar_brut or total is None:
            batch.nr_erori += 1
            write_audit(
                session,
                "eroare_import_extern",
                entitate="import_batch",
                entitate_id=batch.id,
                detalii={"rand": idx, "date_brute": _json_safe(rand)},
                utilizator_id=utilizator_id,
            )
            continue

        config = resolve_numbering_config(session, cif)
        numar_normalizat = normalize_invoice_number(numar_brut, config).normalizata

        record = ExternalRecord(
            batch_id=batch.id,
            profil_id=profile.id,
            cif=cif,
            numar_brut=numar_brut,
            numar_normalizat=numar_normalizat,
            data=data_document,
            total_fara_tva=total_fara_tva,
            total_tva=total_tva,
            total=total,
            date_brute=_json_safe(rand),
        )
        session.add(record)
        create.append(record)

    batch.nr_documente += len(create)
    session.flush()
    return create
