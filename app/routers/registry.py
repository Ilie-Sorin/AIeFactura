import datetime as dt
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models.auth import User
from app.models.document import Invoice, InvoiceLine, InvoiceParty
from app.security import require_login
from app.services.audit import write_audit
from app.services.display import attach_party_names
from app.services.export import build_bulk_export_zip, export_registry_to_excel
from app.templating import templates

router = APIRouter()

EXPORT_LIMIT = 500


def _parse_date(text: str | None) -> dt.date | None:
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _parse_decimal(text: str | None) -> Decimal | None:
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _build_query(
    q: str | None,
    data_de: str | None,
    data_pana: str | None,
    suma_de: str | None,
    suma_pana: str | None,
    cif: str | None,
    stare: str | None,
    directie: str | None,
) -> Select:
    query = select(Invoice).options(selectinload(Invoice.parts)).order_by(Invoice.creat_la.desc())
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        linii_potrivite = select(InvoiceLine.invoice_id).where(
            or_(InvoiceLine.denumire.ilike(like), InvoiceLine.descriere.ilike(like))
        )
        parti_potrivite = select(InvoiceParty.invoice_id).where(InvoiceParty.denumire.ilike(like))
        query = query.where(
            or_(
                Invoice.numar_brut.ilike(like),
                Invoice.numar_normalizat.ilike(like),
                Invoice.cif_emitent.ilike(like),
                Invoice.cif_beneficiar.ilike(like),
                Invoice.nr_contract.ilike(like),
                Invoice.nr_comanda.ilike(like),
                Invoice.id.in_(linii_potrivite),
                Invoice.id.in_(parti_potrivite),
            )
        )

    data_de_val = _parse_date(data_de)
    if data_de_val:
        query = query.where(Invoice.data_emitere >= data_de_val)
    data_pana_val = _parse_date(data_pana)
    if data_pana_val:
        query = query.where(Invoice.data_emitere <= data_pana_val)

    suma_de_val = _parse_decimal(suma_de)
    if suma_de_val is not None:
        query = query.where(Invoice.total_document >= suma_de_val)
    suma_pana_val = _parse_decimal(suma_pana)
    if suma_pana_val is not None:
        query = query.where(Invoice.total_document <= suma_pana_val)

    if cif:
        cif_like = f"%{cif.strip()}%"
        query = query.where(or_(Invoice.cif_emitent.ilike(cif_like), Invoice.cif_beneficiar.ilike(cif_like)))
    if stare:
        query = query.where(Invoice.stare == stare)
    if directie:
        query = query.where(Invoice.directie == directie)

    return query


@router.get("/registru")
def registry(
    request: Request,
    q: str | None = None,
    data_de: str | None = None,
    data_pana: str | None = None,
    suma_de: str | None = None,
    suma_pana: str | None = None,
    cif: str | None = None,
    stare: str | None = None,
    directie: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    query = _build_query(q, data_de, data_pana, suma_de, suma_pana, cif, stare, directie)
    documente = db.scalars(query.limit(200)).all()
    attach_party_names(documente)
    filtre = {
        "q": q or "",
        "data_de": data_de or "",
        "data_pana": data_pana or "",
        "suma_de": suma_de or "",
        "suma_pana": suma_pana or "",
        "cif": cif or "",
        "stare": stare or "",
        "directie": directie or "",
    }
    return templates.TemplateResponse(
        request, "registry.html", {"user": user, "documente": documente, **filtre}
    )


@router.get("/registru/export.xlsx")
def export_excel(
    q: str | None = None,
    data_de: str | None = None,
    data_pana: str | None = None,
    suma_de: str | None = None,
    suma_pana: str | None = None,
    cif: str | None = None,
    stare: str | None = None,
    directie: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    query = _build_query(q, data_de, data_pana, suma_de, suma_pana, cif, stare, directie)
    documente = db.scalars(query.limit(EXPORT_LIMIT)).all()
    continut = export_registry_to_excel(documente)
    write_audit(
        db, "export", utilizator_id=user.id, entitate="registru",
        detalii={"tip": "excel", "nr_documente": len(documente)},
    )
    db.commit()
    return Response(
        content=continut,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="registru.xlsx"'},
    )


@router.get("/registru/export.zip")
def export_zip(
    q: str | None = None,
    data_de: str | None = None,
    data_pana: str | None = None,
    suma_de: str | None = None,
    suma_pana: str | None = None,
    cif: str | None = None,
    stare: str | None = None,
    directie: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    query = _build_query(q, data_de, data_pana, suma_de, suma_pana, cif, stare, directie)
    documente = db.scalars(query.limit(EXPORT_LIMIT)).all()
    continut = build_bulk_export_zip(db, documente)
    write_audit(
        db, "export", utilizator_id=user.id, entitate="registru",
        detalii={"tip": "zip_arhiva", "nr_documente": len(documente)},
    )
    db.commit()
    return Response(
        content=continut,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="export_registru.zip"'},
    )
