"""Scanner local de foldere (cap. 4A): scanare recursivă ZIP/XML, indiferent de
structura directoarelor, cu import manual (drag-and-drop, vezi routere) și
monitorizare periodică a directoarelor configurate (vezi scheduler.py)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ingestion import ImportBatch, SourceObject
from app.services.ingest import FileResult, IngestFile, finish_batch, ingest_file, start_batch

EXTENSII_ACCEPTATE = (".zip", ".xml")


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
    rezultate: list[FileResult] = []
    for path in find_candidate_files(root):
        cale_str = str(path)
        if _already_seen(session, cale_str):
            continue
        continut = path.read_bytes()
        rezultat = ingest_file(
            session,
            batch,
            IngestFile(continut=continut, nume_original=path.name, cale_originala=cale_str),
            utilizator_id=utilizator_id,
        )
        rezultate.append(rezultat)
    finish_batch(session, batch)
    return batch, rezultate
