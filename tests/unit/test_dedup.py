from datetime import date
from decimal import Decimal

from app.models.document import Invoice
from app.models.ingestion import ImportBatch, SourceObject
from app.services.dedup import DedupOutcome, check_duplicate


def _make_invoice(
    session,
    *,
    cif="185472901",
    numar="1234",
    data=date(2026, 3, 10),
    total=Decimal("100.00"),
    sha256="a" * 64,
):
    batch = ImportBatch(tip="scan_local", stare="in_curs")
    session.add(batch)
    session.flush()
    source = SourceObject(batch_id=batch.id, tip="xml_factura", continut=b"<x/>", sha256=sha256, marime=4)
    session.add(source)
    session.flush()
    invoice = Invoice(
        batch_id=batch.id,
        source_object_id=source.id,
        directie="intrare",
        cif_emitent=cif,
        numar_brut=numar,
        numar_normalizat=numar,
        data_emitere=data,
        total_fara_tva=total,
        total_tva=Decimal("0.00"),
        total_document=total,
        stare="indexat",
    )
    session.add(invoice)
    session.flush()
    return invoice


def test_nou_when_nothing_matches(db_session):
    result = check_duplicate(
        db_session,
        sha256_xml="b" * 64,
        cif_emitent="185472901",
        numar_normalizat="9999",
        data_emitere=date(2026, 1, 1),
        total_document=Decimal("50.00"),
    )
    assert result.outcome == DedupOutcome.NOU


def test_duplicat_cert_continut_same_xml_sha256(db_session):
    invoice = _make_invoice(db_session, sha256="c" * 64)
    result = check_duplicate(
        db_session,
        sha256_xml="c" * 64,
        cif_emitent="185472901",
        numar_normalizat="9999",
        data_emitere=date(2099, 1, 1),
        total_document=Decimal("999.00"),
    )
    assert result.outcome == DedupOutcome.DUPLICAT_CERT_CONTINUT
    assert result.existing_invoice_id == invoice.id


def test_duplicat_probabil_same_tuple(db_session):
    invoice = _make_invoice(
        db_session, cif="185472901", numar="1234", data=date(2026, 3, 10), total=Decimal("100.00")
    )
    result = check_duplicate(
        db_session,
        sha256_xml="d" * 64,
        cif_emitent="185472901",
        numar_normalizat="1234",
        data_emitere=date(2026, 3, 10),
        total_document=Decimal("100.00"),
    )
    assert result.outcome == DedupOutcome.DUPLICAT_PROBABIL
    assert result.existing_invoice_id == invoice.id


def test_numar_duplicat_valori_diferite(db_session):
    invoice = _make_invoice(
        db_session, cif="185472901", numar="1234", data=date(2026, 3, 10), total=Decimal("100.00")
    )
    result = check_duplicate(
        db_session,
        sha256_xml="e" * 64,
        cif_emitent="185472901",
        numar_normalizat="1234",
        data_emitere=date(2026, 3, 10),
        total_document=Decimal("250.00"),
    )
    assert result.outcome == DedupOutcome.NUMAR_DUPLICAT_VALORI_DIFERITE
    assert result.existing_invoice_id == invoice.id
