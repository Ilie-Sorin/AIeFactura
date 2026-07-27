from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.security import authenticate, get_current_user
from app.services.audit import write_audit
from app.templating import templates

router = APIRouter()


@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"user": None, "eroare": None})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = authenticate(db, username, password)
    if user is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"user": None, "eroare": "Utilizator sau parolă incorectă."},
            status_code=401,
        )
    request.session["user_id"] = user.id
    user.ultima_autentificare = datetime.now(timezone.utc)
    write_audit(db, "autentificare", utilizator_id=user.id, entitate="app_user", entitate_id=user.id)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user is not None:
        write_audit(db, "deautentificare", utilizator_id=user.id, entitate="app_user", entitate_id=user.id)
        db.commit()
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
