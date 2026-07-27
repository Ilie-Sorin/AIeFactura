"""Motorul de reconciliere (cap. 7).

Regula (grupare/componente/ponderi/praguri) e stocată în JSONB
(`reconciliation_rule.definitie`), nu în cod — vezi structura YAML din
specificație:

    grupare: [cif_furnizor, luna_document]
    componente:
      - camp: numar_normalizat   pondere: 40   tip: exact_apoi_normalizat
      - camp: cif_furnizor       pondere: 25   tip: exact
      - camp: total              pondere: 25   toleranta: 0.02
      - camp: data_document      pondere: 10   toleranta_zile: 3
    praguri: {acceptare_automata: 90, exceptie_sub: 60}

Grupul (cap. 6), nu factura individuală, e unitatea comparată — poziția netă
și numărul oricărui membru intră în scor. Deciziile umane (confirmare,
acceptare ca diferență, ignorare) supraviețuiesc rulărilor ulterioare: o
rulare nouă recalculează scorul/diferențele pentru toată lumea, dar
COPIAZĂ mai departe decizia pentru perechile deja tranșate de un om.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.consolidation import InvoiceGroup, InvoiceGroupMember
from app.models.document import Invoice
from app.models.reconciliation import (
    ExternalRecord,
    ImportProfile,
    ReconciliationResult,
    ReconciliationRule,
    ReconciliationRun,
)

STARI_DECIZIE_CU_MOTIV_OBLIGATORIU = ("acceptata_ca_diferenta", "ignorata")
CAMPURI_GRUPARE_SUPORTATE = ("cif_furnizor", "luna_document")
CAMPURI_COMPONENTA_SUPORTATE = ("numar_normalizat", "cif_furnizor", "total", "data_document")


class ReconciliationRuleError(Exception):
    """Definiția regulii (JSONB) e incompletă sau folosește câmpuri nesuportate."""


@dataclass
class _GrupInfo:
    id: int
    cif: str | None
    numere: set[str]
    date: list[dt.date]
    total: Decimal


def _valideaza_definitie(definitie: dict) -> None:
    for cheie in ("grupare", "componente", "praguri"):
        if cheie not in definitie:
            raise ReconciliationRuleError(f"regula nu are cheia obligatorie '{cheie}'")
    for camp in definitie["grupare"]:
        if camp not in CAMPURI_GRUPARE_SUPORTATE:
            raise ReconciliationRuleError(f"câmp de grupare nesuportat: {camp!r}")
    for componenta in definitie["componente"]:
        if componenta.get("camp") not in CAMPURI_COMPONENTA_SUPORTATE:
            raise ReconciliationRuleError(f"câmp de componentă nesuportat: {componenta.get('camp')!r}")
    for prag in ("acceptare_automata", "exceptie_sub"):
        if prag not in definitie["praguri"]:
            raise ReconciliationRuleError(f"lipsește pragul '{prag}'")


def _luna(d: dt.date | None) -> str | None:
    return d.strftime("%Y-%m") if d else None


def _incarca_grupuri(session: Session) -> list[_GrupInfo]:
    grupuri = session.scalars(select(InvoiceGroup)).all()
    rezultat = []
    for g in grupuri:
        membri = session.scalars(
            select(InvoiceGroupMember).where(InvoiceGroupMember.group_id == g.id)
        ).all()
        if not membri:
            continue
        facturi = {
            f.id: f
            for f in session.scalars(
                select(Invoice).where(Invoice.id.in_([m.invoice_id for m in membri]))
            ).all()
        }
        rezultat.append(
            _GrupInfo(
                id=g.id,
                cif=next((f.cif_emitent for f in facturi.values()), None),
                numere={f.numar_normalizat for f in facturi.values()},
                date=[f.data_emitere for f in facturi.values()],
                total=g.pozitie_neta if g.pozitie_neta is not None else Decimal("0"),
            )
        )
    return rezultat


def _grup_blocking_value(grup: _GrupInfo, camp: str):
    if camp == "cif_furnizor":
        return grup.cif
    if camp == "luna_document":
        return _luna(min(grup.date)) if grup.date else None
    raise ReconciliationRuleError(f"câmp de grupare nesuportat: {camp!r}")


def _extern_blocking_value(rec: ExternalRecord, camp: str):
    if camp == "cif_furnizor":
        return rec.cif
    if camp == "luna_document":
        return _luna(rec.data)
    raise ReconciliationRuleError(f"câmp de grupare nesuportat: {camp!r}")


def _scor_componenta(componenta: dict, grup: _GrupInfo, rec: ExternalRecord) -> tuple[Decimal, dict]:
    camp = componenta["camp"]
    pondere = Decimal(str(componenta["pondere"]))

    if camp == "numar_normalizat":
        potrivire = rec.numar_normalizat in grup.numere
        return (pondere if potrivire else Decimal("0")), {
            "grup": sorted(grup.numere),
            "extern": rec.numar_normalizat,
            "potrivire": potrivire,
        }
    if camp == "cif_furnizor":
        potrivire = rec.cif == grup.cif
        return (pondere if potrivire else Decimal("0")), {
            "grup": grup.cif,
            "extern": rec.cif,
            "potrivire": potrivire,
        }
    if camp == "total":
        toleranta = Decimal(str(componenta.get("toleranta", 0)))
        diferenta = abs((rec.total or Decimal("0")) - grup.total)
        potrivire = diferenta <= toleranta
        return (pondere if potrivire else Decimal("0")), {
            "grup": str(grup.total),
            "extern": str(rec.total) if rec.total is not None else None,
            "diferenta": str(diferenta),
            "potrivire": potrivire,
        }
    if camp == "data_document":
        toleranta_zile = int(componenta.get("toleranta_zile", 0))
        if not grup.date or rec.data is None:
            return Decimal("0"), {"potrivire": False, "motiv": "dată lipsă"}
        zile = min(abs((rec.data - d).days) for d in grup.date)
        potrivire = zile <= toleranta_zile
        return (pondere if potrivire else Decimal("0")), {"zile_distanta": zile, "potrivire": potrivire}

    raise ReconciliationRuleError(f"câmp de componentă nesuportat: {camp!r}")


def _scoreaza(definitie: dict, grup: _GrupInfo, rec: ExternalRecord) -> tuple[Decimal, dict]:
    scor = Decimal("0")
    detalii: dict = {}
    for componenta in definitie["componente"]:
        punctaj, info = _scor_componenta(componenta, grup, rec)
        scor += punctaj
        detalii[componenta["camp"]] = info
    return scor, detalii


def _gaseste_decizie_anterioara(
    session: Session, group_id: int | None, external_record_id: int | None
) -> ReconciliationResult | None:
    """Ultima decizie UMANĂ (utilizator_id IS NOT NULL) pentru exact aceeași
    pereche, din orice rulare anterioară — asta se copiază mai departe."""
    query = select(ReconciliationResult).where(ReconciliationResult.utilizator_id.isnot(None))
    query = query.where(
        ReconciliationResult.group_id == group_id
        if group_id is not None
        else ReconciliationResult.group_id.is_(None)
    )
    query = query.where(
        ReconciliationResult.external_record_id == external_record_id
        if external_record_id is not None
        else ReconciliationResult.external_record_id.is_(None)
    )
    return session.scalar(query.order_by(ReconciliationResult.id.desc()).limit(1))


def _creeaza_rezultat(
    session: Session,
    run_id: int,
    group_id: int | None,
    external_record_id: int | None,
    scor: Decimal | None,
    diferente: dict,
    *,
    auto: bool = False,
) -> ReconciliationResult:
    anterior = _gaseste_decizie_anterioara(session, group_id, external_record_id)
    if anterior is not None:
        stare, decizie, motiv = anterior.stare, anterior.decizie, anterior.motiv
        utilizator_id, decis_la = anterior.utilizator_id, anterior.decis_la
    elif auto:
        stare, decizie = "rezolvata", "potrivire_automata"
        motiv = utilizator_id = decis_la = None
    else:
        stare = "noua"
        decizie = motiv = utilizator_id = decis_la = None

    rezultat = ReconciliationResult(
        run_id=run_id,
        group_id=group_id,
        external_record_id=external_record_id,
        scor=scor,
        stare=stare,
        diferente=diferente,
        decizie=decizie,
        motiv=motiv,
        utilizator_id=utilizator_id,
        decis_la=decis_la,
    )
    session.add(rezultat)
    return rezultat


def run_reconciliation(
    session: Session, rule: ReconciliationRule, utilizator_id: int | None = None
) -> ReconciliationRun:
    definitie = rule.definitie
    _valideaza_definitie(definitie)

    campuri_grupare = definitie["grupare"]
    prag_auto = Decimal(str(definitie["praguri"]["acceptare_automata"]))
    prag_exceptie = Decimal(str(definitie["praguri"]["exceptie_sub"]))

    tip_sursa_b = str(definitie.get("sursa_b", "")).rsplit(".", maxsplit=1)[-1]
    profil_ids = session.scalars(
        select(ImportProfile.id).where(
            ImportProfile.tip_sursa == tip_sursa_b, ImportProfile.activ.is_(True)
        )
    ).all()

    grupuri = _incarca_grupuri(session)
    externe = (
        session.scalars(select(ExternalRecord).where(ExternalRecord.profil_id.in_(profil_ids))).all()
        if profil_ids
        else []
    )

    bucket_grupuri: dict[tuple, list[_GrupInfo]] = {}
    for g in grupuri:
        cheie = tuple(_grup_blocking_value(g, c) for c in campuri_grupare)
        if None in cheie:
            continue
        bucket_grupuri.setdefault(cheie, []).append(g)

    candidati_per_grup: dict[int, list[tuple[Decimal, ExternalRecord, dict]]] = {}
    externe_atinse: set[int] = set()

    for rec in externe:
        cheie = tuple(_extern_blocking_value(rec, c) for c in campuri_grupare)
        if None in cheie:
            continue
        for grup in bucket_grupuri.get(cheie, []):
            scor, detalii = _scoreaza(definitie, grup, rec)
            if scor < prag_exceptie:
                continue
            candidati_per_grup.setdefault(grup.id, []).append((scor, rec, detalii))

    run = ReconciliationRun(rule_id=rule.id)
    session.add(run)
    session.flush()

    nr_potriviri = nr_exceptii = nr_ambigue = 0

    for grup in grupuri:
        candidati = sorted(candidati_per_grup.get(grup.id, []), key=lambda c: c[0], reverse=True)
        if not candidati:
            _creeaza_rezultat(session, run.id, grup.id, None, None, {"tip": "lipsa_in_contabilitate"})
            nr_exceptii += 1
            continue

        varf = candidati[0][0]
        varfuri = [c for c in candidati if c[0] == varf]
        if varf >= prag_auto and len(varfuri) == 1:
            scor, rec, detalii = candidati[0]
            _creeaza_rezultat(session, run.id, grup.id, rec.id, scor, detalii, auto=True)
            externe_atinse.add(rec.id)
            nr_potriviri += 1
        else:
            for scor, rec, detalii in candidati:
                _creeaza_rezultat(session, run.id, grup.id, rec.id, scor, detalii, auto=False)
                externe_atinse.add(rec.id)
            nr_ambigue += 1

    for rec in externe:
        if rec.id in externe_atinse:
            continue
        _creeaza_rezultat(session, run.id, None, rec.id, None, {"tip": "lipsa_in_efactura"})
        nr_exceptii += 1

    run.nr_potriviri = nr_potriviri
    run.nr_exceptii = nr_exceptii
    run.nr_ambigue = nr_ambigue
    session.flush()
    return run


def decide_result(
    session: Session,
    result_id: int,
    stare: str,
    utilizator_id: int,
    decizie: str | None = None,
    motiv: str | None = None,
) -> ReconciliationResult:
    if stare in STARI_DECIZIE_CU_MOTIV_OBLIGATORIU and not motiv:
        raise ValueError(f"motivul e obligatoriu pentru starea '{stare}'")
    rezultat = session.get(ReconciliationResult, result_id)
    if rezultat is None:
        raise ValueError(f"rezultatul {result_id} nu există")
    rezultat.stare = stare
    rezultat.decizie = decizie
    rezultat.motiv = motiv
    rezultat.utilizator_id = utilizator_id
    rezultat.decis_la = dt.datetime.now(dt.timezone.utc)
    session.flush()
    return rezultat
