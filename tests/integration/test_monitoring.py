"""Controale de completitudine/integritate + alertare (cap. 8)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.consolidation import InvoiceRelation
from app.models.document import Invoice, InvoiceLine
from app.models.ingestion import ImportBatch, SourceObject
from app.models.monitoring import IntegrityAlert
from app.security import create_user
from app.services.monitoring import (
    Finding,
    check_invalid_cif,
    check_numbering_gaps,
    check_orphan_storno,
    check_source_checksum_sample,
    check_unusual_vat,
    reconcile_alerts,
    resolve_alert,
)


def _make_invoice(
    session,
    *,
    cif_emitent="185472901",
    cif_beneficiar="143981007",
    numar="1",
    serie="FCT",
    numar_numeric=1,
    directie="intrare",
    tip_document="380",
    total=Decimal("100.00"),
    data=date(2026, 3, 10),
):
    batch = session.get(ImportBatch, 1) or ImportBatch(tip="scan_local", stare="in_curs")
    if batch.id is None:
        session.add(batch)
        session.flush()
    source = SourceObject(
        batch_id=batch.id, tip="xml_factura", continut=b"<x/>", sha256="a" * 63 + str(numar_numeric % 10), marime=4
    )
    session.add(source)
    session.flush()
    invoice = Invoice(
        batch_id=batch.id,
        source_object_id=source.id,
        directie=directie,
        cif_emitent=cif_emitent,
        cif_beneficiar=cif_beneficiar,
        numar_brut=numar,
        numar_normalizat=numar,
        serie=serie,
        numar_numeric=numar_numeric,
        tip_document=tip_document,
        data_emitere=data,
        total_fara_tva=total,
        total_tva=Decimal("0.00"),
        total_document=total,
        stare="indexat",
    )
    session.add(invoice)
    session.flush()
    return invoice, source


def test_numbering_gaps_detected_only_for_outgoing_series(db_session):
    _make_invoice(db_session, directie="iesire", serie="FCT", numar_numeric=1, numar="1")
    _make_invoice(db_session, directie="iesire", serie="FCT", numar_numeric=2, numar="2")
    _make_invoice(db_session, directie="iesire", serie="FCT", numar_numeric=5, numar="5")

    findings = check_numbering_gaps(db_session)
    assert len(findings) == 1
    assert findings[0].cod == "discontinuitate_serie"
    assert findings[0].detalii["nr_lipsa_total"] == 2  # lipsesc 3 si 4
    assert findings[0].detalii["lipsa"] == [3, 4]


def test_numbering_gaps_ignores_incoming_invoices(db_session):
    _make_invoice(db_session, directie="intrare", serie="FCT", numar_numeric=1, numar="1")
    _make_invoice(db_session, directie="intrare", serie="FCT", numar_numeric=5, numar="5")
    assert check_numbering_gaps(db_session) == []


def test_unusual_vat_rate_is_flagged(db_session):
    invoice, _ = _make_invoice(db_session)
    db_session.add(
        InvoiceLine(invoice_id=invoice.id, nr_crt=1, descriere="x", cota_tva=Decimal("24"), categorie_tva="S")
    )
    db_session.flush()

    findings = check_unusual_vat(db_session)
    assert len(findings) == 1
    assert findings[0].cheie == f"invoice={invoice.id}"


def test_standard_vat_rate_not_flagged(db_session):
    invoice, _ = _make_invoice(db_session)
    db_session.add(
        InvoiceLine(invoice_id=invoice.id, nr_crt=1, descriere="x", cota_tva=Decimal("19"), categorie_tva="S")
    )
    db_session.flush()
    assert check_unusual_vat(db_session) == []


def test_invalid_cif_detected_for_emitent_and_beneficiar(db_session):
    invoice, _ = _make_invoice(db_session, cif_emitent="185472900", cif_beneficiar="143981001")
    findings = check_invalid_cif(db_session)
    assert len(findings) == 1
    roluri = {i["rol"] for i in findings[0].detalii["invalide"]}
    assert roluri == {"emitent", "beneficiar"}


def test_orphan_storno_flagged_until_linked(db_session):
    original, _ = _make_invoice(db_session, numar="1", numar_numeric=1, tip_document="380")
    storno, _ = _make_invoice(db_session, numar="2", numar_numeric=2, tip_document="381")

    findings = check_orphan_storno(db_session)
    assert len(findings) == 1
    assert findings[0].cheie == f"invoice={storno.id}"

    db_session.add(
        InvoiceRelation(invoice_from=storno.id, invoice_to=original.id, tip="storno", sursa="xml", stare="confirmata")
    )
    db_session.flush()
    assert check_orphan_storno(db_session) == []


def test_source_checksum_sample_detects_corruption(db_session):
    _invoice, source = _make_invoice(db_session)
    # simulam coruperea silentioasa: continutul difera de SHA-256-ul inregistrat
    # la import (posibil doar direct in DB -- rolul de runtime nu are UPDATE)
    source.continut = b"<x>modificat</x>"
    db_session.flush()

    findings = check_source_checksum_sample(db_session, esantion=10)
    assert len(findings) == 1
    assert findings[0].cod == "coruptie_silentioasa"
    assert findings[0].detalii["source_object_id"] == source.id


def test_reconcile_alerts_is_idempotent_and_auto_resolves(db_session):
    finding = Finding(cod="test_cod", nivel="avertisment", cheie="k1", mesaj="mesaj initial")

    reconcile_alerts(db_session, "test_cod", [finding])
    alerte = db_session.query(IntegrityAlert).filter_by(cod="test_cod").all()
    assert len(alerte) == 1
    assert alerte[0].rezolvat_la is None

    # a doua rulare cu ACEEASI cheie -- actualizeaza, nu duplica
    finding2 = Finding(cod="test_cod", nivel="avertisment", cheie="k1", mesaj="mesaj actualizat")
    reconcile_alerts(db_session, "test_cod", [finding2])
    alerte = db_session.query(IntegrityAlert).filter_by(cod="test_cod").all()
    assert len(alerte) == 1
    assert alerte[0].mesaj == "mesaj actualizat"

    # a treia rulare fara findings -- alerta se auto-rezolva, nu se sterge
    reconcile_alerts(db_session, "test_cod", [])
    alerte = db_session.query(IntegrityAlert).filter_by(cod="test_cod").all()
    assert len(alerte) == 1
    assert alerte[0].rezolvat_la is not None
    assert alerte[0].rezolvat_automat is True


def test_resolve_alert_manual(db_session):
    utilizator = create_user(db_session, "operator_monitorizare", "parola123")
    db_session.flush()
    reconcile_alerts(db_session, "test_cod", [Finding(cod="test_cod", nivel="critic", cheie="k2", mesaj="m")])
    alerta = db_session.query(IntegrityAlert).filter_by(cod="test_cod").one()

    resolve_alert(db_session, alerta.id, utilizator_id=utilizator.id, motiv="cunoscut, ignorat")

    db_session.refresh(alerta)
    assert alerta.rezolvat_la is not None
    assert alerta.rezolvat_automat is False
    assert alerta.rezolvat_de_id == utilizator.id
    assert alerta.motiv_rezolvare == "cunoscut, ignorat"


def test_resolve_alert_missing_raises(db_session):
    with pytest.raises(ValueError):
        resolve_alert(db_session, 999999, utilizator_id=1)
