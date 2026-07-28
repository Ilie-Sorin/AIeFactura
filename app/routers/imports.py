from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models.audit import AuditLog
from app.models.auth import User
from app.models.document import Invoice
from app.models.ingestion import ImportBatch
from app.security import require_admin, require_login
from app.services.consolidation import resolve_pending_references_for_suppliers
from app.services.ingest import IngestFile, cancel_batch, finish_batch, safe_ingest_file, start_batch
from app.services.scanner import scan_directory
from app.templating import templates

router = APIRouter()


@router.get("/importuri")
def imports_list(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_login)
):
    loturi = db.scalars(select(ImportBatch).order_by(ImportBatch.pornit_la.desc()).limit(100)).all()
    return templates.TemplateResponse(
        request,
        "imports.html",
        {"user": user, "loturi": loturi, "scan_root": get_settings().scan_root},
    )


@router.get("/importuri/{batch_id}")
def batch_detail(
    batch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "Lot inexistent")
    documente = db.scalars(select(Invoice).where(Invoice.batch_id == batch.id)).all()
    jurnal = db.scalars(
        select(AuditLog)
        .where(AuditLog.entitate == "import_batch", AuditLog.entitate_id == batch.id)
        .order_by(AuditLog.moment)
    ).all()
    return templates.TemplateResponse(
        request,
        "batch_detail.html",
        {"user": user, "batch": batch, "documente": documente, "jurnal": jurnal},
    )


@router.post("/importuri/upload")
async def upload_files(
    fisiere: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    batch = start_batch(db, tip="scan_local", sursa="upload manual", utilizator_id=user.id)
    for f in fisiere:
        continut = await f.read()
        safe_ingest_file(
            db, batch, IngestFile(continut=continut, nume_original=f.filename or "fisier"),
            utilizator_id=user.id,
        )
    furnizori_atinsi = set(
        db.scalars(select(Invoice.cif_emitent).where(Invoice.batch_id == batch.id).distinct()).all()
    )
    if furnizori_atinsi:
        resolve_pending_references_for_suppliers(db, furnizori_atinsi)
    finish_batch(db, batch)
    db.commit()
    return RedirectResponse(url=f"/importuri/{batch.id}", status_code=303)


@router.post("/importuri/scaneaza")
def trigger_scan(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    root = Path(get_settings().scan_root)
    batch, _ = scan_directory(
        db, root, tip="scan_local", sursa=f"scanare manuală: {root}", utilizator_id=user.id
    )
    db.commit()
    return RedirectResponse(url=f"/importuri/{batch.id}", status_code=303)


@router.post("/importuri/{batch_id}/anuleaza")
def cancel(
    batch_id: int,
    motiv: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "Lot inexistent")
    cancel_batch(db, batch, motiv=motiv, utilizator_id=user.id)
    db.commit()
    return RedirectResponse(url=f"/importuri/{batch.id}", status_code=303)
