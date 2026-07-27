"""Import extern generic (cap. 7): profil configurabil -> external_record."""

import io
from datetime import date
from decimal import Decimal

import openpyxl

from app.models.reconciliation import ExternalRecord, ImportProfile
from app.services.external_import import import_external_records
from app.services.ingest import start_batch

MAPARE = {
    "cif": "CIF",
    "numar_brut": "Numar",
    "data": "Data",
    "total_fara_tva": "Fara TVA",
    "total_tva": "TVA",
    "total": "Total",
}


def _make_profile(session, **overrides) -> ImportProfile:
    defaults = dict(
        denumire="Registru contabil test",
        tip_sursa="registru_contabil",
        format="csv",
        mapare=MAPARE,
        reguli_curatare={
            "format_data": "%d.%m.%Y",
            "separator_zecimal": ",",
            "separator_csv": ";",
        },
        activ=True,
    )
    defaults.update(overrides)
    profile = ImportProfile(**defaults)
    session.add(profile)
    session.flush()
    return profile


def test_csv_import_parses_romanian_number_and_date_formats(db_session_commit):
    session = db_session_commit
    profile = _make_profile(session)
    batch = start_batch(session, tip="import_extern", sursa="test")

    csv_content = (
        "CIF;Numar;Data;Fara TVA;TVA;Total\n"
        "RO 018547290;0001234;10.03.2026;15630,00;2969,70;18599,70\n"
    ).encode("utf-8-sig")

    records = import_external_records(session, profile, batch, csv_content)
    session.commit()

    assert len(records) == 1
    rec = records[0]
    assert rec.cif == "18547290"  # prefix RO + spatii + zerouri eliminate
    assert rec.numar_brut == "0001234"
    assert rec.numar_normalizat == "1234"
    assert rec.data == date(2026, 3, 10)
    assert rec.total_fara_tva == Decimal("15630.00")
    assert rec.total_tva == Decimal("2969.70")
    assert rec.total == Decimal("18599.70")
    assert rec.date_brute["CIF"] == "RO 018547290"


def test_csv_import_skips_invalid_rows_without_stopping(db_session_commit):
    session = db_session_commit
    profile = _make_profile(session)
    batch = start_batch(session, tip="import_extern", sursa="test")

    csv_content = (
        "CIF;Numar;Data;Fara TVA;TVA;Total\n"
        "185472901;0001234;10.03.2026;15630,00;2969,70;18599,70\n"
        ";;;;;\n"  # rand gol, de sarit
        "185472901;0005555;nu-e-o-data;100,00;19,00;\n"  # fara total -> eroare
        "143981007;0009999;15.03.2026;500,00;95,00;595,00\n"
    ).encode("utf-8-sig")

    records = import_external_records(session, profile, batch, csv_content)
    session.commit()

    assert len(records) == 2
    assert batch.nr_erori == 1
    assert session.query(ExternalRecord).count() == 2


def test_excel_import_with_dot_decimal_and_native_types(db_session_commit):
    session = db_session_commit
    profile = _make_profile(
        session,
        format="excel",
        reguli_curatare={"format_data": "%d.%m.%Y", "separator_zecimal": "."},
    )
    batch = start_batch(session, tip="import_extern", sursa="test")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["CIF", "Numar", "Data", "Fara TVA", "TVA", "Total"])
    ws.append([185472901, "0001234", "10.03.2026", 15630.00, 2969.70, 18599.70])
    buffer = io.BytesIO()
    wb.save(buffer)

    records = import_external_records(session, profile, batch, buffer.getvalue())
    session.commit()

    assert len(records) == 1
    rec = records[0]
    assert rec.cif == "185472901"  # celula numerica Excel -> intreg -> text
    assert rec.total == Decimal("18599.7")
