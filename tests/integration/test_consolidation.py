"""Teste de capăt-la-capăt pentru consolidare (cap. 6): grup cu poziție netă
ca unitate de lucru, relații explicite (inclusiv întârziate) și deduse care
nu ating gruparea decât după confirmare umană."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.models.consolidation import InvoiceGroup, InvoiceGroupMember, InvoiceRelation
from app.models.document import Invoice
from app.models.ingestion import SourceObject
from app.services.consolidation import (
    decide_relation,
    propose_deduced_relations,
    resolve_pending_references_for_suppliers,
)
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
    nu se poate crea imediat (consolidate_invoice nu mai reincearca automat
    referintele intarziate ale ALTOR facturi la fiecare ingestie -- ar fi
    O(n^2) pe lot, cf. resolve_pending_references_for_suppliers), dar se
    rezolva la finalul lotului (scanner/upload apeleaza explicit functia,
    o singura data per furnizor)."""
    session = db_session_commit
    batch = start_batch(session, tip="scan_local", sursa="test")
    r_storno = _ingest(session, batch, "factura_storno.xml")

    # Inca nicio relatie -- tinta nu exista inca.
    assert session.scalar(select(InvoiceRelation)) is None
    assert len(_group_of(session, r_storno.invoice_id).members) == 1

    r_normala = _ingest(session, batch, "factura_normala.xml")
    resolve_pending_references_for_suppliers(session, {"185472901"})
    finish_batch(session, batch)
    session.commit()

    toate_relatiile = session.scalars(select(InvoiceRelation)).all()
    # Facturile astea au si valoare egala + tip complementar, deci
    # propose_deduced_relations() ar propune independent un "storno_dedus" --
    # trebuie sa ramana O SINGURA relatie, cea explicita din XML (regresie:
    # o propunere dedusa creata prima, in timpul ingestiei normale, bloca
    # anterior confirmarea automata a aceleiasi perechi din XML).
    assert len(toate_relatiile) == 1
    relatie = toate_relatiile[0]
    assert relatie.invoice_from == r_storno.invoice_id
    assert relatie.invoice_to == r_normala.invoice_id
    assert relatie.tip == "storno"
    assert relatie.sursa == "xml"
    assert relatie.stare == "confirmata"

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


def _make_minimal_invoice(session, batch, *, numar, nr_comanda=None, cif="185472901"):
    source = SourceObject(
        batch_id=batch.id, tip="xml_factura", continut=b"<x/>", sha256=numar.zfill(64), marime=4
    )
    session.add(source)
    session.flush()
    invoice = Invoice(
        batch_id=batch.id,
        source_object_id=source.id,
        directie="intrare",
        cif_emitent=cif,
        numar_brut=numar,
        numar_normalizat=numar,
        nr_comanda=nr_comanda,
        tip_document="380",
        data_emitere=date(2026, 1, 1),
        total_fara_tva=Decimal("100.00"),
        total_tva=Decimal("0.00"),
        total_document=Decimal("100.00"),
        stare="indexat",
    )
    session.add(invoice)
    session.flush()
    return invoice


def test_deduced_relation_skips_overly_common_order_number(db_session_commit):
    """Regresie: un nr_comanda placeholder repetat la zeci de facturi ale
    aceluiași furnizor (date reale: "1" la 197 facturi) nu trebuie să
    genereze O(n²) propuneri de relație -- e zgomot, nu un corelator real,
    și a dus la un import de ore pe ~5000 documente reale."""
    session = db_session_commit
    batch = start_batch(session, tip="scan_local", sursa="test-comanda-comuna")

    facturi = [
        _make_minimal_invoice(session, batch, numar=f"{i:04d}", nr_comanda="1") for i in range(25)
    ]
    session.commit()

    propuneri = propose_deduced_relations(session, facturi[-1])
    session.commit()

    assert propuneri == []
    assert session.scalar(select(InvoiceRelation)) is None


def test_deduced_relation_still_works_below_the_cap(db_session_commit):
    session = db_session_commit
    batch = start_batch(session, tip="scan_local", sursa="test-comanda-normala")

    facturi = [
        _make_minimal_invoice(session, batch, numar=f"{i:04d}", nr_comanda="CMD-42") for i in range(3)
    ]
    session.commit()

    propuneri = propose_deduced_relations(session, facturi[-1])
    session.commit()

    assert len(propuneri) == 2
    assert all(p.tip == "comanda_comuna" for p in propuneri)


def test_explicit_xml_relation_replaces_earlier_deduced_proposal(db_session_commit):
    """O propunere DEDUSĂ (creată prima, doar din întâmplarea ordinii de
    procesare) nu trebuie să blocheze o relație EXPLICITĂ din XML pentru
    aceeași pereche -- faptul cert câștigă, nu presupunerea, indiferent de
    ordine (regresie reală: aceleași facturi cu valoare egală ȘI referință
    explicită, deducerea câștiga cursa dacă rula prima)."""
    from app.services.consolidation import resolve_explicit_relations

    session = db_session_commit
    batch = start_batch(session, tip="scan_local", sursa="test-prioritate")

    original = _make_minimal_invoice(session, batch, numar="1000")
    storno = _make_minimal_invoice(session, batch, numar="2000")
    storno.tip_document = "381"
    storno.referinte_xml = [{"tip": "storno", "valoare": "1000", "xpath": None}]
    session.flush()

    # ordinea "proasta": intai se propune deducerea (storno_dedus)...
    propuneri = propose_deduced_relations(session, storno)
    assert len(propuneri) == 1
    assert propuneri[0].sursa == "regula"
    assert propuneri[0].stare == "propusa"

    # ...abia apoi se rezolva referinta explicita din XML pentru aceeasi pereche
    explicite = resolve_explicit_relations(session, storno)
    assert len(explicite) == 1
    assert explicite[0].sursa == "xml"

    toate = session.scalars(select(InvoiceRelation)).all()
    assert len(toate) == 1  # propunerea slaba a fost inlocuita, nu dublata
    assert toate[0].sursa == "xml"
    assert toate[0].stare == "confirmata"


def test_human_decision_still_blocks_explicit_relation(db_session_commit):
    """Spre deosebire de o propunere neatinsa, o relatie deja decisa de un OM
    (utilizator_id populat) tot nu trebuie inlocuita de nimic automat."""
    from app.services.consolidation import resolve_explicit_relations

    session = db_session_commit
    batch = start_batch(session, tip="scan_local", sursa="test-decizie-umana")

    original = _make_minimal_invoice(session, batch, numar="1000")
    storno = _make_minimal_invoice(session, batch, numar="2000")
    storno.tip_document = "381"
    storno.referinte_xml = [{"tip": "storno", "valoare": "1000", "xpath": None}]
    session.flush()

    from app.security import create_user

    utilizator = create_user(session, "revizor-relatii", "parola123")
    session.flush()

    session.add(
        InvoiceRelation(
            invoice_from=storno.id,
            invoice_to=original.id,
            tip="storno_dedus",
            sursa="regula",
            stare="respinsa",
            motiv="verificat manual, nu sunt legate",
            utilizator_id=utilizator.id,
        )
    )
    session.flush()

    explicite = resolve_explicit_relations(session, storno)
    assert explicite == []

    toate = session.scalars(select(InvoiceRelation)).all()
    assert len(toate) == 1
    assert toate[0].stare == "respinsa"
