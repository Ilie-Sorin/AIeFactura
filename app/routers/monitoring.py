from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.auth import User
from app.security import require_admin
from app.services.alerting import run_checks_and_notify
from app.services.monitoring import resolve_alert

router = APIRouter()


@router.post("/monitorizare/ruleaza")
def trigger_checks(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    run_checks_and_notify(db)
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@router.post("/alerte/{alert_id}/rezolva")
def resolve(
    alert_id: int,
    motiv: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    try:
        resolve_alert(db, alert_id, utilizator_id=user.id, motiv=motiv or None)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    return RedirectResponse(url="/", status_code=303)
