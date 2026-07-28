from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.auth import User
from app.models.document import Invoice
from app.models.ingestion import ImportBatch
from app.models.monitoring import IntegrityAlert
from app.security import require_login
from app.templating import templates

router = APIRouter()


@router.get("/")
def dashboard(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_login)
):
    documente_noi = db.scalars(
        select(Invoice).order_by(Invoice.creat_la.desc()).limit(10)
    ).all()
    documente_eroare = db.scalars(
        select(Invoice).where(Invoice.stare == "eroare").order_by(Invoice.creat_la.desc()).limit(10)
    ).all()
    loturi_recente = db.scalars(
        select(ImportBatch).order_by(ImportBatch.pornit_la.desc()).limit(10)
    ).all()
    alerte_deschise = db.scalars(
        select(IntegrityAlert)
        .where(IntegrityAlert.rezolvat_la.is_(None))
        .order_by(IntegrityAlert.nivel.desc(), IntegrityAlert.generat_la.desc())
        .limit(20)
    ).all()

    total_documente = db.scalar(select(func.count()).select_from(Invoice))
    total_erori = db.scalar(
        select(func.count()).select_from(Invoice).where(Invoice.stare == "eroare")
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "documente_noi": documente_noi,
            "documente_eroare": documente_eroare,
            "loturi_recente": loturi_recente,
            "alerte_deschise": alerte_deschise,
            "total_documente": total_documente,
            "total_erori": total_erori,
        },
    )
