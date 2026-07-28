"""Scanner local de foldere (cap. 4A): scanare recursivă ZIP/XML, indiferent de
structura directoarelor, cu import manual (drag-and-drop, vezi routere) și
monitorizare periodică a directoarelor configurate (vezi scheduler.py)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Invoice
from app.models.ingestion import ImportBatch, SourceObject
from app.services.consolidation import resolve_pending_references_for_suppliers
from app.services.ingest import FileResult, IngestFile, finish_batch, safe_ingest_file, start_batch

EXTENSII_ACCEPTATE = (".zip", ".xml")
COMMIT_LA_FIECARE = 200


def _already_seen(session: Session, cale_originala: str) -> bool:
    return (
        session.scalar(select(SourceObject.id).where(SourceObject.cale_originala == cale_originala))
        is not None
    )


def find_candidate_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSII_ACCEPTATE
    )


def scan_directory(
    session: Session,
    root: Path,
    *,
    tip: str = "scan_local",
    sursa: str | None = None,
    utilizator_id: int | None = None,
) -> tuple[ImportBatch, list[FileResult]]:
    """Scanează recursiv `root`, ingerând fișierele noi (după cale originală).
    Fișierele deja văzute la o scanare anterioară se sar fără a le mai citi —
    optimizare esențială pentru monitorizarea periodică a acelorași directoare
    mari (cap. 4A: „ignore ce a mai importat")."""
    batch = start_batch(session, tip=tip, sursa=sursa or str(root), utilizator_id=utilizator_id)
    batch_id = batch.id
    session.commit()
    rezultate: list[FileResult] = []
    for i, path in enumerate(find_candidate_files(root), start=1):
        cale_str = str(path)
        if _already_seen(session, cale_str):
            continue
        continut = path.read_bytes()
        rezultat = safe_ingest_file(
            session,
            batch,
            IngestFile(continut=continut, nume_original=path.name, cale_originala=cale_str),
            utilizator_id=utilizator_id,
        )
        rezultate.append(rezultat)
        # Progresul se salveaza incremental -- un lot de mii de fisiere nu
        # trebuie sa depinda de o singura tranzactie uriasa pana la capat
        # (P1: captura completa si verificabila, nu "totul sau nimic").
        if i % COMMIT_LA_FIECARE == 0:
            session.commit()
            # Fara asta, identity map-ul sesiunii creste nemarginit pe un lot
            # de mii de fisiere -- fiecare autoflush (declansat de orice SELECT
            # din ingest_file/consolidation) scaneaza tot ce e urmarit in
            # sesiune, deci viteza scade progresiv pe masura ce lotul avanseaza
            # (confirmat pe date reale: ~100/min la inceput, ~7/min dupa 1000
            # de facturi in aceeasi sesiune). `batch` trebuie reincarcat dupa
            # expunge_all(), fiindca devine detasat de sesiune -- de-aia
            # `batch_id` s-a retinut separat, ca simplu int, INAINTE de commit
            # (dupa commit, `batch.id` insusi e expirat si inaccesibil pe un
            # obiect deja detasat de expunge_all()).
            session.expunge_all()
            batch = session.get(ImportBatch, batch_id)

    # Referintele storno intarziate (originalul soseste DUPA cel care il
    # referentiaza) se reincearca o singura data per furnizor atins de acest
    # lot -- nu per factura (vezi consolidate_invoice).
    furnizori_atinsi = set(
        session.scalars(
            select(Invoice.cif_emitent).where(Invoice.batch_id == batch_id).distinct()
        ).all()
    )
    if furnizori_atinsi:
        resolve_pending_references_for_suppliers(session, furnizori_atinsi)

    finish_batch(session, batch)
    session.commit()
    return batch, rezultate
