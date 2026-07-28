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


def test_find_candidate_files_skips_loose_xml_with_sibling_zip(tmp_path):
    # unele unelte de descarcare lasa langa ZIP-ul original si o copie
    # extrasa a XML-ului -- ZIP-ul e exemplarul autoritativ (conservare cap. 3).
    (tmp_path / "F_123.xml").write_bytes(b"<x/>")
    (tmp_path / "F_123.zip").write_bytes(b"PK")
    (tmp_path / "F_456.xml").write_bytes(b"<y/>")  # fara zip sora -> ramane

    found = find_candidate_files(tmp_path)
    names = sorted(p.name for p in found)
    assert names == ["F_123.zip", "F_456.xml"]


def test_find_candidate_files_keeps_xml_when_zip_in_different_dir(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "F_789.xml").write_bytes(b"<x/>")
    (tmp_path / "sub" / "F_789.zip").write_bytes(b"PK")

    found = find_candidate_files(tmp_path)
    names = sorted(p.name for p in found)
    assert names == ["F_789.xml", "F_789.zip"]


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
