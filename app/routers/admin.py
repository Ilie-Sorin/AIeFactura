from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.auth import User
from app.security import create_user, require_admin
from app.services.audit import write_audit
from app.templating import templates

router = APIRouter()


@router.get("/admin")
def admin_home(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    utilizatori = db.scalars(select(User).order_by(User.username)).all()
    return templates.TemplateResponse(request, "admin.html", {"user": user, "utilizatori": utilizatori})


@router.post("/admin/utilizatori")
def create_user_route(
    username: str = Form(...),
    password: str = Form(...),
    rol: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    new_user = create_user(db, username, password, rol=rol)
    write_audit(
        db,
        "utilizator_creat",
        utilizator_id=user.id,
        entitate="app_user",
        entitate_id=new_user.id,
        detalii={"username": username, "rol": rol},
    )
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)
