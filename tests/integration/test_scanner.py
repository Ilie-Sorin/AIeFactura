from pathlib import Path

from sqlalchemy import func, select

from app.models.document import Invoice
from app.services.scanner import find_candidate_files, scan_directory

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_find_candidate_files_recursive(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.xml").write_bytes(b"<x/>")
    (tmp_path / "sub" / "b.zip").write_bytes(b"PK")
    (tmp_path / "ignora.txt").write_bytes(b"nimic")

    found = find_candidate_files(tmp_path)
    names = sorted(p.name for p in found)
    assert names == ["a.xml", "b.zip"]


def test_find_candidate_files_missing_root_returns_empty(tmp_path):
    assert find_candidate_files(tmp_path / "nu-exista") == []


def test_scan_directory_ingests_and_skips_reseen_paths(tmp_path, db_session_commit):
    session = db_session_commit
    (tmp_path / "factura_normala.xml").write_bytes((FIXTURES / "factura_normala.xml").read_bytes())

    batch1, rezultate1 = scan_directory(session, tmp_path, tip="scan_local")
    session.commit()
    assert len(rezultate1) == 1
    assert rezultate1[0].stare == "importat"
    assert session.scalar(select(func.count()).select_from(Invoice)) == 1
    assert batch1.nr_documente == 1

    # A doua scanare a aceluiasi folder: fisierul e deja vazut (aceeasi cale) -> sarit.
    batch2, rezultate2 = scan_directory(session, tmp_path, tip="scan_local")
    session.commit()
    assert rezultate2 == []
    assert session.scalar(select(func.count()).select_from(Invoice)) == 1
