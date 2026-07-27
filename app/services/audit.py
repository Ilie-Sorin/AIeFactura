"""Scrierea în jurnalul de operații (cap. 12): cine, când, ce a
importat/anulat/exportat/reconciliat, cine a accesat conținut binar."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.audit import AuditLog


def write_audit(
    session: Session,
    actiune: str,
    *,
    utilizator_id: int | None = None,
    entitate: str | None = None,
    entitate_id: int | None = None,
    detalii: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        utilizator_id=utilizator_id,
        actiune=actiune,
        entitate=entitate,
        entitate_id=entitate_id,
        detalii=detalii,
    )
    session.add(entry)
    return entry
