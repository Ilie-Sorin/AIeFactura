from datetime import date
from decimal import Decimal

from app.models.document import Invoice, InvoiceParty
from app.models.ingestion import ImportBatch, SourceObject
from app.services.display import attach_party_names


def _make_invoice_with_parts(session, *, furnizor_denumire=None, client_denumire=None):
    batch = ImportBatch(tip="scan_local", stare="in_curs")
    session.add(batch)
    session.flush()
    source = SourceObject(batch_id=batch.id, tip="xml_factura", continut=b"<x/>", sha256="a" * 64, marime=4)
    session.add(source)
    session.flush()
    invoice = Invoice(
        batch_id=batch.id,
        source_object_id=source.id,
        directie="intrare",
        cif_emitent="185472901",
        cif_beneficiar="143981007",
        numar_brut="1",
        numar_normalizat="1",
        data_emitere=date(2026, 1, 1),
        total_fara_tva=Decimal("100.00"),
        total_tva=Decimal("19.00"),
        total_document=Decimal("119.00"),
        stare="indexat",
    )
    session.add(invoice)
    session.flush()
    if furnizor_denumire:
        session.add(InvoiceParty(invoice_id=invoice.id, rol="furnizor", denumire=furnizor_denumire))
    if client_denumire:
        session.add(InvoiceParty(invoice_id=invoice.id, rol="client", denumire=client_denumire))
    session.flush()
    session.refresh(invoice)
    return invoice


def test_attach_party_names_sets_names_from_parts(db_session):
    invoice = _make_invoice_with_parts(
        db_session, furnizor_denumire="Furnizor Exemplu SRL", client_denumire="Client Exemplu SA"
    )
    attach_party_names([invoice])
    assert invoice.nume_furnizor == "Furnizor Exemplu SRL"
    assert invoice.nume_client == "Client Exemplu SA"


def test_attach_party_names_none_when_party_missing(db_session):
    invoice = _make_invoice_with_parts(db_session)
    attach_party_names([invoice])
    assert invoice.nume_furnizor is None
    assert invoice.nume_client is None
