"""Teste de capăt-la-capăt pentru consolidare (cap. 6): grup cu poziție netă
ca unitate de lucru, relații explicite (inclusiv întârziate) și deduse care
nu ating gruparea decât după confirmare umană."""

from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.models.consolidation import InvoiceGroup, InvoiceGroupMember, InvoiceRelation
from app.models.document import Invoice
from app.services.consolidation import decide_relation, propose_deduced_relations
from app.services.ingest import IngestFile, finish_batch, ingest_file, start_batch

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _ingest(session, batch, nume: str):
    return ingest_file(session, batch, IngestFile(_read(nume), nume))


def _group_of(session, invoice_id: int) -> InvoiceGroup:
    member = session.scalar(select(InvoiceGroupMember).where(InvoiceGroupMember.invoice_id == invoice_id))
    assert member is not None, f"factura {invoice_id} nu are grup"
    return session.get(InvoiceGroup, member.group_id)


def test_storno_pair_produces_group_with_correct_net_position(db_session_commit):
    session = db_session_commit
    batch = start_batch(session, tip="scan_local", sursa="test")
    r_normala = _ingest(session, batch, "factura_normala.xml")
    r_storno = _ingest(session, batch, "factura_storno.xml")
    finish_batch(session, batch)
    session.commit()

    relatie = session.scalar(
        select(InvoiceRelation).where(InvoiceRelation.invoice_from == r_storno.invoice_id)
    )
    assert relatie is not None
    assert relatie.tip == "storno"
    assert relatie.sursa == "xml"
    assert relatie.stare == "confirmata"
    assert relatie.invoice_to == r_normala.invoice_id

    grup = _group_of(session, r_normala.invoice_id)
    assert _group_of(session, r_storno.invoice_id).id == grup.id
    assert len(grup.members) == 2
    assert grup.pozitie_neta == Decimal("0.00")


def test_reversed_import_order_resolves_pending_reference(db_session_commit):
    """Storno-ul soseste INAINTEA facturii pe care o referentiaza -- relatia
    nu se poate crea imediat, dar se rezolva quand originalul e importat."""
    session = db_session_commit
    batch = start_batch(session, tip="scan_local", sursa="test")
    r_storno = _ingest(session, batch, "factura_storno.xml")

    # Inca nicio relatie -- tinta nu exista inca.
    assert session.scalar(select(InvoiceRelation)) is None
    assert len(_group_of(session, r_storno.invoice_id).members) == 1

    r_normala = _ingest(session, batch, "factura_normala.xml")
    finish_batch(session, batch)
    session.commit()

    relatie = session.scalar(select(InvoiceRelation))
    assert relatie is not None
    assert relatie.invoice_from == r_storno.invoice_id
    assert relatie.invoice_to == r_normala.invoice_id

    grup = _group_of(session, r_normala.invoice_id)
    assert _group_of(session, r_storno.invoice_id).id == grup.id
    assert len(grup.members) == 2
    assert grup.pozitie_neta == Decimal("0.00")


def test_three_document_chain_initial_storno_corrected(db_session_commit):
    """Lantul din exemplul cap. 6: factura initiala + storno + factura
    corectata, toate legate prin BillingReference catre factura initiala."""
    session = db_session_commit
    batch = start_batch(session, tip="scan_local", sursa="test")
    r_normala = _ingest(session, batch, "factura_normala.xml")
    r_storno = _ingest(session, batch, "factura_storno.xml")
    r_corectata = _ingest(session, batch, "factura_corectata.xml")
    finish_batch(session, batch)
    session.commit()

    grup = _group_of(session, r_normala.invoice_id)
    membri_ids = {m.invoice_id for m in grup.members}
    assert membri_ids == {r_normala.invoice_id, r_storno.invoice_id, r_corectata.invoice_id}

    # 18599.70 (initiala, +) - 18599.70 (storno, -) + 11900.00 (corectata, +)
    assert grup.pozitie_neta == Decimal("11900.00")


def test_deduced_relation_does_not_affect_group_until_confirmed(db_session_commit):
    session = db_session_commit
    batch = start_batch(session, tip="scan_local", sursa="test")
    r_a = _ingest(session, batch, "factura_a.xml")
    r_b = _ingest(session, batch, "factura_b_credit_nedeclarat.xml")
    finish_batch(session, batch)
    session.commit()

    relatie = session.scalar(select(InvoiceRelation))
    assert relatie is not None
    assert relatie.sursa == "regula"
    assert relatie.stare == "propusa"
    assert relatie.tip == "storno_dedus"

    # Pana la confirmare, fiecare factura ramane in propriul grup individual.
    grup_a = _group_of(session, r_a.invoice_id)
    grup_b = _group_of(session, r_b.invoice_id)
    assert grup_a.id != grup_b.id
    assert grup_a.pozitie_neta == Decimal("595.00")
    assert grup_b.pozitie_neta == Decimal("-595.00")

    invoice = session.get(Invoice, r_a.invoice_id)
    admin_id = None  # decizie de sistem in acest test, nu conteaza cine

    decide_relation(session, relatie.id, "confirmata", utilizator_id=admin_id, motiv="verificat manual")
    session.commit()

    grup_unit = _group_of(session, r_a.invoice_id)
    assert _group_of(session, r_b.invoice_id).id == grup_unit.id
    assert grup_unit.pozitie_neta == Decimal("0.00")


def test_manual_rejection_survives_rule_rerun(db_session_commit):
    session = db_session_commit
    batch = start_batch(session, tip="scan_local", sursa="test")
    r_a = _ingest(session, batch, "factura_a.xml")
    r_b = _ingest(session, batch, "factura_b_credit_nedeclarat.xml")
    finish_batch(session, batch)
    session.commit()

    relatie = session.scalar(select(InvoiceRelation))
    decide_relation(session, relatie.id, "respinsa", utilizator_id=None, motiv="documente fara legatura")
    session.commit()

    # O rulare ulterioara a regulilor nu trebuie sa creeze o relatie noua sau
    # sa suprascrie decizia -- ramane o singura relatie, respinsa.
    invoice_a = session.get(Invoice, r_a.invoice_id)
    propuneri_noi = propose_deduced_relations(session, invoice_a)
    session.commit()

    assert propuneri_noi == []
    toate_relatiile = session.scalars(select(InvoiceRelation)).all()
    assert len(toate_relatiile) == 1
    assert toate_relatiile[0].stare == "respinsa"

    # grupurile raman separate
    assert _group_of(session, r_a.invoice_id).id != _group_of(session, r_b.invoice_id).id
