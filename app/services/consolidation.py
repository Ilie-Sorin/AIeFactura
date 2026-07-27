"""Consolidarea documentelor legate (cap. 6).

Relații explicite (din XML, `sursa='xml'`, `stare='confirmata'`) — momentan
doar referința de stornare/corectare (`cac:BillingReference`), singura
legătură UBL nativă factură-către-factură; comandă/contract/aviz sunt chei de
potrivire pentru relații DEDUSE, nu referințe directe.

Relații deduse (`sursa='regula'`, `stare='propusa'`) — necesită confirmare
umană înainte să afecteze gruparea. O rulare ulterioară nu creează niciodată
o relație duplicat peste una deja existentă (confirmată, respinsă sau
propusă) — asta protejează implicit orice decizie manuală de suprascriere.

Grupul (`invoice_group`) e componenta conexă peste relațiile CONFIRMATE,
calculată cu o interogare recursivă (`WITH RECURSIVE`, cap. 6/13), cu poziție
netă = suma semnată a documentelor membre (semn -1 pentru facturi storno/credit,
+1 altfel).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import case, delete, literal, or_, select
from sqlalchemy.orm import Session

from app.models.consolidation import InvoiceGroup, InvoiceGroupMember, InvoiceRelation
from app.models.document import Invoice
from app.services.normalize_number import normalize_invoice_number, resolve_numbering_config

CREDIT_NOTE_TYPE_CODE = "381"
FEREASTRA_STORNO_ZILE = 90


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _relation_exists(session: Session, invoice_a: int, invoice_b: int) -> bool:
    return (
        session.scalar(
            select(InvoiceRelation.id).where(
                or_(
                    (InvoiceRelation.invoice_from == invoice_a)
                    & (InvoiceRelation.invoice_to == invoice_b),
                    (InvoiceRelation.invoice_from == invoice_b)
                    & (InvoiceRelation.invoice_to == invoice_a),
                )
            )
        )
        is not None
    )


def _create_relation(
    session: Session,
    *,
    invoice_from: int,
    invoice_to: int,
    tip: str,
    sursa: str,
    stare: str,
    scor: Decimal | None = None,
    motiv: str | None = None,
    utilizator_id: int | None = None,
) -> InvoiceRelation | None:
    """Nu creează nimic dacă există deja orice relație (indiferent de stare)
    între cele două facturi — o rulare ulterioară nu suprascrie o decizie."""
    if invoice_from == invoice_to or _relation_exists(session, invoice_from, invoice_to):
        return None
    relation = InvoiceRelation(
        invoice_from=invoice_from,
        invoice_to=invoice_to,
        tip=tip,
        sursa=sursa,
        stare=stare,
        scor=scor,
        motiv=motiv,
        utilizator_id=utilizator_id,
    )
    session.add(relation)
    session.flush()
    return relation


def resolve_explicit_relations(session: Session, invoice: Invoice) -> list[InvoiceRelation]:
    if not invoice.referinte_xml:
        return []

    config = resolve_numbering_config(session, invoice.cif_emitent)
    create: list[InvoiceRelation] = []
    for ref in invoice.referinte_xml:
        if ref.get("tip") != "storno":
            continue
        numar_normalizat_tinta = normalize_invoice_number(ref["valoare"], config).normalizata
        tinta = session.scalar(
            select(Invoice).where(
                Invoice.cif_emitent == invoice.cif_emitent,
                Invoice.numar_normalizat == numar_normalizat_tinta,
                Invoice.id != invoice.id,
            )
        )
        if tinta is None:
            continue
        relation = _create_relation(
            session,
            invoice_from=invoice.id,
            invoice_to=tinta.id,
            tip="storno",
            sursa="xml",
            stare="confirmata",
            motiv=f"Referință din XML (BillingReference) -> {ref['valoare']}",
        )
        if relation is not None:
            create.append(relation)
    return create


def resolve_pending_explicit_references(session: Session, cif_emitent: str) -> list[InvoiceRelation]:
    """Reîncearcă referințele storno nerezolvate ale unui furnizor — cazul în
    care documentul-țintă sosește DUPĂ cel care îl referențiază. Idempotent:
    `_create_relation` sare peste perechile deja legate."""
    candidati = session.scalars(
        select(Invoice).where(Invoice.cif_emitent == cif_emitent, Invoice.referinte_xml.isnot(None))
    ).all()
    create: list[InvoiceRelation] = []
    for inv in candidati:
        create.extend(resolve_explicit_relations(session, inv))
    return create


def propose_deduced_relations(session: Session, invoice: Invoice) -> list[InvoiceRelation]:
    """Reguli simple (cap. 6) când referințele explicite lipsesc: aceeași
    valoare + furnizor + tip de document complementar (factură/credit) în
    fereastra de zile -> candidat storno dedus; aceeași comandă/contract ->
    candidat grup comun. Rămân 'propusa' până la decizie umană."""
    propuneri: list[InvoiceRelation] = []
    is_storno = invoice.tip_document == CREDIT_NOTE_TYPE_CODE
    fereastra = dt.timedelta(days=FEREASTRA_STORNO_ZILE)

    candidati_valoare = session.scalars(
        select(Invoice).where(
            Invoice.cif_emitent == invoice.cif_emitent,
            Invoice.id != invoice.id,
            Invoice.total_document == invoice.total_document,
            Invoice.data_emitere >= invoice.data_emitere - fereastra,
            Invoice.data_emitere <= invoice.data_emitere + fereastra,
        )
    ).all()
    for cand in candidati_valoare:
        if is_storno == (cand.tip_document == CREDIT_NOTE_TYPE_CODE):
            continue  # avem nevoie de o pereche factură <-> document de credit
        zile = abs((invoice.data_emitere - cand.data_emitere).days)
        scor = Decimal("90") if zile <= 7 else Decimal("70") if zile <= 30 else Decimal("50")
        storno_id, original_id = (invoice.id, cand.id) if is_storno else (cand.id, invoice.id)
        relation = _create_relation(
            session,
            invoice_from=storno_id,
            invoice_to=original_id,
            tip="storno_dedus",
            sursa="regula",
            stare="propusa",
            scor=scor,
            motiv=f"Aceeași valoare ({invoice.total_document} {invoice.moneda}), același furnizor, la {zile} zile distanță.",
        )
        if relation is not None:
            propuneri.append(relation)

    for camp, tip in (("nr_comanda", "comanda_comuna"), ("nr_contract", "contract_comun")):
        valoare = getattr(invoice, camp)
        if not valoare:
            continue
        candidati = session.scalars(
            select(Invoice).where(
                Invoice.cif_emitent == invoice.cif_emitent,
                Invoice.id != invoice.id,
                getattr(Invoice, camp) == valoare,
            )
        ).all()
        for cand in candidati:
            relation = _create_relation(
                session,
                invoice_from=invoice.id,
                invoice_to=cand.id,
                tip=tip,
                sursa="regula",
                stare="propusa",
                scor=Decimal("60"),
                motiv=f"Aceeași valoare {camp}: {valoare}",
            )
            if relation is not None:
                propuneri.append(relation)

    return propuneri


def find_connected_invoice_ids(session: Session, start_invoice_id: int) -> set[int]:
    """Componenta conexă a facturii, peste relațiile CONFIRMATE — interogare
    recursivă (cap. 6: „Lanțul complet se interoghează cu WITH RECURSIVE",
    cap. 13: obiectiv tehnic de testare)."""
    base = select(literal(start_invoice_id).label("invoice_id"))
    cte = base.cte(name="grup_conex", recursive=True)

    # Postgres cere UN SINGUR termen recursiv (o singura interogare care se
    # autoreferentiaza), nu doua UNION-uri inlantuite -- de-aia "ambele
    # directii" ale muchiei neorientate sunt combinate aici cu CASE/OR
    # intr-un singur SELECT, nu in doua selecturi unite separat.
    capat_celalalt = case(
        (InvoiceRelation.invoice_from == cte.c.invoice_id, InvoiceRelation.invoice_to),
        else_=InvoiceRelation.invoice_from,
    ).label("invoice_id")
    pas_recursiv = (
        select(capat_celalalt)
        .select_from(InvoiceRelation)
        .join(
            cte,
            or_(
                InvoiceRelation.invoice_from == cte.c.invoice_id,
                InvoiceRelation.invoice_to == cte.c.invoice_id,
            ),
        )
        .where(InvoiceRelation.stare == "confirmata")
    )
    cte = cte.union(pas_recursiv)

    return set(session.execute(select(cte.c.invoice_id)).scalars().all())


def recompute_group(session: Session, invoice_id: int) -> InvoiceGroup:
    """Recalculează grupul componentei conexe a `invoice_id`. Gestionează
    corect atât unirea a două grupuri existente (o relație nouă le conectează)
    cât și despărțirea unuia (o relație respinsă/anulată le desface) — vezi
    testele dedicate pentru cele două scenarii."""
    membri_ids = find_connected_invoice_ids(session, invoice_id)
    invoices = session.scalars(select(Invoice).where(Invoice.id.in_(membri_ids))).all()

    grup_existent_id = session.scalar(
        select(InvoiceGroupMember.group_id).where(InvoiceGroupMember.invoice_id.in_(membri_ids)).limit(1)
    )
    if grup_existent_id is not None:
        group = session.get(InvoiceGroup, grup_existent_id)
    else:
        group = InvoiceGroup()
        session.add(group)
        session.flush()

    # Resincronizeaza apartenenta grupului ales EXACT cu multimea conexa curenta:
    # scoate membri care nu mai fac parte (despartire), scoate membrii curenti
    # din orice alt grup vechi (unire), apoi reinsereaza-i curat cu semnul recalculat.
    session.execute(
        delete(InvoiceGroupMember).where(
            InvoiceGroupMember.group_id == group.id, InvoiceGroupMember.invoice_id.notin_(membri_ids)
        )
    )
    session.execute(
        delete(InvoiceGroupMember).where(
            InvoiceGroupMember.invoice_id.in_(membri_ids), InvoiceGroupMember.group_id != group.id
        )
    )
    session.execute(
        delete(InvoiceGroupMember).where(
            InvoiceGroupMember.group_id == group.id, InvoiceGroupMember.invoice_id.in_(membri_ids)
        )
    )

    pozitie_neta = Decimal("0")
    for inv in invoices:
        semn = -1 if inv.tip_document == CREDIT_NOTE_TYPE_CODE else 1
        session.add(InvoiceGroupMember(group_id=group.id, invoice_id=inv.id, semn=semn))
        pozitie_neta += semn * inv.total_document

    group.tip = "consolidat" if len(invoices) > 1 else "individual"
    group.pozitie_neta = pozitie_neta
    group.calculat_la = _now()
    session.flush()

    orfane = session.scalars(
        select(InvoiceGroup.id).where(
            ~InvoiceGroup.id.in_(select(InvoiceGroupMember.group_id).distinct())
        )
    ).all()
    if orfane:
        session.execute(delete(InvoiceGroup).where(InvoiceGroup.id.in_(orfane)))
        session.flush()

    return group


def consolidate_invoice(session: Session, invoice: Invoice) -> InvoiceGroup:
    """Orchestrare completă pentru o factură nou-importată: rezolvă relațiile
    explicite (proprii + ale altor facturi ale aceluiași furnizor, întârziate),
    propune relații deduse (fără efect asupra grupării) și recalculează grupul
    tuturor facturilor efectiv afectate."""
    afectate = {invoice.id}

    for relatie in resolve_explicit_relations(session, invoice):
        afectate.update((relatie.invoice_from, relatie.invoice_to))

    for relatie in resolve_pending_explicit_references(session, invoice.cif_emitent):
        afectate.update((relatie.invoice_from, relatie.invoice_to))

    propose_deduced_relations(session, invoice)

    grup_curent = None
    for inv_id in afectate:
        grup = recompute_group(session, inv_id)
        if inv_id == invoice.id:
            grup_curent = grup
    return grup_curent


def decide_relation(
    session: Session, relation_id: int, decizie: str, utilizator_id: int, motiv: str | None = None
) -> InvoiceRelation:
    if decizie not in ("confirmata", "respinsa"):
        raise ValueError(f"decizie invalidă: {decizie!r}")
    relation = session.get(InvoiceRelation, relation_id)
    if relation is None:
        raise ValueError(f"relația {relation_id} nu există")
    relation.stare = decizie
    relation.motiv = motiv
    relation.utilizator_id = utilizator_id
    session.flush()
    recompute_group(session, relation.invoice_from)
    recompute_group(session, relation.invoice_to)
    return relation


def create_manual_relation(
    session: Session,
    invoice_from_id: int,
    invoice_to_id: int,
    tip: str,
    utilizator_id: int,
    motiv: str | None = None,
) -> InvoiceRelation | None:
    relation = _create_relation(
        session,
        invoice_from=invoice_from_id,
        invoice_to=invoice_to_id,
        tip=tip,
        sursa="manual",
        stare="confirmata",
        motiv=motiv,
        utilizator_id=utilizator_id,
    )
    if relation is not None:
        recompute_group(session, invoice_from_id)
        recompute_group(session, invoice_to_id)
    return relation
