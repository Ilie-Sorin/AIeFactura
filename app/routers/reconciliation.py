import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.auth import User
from app.models.consolidation import InvoiceGroup, InvoiceGroupMember
from app.models.document import Invoice
from app.models.reconciliation import (
    ExternalRecord,
    ImportProfile,
    ReconciliationResult,
    ReconciliationRule,
    ReconciliationRun,
)
from app.security import require_admin, require_login
from app.services.external_import import ExternalImportError, import_external_records
from app.services.ingest import finish_batch, start_batch
from app.services.reconciliation import ReconciliationRuleError, decide_result, run_reconciliation
from app.templating import templates

router = APIRouter()


@router.get("/reconciliere")
def overview(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_login)
):
    profiluri = db.scalars(select(ImportProfile).order_by(ImportProfile.denumire)).all()
    reguli = db.scalars(select(ReconciliationRule).order_by(ReconciliationRule.denumire)).all()
    rulari = db.scalars(
        select(ReconciliationRun).order_by(ReconciliationRun.rulat_la.desc()).limit(20)
    ).all()
    return templates.TemplateResponse(
        request,
        "reconciliation_overview.html",
        {"user": user, "profiluri": profiluri, "reguli": reguli, "rulari": rulari},
    )


@router.post("/reconciliere/profiluri")
def create_profile(
    denumire: str = Form(...),
    tip_sursa: str = Form(...),
    format: str = Form(...),
    mapare: str = Form(...),
    reguli_curatare: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    try:
        mapare_json = json.loads(mapare)
        curatare_json = json.loads(reguli_curatare) if reguli_curatare.strip() else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"JSON invalid: {exc}") from exc
    profile = ImportProfile(
        denumire=denumire,
        tip_sursa=tip_sursa,
        format=format,
        mapare=mapare_json,
        reguli_curatare=curatare_json,
        activ=True,
    )
    db.add(profile)
    db.commit()
    return RedirectResponse(url="/reconciliere", status_code=303)


@router.post("/reconciliere/profiluri/{profile_id}/import")
async def upload_external_file(
    profile_id: int,
    fisier: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    profile = db.get(ImportProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "Profil inexistent")
    continut = await fisier.read()
    batch = start_batch(db, tip="import_extern", sursa=f"profil:{profile.denumire}", utilizator_id=user.id)
    try:
        import_external_records(db, profile, batch, continut, utilizator_id=user.id)
    except ExternalImportError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    finish_batch(db, batch)
    db.commit()
    return RedirectResponse(url=f"/importuri/{batch.id}", status_code=303)


@router.post("/reconciliere/reguli")
def create_rule(
    denumire: str = Form(...),
    definitie: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    try:
        definitie_json = json.loads(definitie)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"JSON invalid: {exc}") from exc
    rule = ReconciliationRule(denumire=denumire, definitie=definitie_json, activa=True)
    db.add(rule)
    db.commit()
    return RedirectResponse(url="/reconciliere", status_code=303)


@router.post("/reconciliere/reguli/{rule_id}/ruleaza")
def trigger_run(rule_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    rule = db.get(ReconciliationRule, rule_id)
    if rule is None:
        raise HTTPException(404, "Regulă inexistentă")
    try:
        run = run_reconciliation(db, rule, utilizator_id=user.id)
    except ReconciliationRuleError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return RedirectResponse(url=f"/reconciliere/rezultate?run_id={run.id}", status_code=303)


@router.get("/reconciliere/rezultate")
def results_list(
    request: Request,
    run_id: int | None = None,
    stare: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    query = select(ReconciliationResult).order_by(ReconciliationResult.id.desc())
    if run_id:
        query = query.where(ReconciliationResult.run_id == run_id)
    if stare:
        query = query.where(ReconciliationResult.stare == stare)
    rezultate_orm = db.scalars(query.limit(300)).all()

    group_ids = {r.group_id for r in rezultate_orm if r.group_id}
    external_ids = {r.external_record_id for r in rezultate_orm if r.external_record_id}

    grupuri_info: dict[int, dict] = {}
    if group_ids:
        membri = db.scalars(
            select(InvoiceGroupMember).where(InvoiceGroupMember.group_id.in_(group_ids))
        ).all()
        facturi = {
            f.id: f
            for f in db.scalars(
                select(Invoice).where(Invoice.id.in_([m.invoice_id for m in membri]))
            ).all()
        }
        grupuri_orm = {
            g.id: g for g in db.scalars(select(InvoiceGroup).where(InvoiceGroup.id.in_(group_ids))).all()
        }
        for gid in group_ids:
            membri_grup = [facturi[m.invoice_id] for m in membri if m.group_id == gid and m.invoice_id in facturi]
            grupuri_info[gid] = {
                "grup": grupuri_orm.get(gid),
                "numere": ", ".join(sorted({f.numar_brut for f in membri_grup})),
                "cif": membri_grup[0].cif_emitent if membri_grup else None,
                "reprezentant_id": membri_grup[0].id if membri_grup else None,
            }

    externe_info: dict[int, ExternalRecord] = {}
    if external_ids:
        externe_info = {
            e.id: e
            for e in db.scalars(select(ExternalRecord).where(ExternalRecord.id.in_(external_ids))).all()
        }

    rezultate = [
        {
            "id": r.id,
            "run_id": r.run_id,
            "scor": r.scor,
            "stare": r.stare,
            "decizie": r.decizie,
            "motiv": r.motiv,
            "diferente": r.diferente,
            "grup": grupuri_info.get(r.group_id) if r.group_id else None,
            "extern": externe_info.get(r.external_record_id) if r.external_record_id else None,
        }
        for r in rezultate_orm
    ]

    return templates.TemplateResponse(
        request,
        "reconciliation_results.html",
        {"user": user, "rezultate": rezultate, "run_id": run_id, "stare": stare},
    )


@router.post("/reconciliere/rezultate/{result_id}/decide")
def decide(
    result_id: int,
    stare: str = Form(...),
    decizie: str = Form(""),
    motiv: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    try:
        decide_result(
            db, result_id, stare, utilizator_id=user.id, decizie=decizie or None, motiv=motiv or None
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return RedirectResponse(url="/reconciliere/rezultate", status_code=303)
