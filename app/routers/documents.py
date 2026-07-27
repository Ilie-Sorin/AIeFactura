from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.auth import User
from app.models.audit import AuditLog
from app.models.document import Attachment, Invoice
from app.models.ingestion import InvoiceSourceLink, SourceObject
from app.security import require_login
from app.services.audit import write_audit
from app.templating import templates

router = APIRouter()


@router.get("/documente/{invoice_id}")
def document_detail(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "Document inexistent")

    are_zip = (
        db.scalar(
            select(InvoiceSourceLink.id)
            .join(SourceObject, InvoiceSourceLink.source_object_id == SourceObject.id)
            .where(InvoiceSourceLink.invoice_id == invoice.id, SourceObject.tip == "zip")
        )
        is not None
    )
    istoric = db.scalars(
        select(AuditLog)
        .where(AuditLog.entitate == "invoice", AuditLog.entitate_id == invoice.id)
        .order_by(AuditLog.moment.desc())
    ).all()

    return templates.TemplateResponse(
        request,
        "document_detail.html",
        {"user": user, "invoice": invoice, "are_zip": are_zip, "istoric": istoric},
    )


def _download(
    db: Session, source_object_id: int, utilizator_id: int, nume_fisier: str, mime: str | None
) -> Response:
    obj = db.get(SourceObject, source_object_id)
    if obj is None:
        raise HTTPException(404, "Fișier inexistent")
    write_audit(
        db, "acces_binar", utilizator_id=utilizator_id, entitate="source_object", entitate_id=obj.id
    )
    db.commit()
    return Response(
        content=obj.continut,
        media_type=mime or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{nume_fisier}"'},
    )


@router.get("/documente/{invoice_id}/xml")
def document_xml(
    invoice_id: int, db: Session = Depends(get_db), user: User = Depends(require_login)
):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "Document inexistent")
    return _download(
        db, invoice.source_object_id, user.id, f"{invoice.numar_normalizat}.xml", "application/xml"
    )


@router.get("/documente/{invoice_id}/zip")
def document_zip(
    invoice_id: int, db: Session = Depends(get_db), user: User = Depends(require_login)
):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "Document inexistent")
    link = db.scalar(
        select(InvoiceSourceLink)
        .join(SourceObject, InvoiceSourceLink.source_object_id == SourceObject.id)
        .where(InvoiceSourceLink.invoice_id == invoice.id, SourceObject.tip == "zip")
    )
    if link is None:
        raise HTTPException(404, "Documentul nu provine dintr-o arhivă ZIP")
    return _download(
        db, link.source_object_id, user.id, f"{invoice.numar_normalizat}.zip", "application/zip"
    )


@router.get("/atasamente/{attachment_id}")
def attachment_download(
    attachment_id: int, db: Session = Depends(get_db), user: User = Depends(require_login)
):
    att = db.get(Attachment, attachment_id)
    if att is None or att.source_object_id is None:
        raise HTTPException(404, "Atașament inexistent")
    return _download(db, att.source_object_id, user.id, att.nume or "atasament", att.mime)
