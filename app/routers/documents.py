from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.auth import User
from app.models.audit import AuditLog
from app.models.consolidation import InvoiceGroupMember, InvoiceRelation
from app.models.document import Attachment, Invoice
from app.models.ingestion import InvoiceSourceLink, SourceObject
from app.security import require_login
from app.services.audit import write_audit
from app.services.export import build_document_export_zip
from app.services.pdf import DocumentRenderError, StylesheetMissingError, render_invoice_html
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

    grup_id = db.scalar(
        select(InvoiceGroupMember.group_id).where(InvoiceGroupMember.invoice_id == invoice.id)
    )

    relatii_brute = db.scalars(
        select(InvoiceRelation).where(
            or_(InvoiceRelation.invoice_from == invoice.id, InvoiceRelation.invoice_to == invoice.id)
        )
    ).all()
    alte_capete_ids = {
        (r.invoice_to if r.invoice_from == invoice.id else r.invoice_from) for r in relatii_brute
    }
    alte_facturi = {
        i.id: i for i in db.scalars(select(Invoice).where(Invoice.id.in_(alte_capete_ids))).all()
    }
    relatii = [
        {
            "id": r.id,
            "celalalt": alte_facturi.get(r.invoice_to if r.invoice_from == invoice.id else r.invoice_from),
            "directie": "spre" if r.invoice_from == invoice.id else "dinspre",
            "tip": r.tip,
            "sursa": r.sursa,
            "stare": r.stare,
            "scor": r.scor,
            "motiv": r.motiv,
        }
        for r in relatii_brute
    ]

    return templates.TemplateResponse(
        request,
        "document_detail.html",
        {
            "user": user,
            "invoice": invoice,
            "are_zip": are_zip,
            "istoric": istoric,
            "grup_id": grup_id,
            "relatii": relatii,
        },
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


@router.get("/documente/{invoice_id}/vizualizare", response_class=HTMLResponse)
def document_view(
    invoice_id: int, db: Session = Depends(get_db), user: User = Depends(require_login)
):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "Document inexistent")
    xml_source = db.get(SourceObject, invoice.source_object_id)
    if xml_source is None:
        raise HTTPException(404, "XML sursă inexistent")

    try:
        html = render_invoice_html(xml_source.continut)
    except StylesheetMissingError as exc:
        return HTMLResponse(
            "<p style='font-family:sans-serif; max-width:640px; margin:40px auto'>"
            f"{exc}</p>",
            status_code=200,
        )
    except DocumentRenderError as exc:
        raise HTTPException(500, str(exc)) from exc

    write_audit(db, "vizualizare", utilizator_id=user.id, entitate="invoice", entitate_id=invoice.id)
    db.commit()
    return HTMLResponse(content=html)


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


@router.get("/documente/{invoice_id}/export")
def document_export(
    invoice_id: int, db: Session = Depends(get_db), user: User = Depends(require_login)
):
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "Document inexistent")
    continut = build_document_export_zip(db, invoice)
    write_audit(
        db, "export", utilizator_id=user.id, entitate="invoice", entitate_id=invoice.id,
        detalii={"tip": "zip_document"},
    )
    db.commit()
    return Response(
        content=continut,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="export_{invoice.numar_normalizat}.zip"'
        },
    )


@router.get("/atasamente/{attachment_id}")
def attachment_download(
    attachment_id: int, db: Session = Depends(get_db), user: User = Depends(require_login)
):
    att = db.get(Attachment, attachment_id)
    if att is None or att.source_object_id is None:
        raise HTTPException(404, "Atașament inexistent")
    return _download(db, att.source_object_id, user.id, att.nume or "atasament", att.mime)
