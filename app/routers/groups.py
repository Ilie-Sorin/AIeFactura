from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models.auth import User
from app.models.consolidation import InvoiceGroup, InvoiceGroupMember
from app.security import require_login
from app.templating import templates

router = APIRouter()


@router.get("/grupuri")
def groups_list(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_login)
):
    grupuri = db.scalars(
        select(InvoiceGroup)
        .options(selectinload(InvoiceGroup.members))
        .order_by(InvoiceGroup.calculat_la.desc())
    ).all()
    return templates.TemplateResponse(request, "groups.html", {"user": user, "grupuri": grupuri})


@router.get("/grupuri/{group_id}")
def group_detail(
    group_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    grup = db.scalar(
        select(InvoiceGroup)
        .options(selectinload(InvoiceGroup.members).selectinload(InvoiceGroupMember.invoice))
        .where(InvoiceGroup.id == group_id)
    )
    if grup is None:
        raise HTTPException(404, "Grup inexistent")
    membri = sorted(grup.members, key=lambda m: m.invoice.data_emitere)
    return templates.TemplateResponse(
        request, "group_detail.html", {"user": user, "grup": grup, "membri": membri}
    )
