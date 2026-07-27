from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.auth import User
from app.models.document import Invoice, InvoiceLine, InvoiceParty
from app.security import require_login
from app.templating import templates

router = APIRouter()


@router.get("/registru")
def registry(
    request: Request,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    query = select(Invoice).order_by(Invoice.creat_la.desc())
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        linii_potrivite = select(InvoiceLine.invoice_id).where(InvoiceLine.descriere.ilike(like))
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
    documente = db.scalars(query.limit(200)).all()
    return templates.TemplateResponse(
        request, "registry.html", {"user": user, "documente": documente, "q": q}
    )
