from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.auth import User
from app.models.document import Invoice
from app.security import require_admin
from app.services.consolidation import create_manual_relation, decide_relation
from app.services.normalize_number import normalize_invoice_number, resolve_numbering_config

router = APIRouter()


@router.post("/relatii/{relation_id}/decide")
def decide(
    relation_id: int,
    decizie: str = Form(...),
    motiv: str = Form(""),
    intoarce_la: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    try:
        decide_relation(db, relation_id, decizie, utilizator_id=user.id, motiv=motiv or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return RedirectResponse(url=f"/documente/{intoarce_la}", status_code=303)


@router.post("/relatii/manuala")
def create_manual(
    invoice_from_id: int = Form(...),
    numar_tinta: str = Form(...),
    tip: str = Form(...),
    motiv: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    invoice_from = db.get(Invoice, invoice_from_id)
    if invoice_from is None:
        raise HTTPException(404, "Document inexistent")

    config = resolve_numbering_config(db, invoice_from.cif_emitent)
    numar_normalizat = normalize_invoice_number(numar_tinta, config).normalizata
    tinta = db.scalar(
        select(Invoice).where(
            Invoice.cif_emitent == invoice_from.cif_emitent,
            Invoice.numar_normalizat == numar_normalizat,
            Invoice.id != invoice_from.id,
        )
    )
    if tinta is None:
        raise HTTPException(
            404, f"Nu s-a găsit un document cu numărul '{numar_tinta}' de la același furnizor"
        )

    create_manual_relation(
        db, invoice_from.id, tinta.id, tip, utilizator_id=user.id, motiv=motiv or None
    )
    db.commit()
    return RedirectResponse(url=f"/documente/{invoice_from.id}", status_code=303)
