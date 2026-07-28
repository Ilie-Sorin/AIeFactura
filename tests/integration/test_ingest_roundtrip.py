"""Test de capăt-la-capăt care verifică criteriile de terminat MVP 2 și 4
(cap. 14): reimportul aceluiași lot nu creează documente noi, iar anularea
unui lot readuce baza la starea dinaintea lui, verificat prin numărătoare
de control pe tabelele afectate."""

from pathlib import Path

from sqlalchemy import func, select

from app.models.document import Invoice, InvoiceLine
from app.models.ingestion import ImportBatch, InvoiceSourceLink, SourceObject
from app.services.ingest import (
    IngestFile,
    cancel_batch,
    finish_batch,
    ingest_file,
    safe_ingest_file,
    start_batch,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_import_reimport_and_cancel_roundtrip(db_session_commit):
    session = db_session_commit

    # --- import initial: factura normala + storno ---
    batch1 = start_batch(session, tip="scan_local", sursa="test")
    r1 = ingest_file(session, batch1, IngestFile(_read("factura_normala.xml"), "factura_normala.xml"))
    r2 = ingest_file(session, batch1, IngestFile(_read("factura_storno.xml"), "factura_storno.xml"))
    finish_batch(session, batch1)
    session.commit()

    assert r1.stare == "importat"
    assert r2.stare == "importat"
    assert _count(session, Invoice) == 2
    assert _count(session, InvoiceLine) == 4  # 2 linii per document
    source_objects_dupa_import1 = _count(session, SourceObject)

    # --- criteriul 2: reimportarea aceluiasi lot nu creeaza documente noi ---
    batch2 = start_batch(session, tip="scan_local", sursa="test-reimport")
    r1b = ingest_file(session, batch2, IngestFile(_read("factura_normala.xml"), "factura_normala.xml"))
    r2b = ingest_file(session, batch2, IngestFile(_read("factura_storno.xml"), "factura_storno.xml"))
    finish_batch(session, batch2)
    session.commit()

    assert r1b.stare == "duplicat"
    assert r2b.stare == "duplicat"
    assert _count(session, Invoice) == 2
    assert _count(session, InvoiceSourceLink) == 2
    # sursele binare noi (XML-urile reimportate) TOT se stocheaza -- captura completa (P1) --
    # doar factura normalizata nu se duplica.
    assert _count(session, SourceObject) == source_objects_dupa_import1 + 2

    # --- criteriul 4: anularea lotului readuce baza la starea initiala ---
    checksum_surse_inainte_de_anulare = _count(session, SourceObject)

    cancel_batch(session, batch1, motiv="test anulare")
    session.commit()

    assert _count(session, Invoice) == 0
    assert _count(session, InvoiceLine) == 0
    # sursele binare NU se sterg la anulare (insert-only, cap. 4)
    assert _count(session, SourceObject) == checksum_surse_inainte_de_anulare

    batch1_reloaded = session.get(ImportBatch, batch1.id)
    assert batch1_reloaded.stare == "anulat"
    assert batch1_reloaded.motiv_anulare == "test anulare"


def test_corrupted_zip_does_not_stop_batch(db_session_commit):
    session = db_session_commit
    batch = start_batch(session, tip="scan_local", sursa="test-corupt")

    r_corupt = ingest_file(session, batch, IngestFile(b"PK\x03\x04not-a-real-zip", "corupt.zip"))
    r_ok = ingest_file(session, batch, IngestFile(_read("factura_normala.xml"), "factura_normala.xml"))
    finish_batch(session, batch)
    session.commit()

    assert r_corupt.stare == "eroare"
    assert r_ok.stare == "importat"
    assert batch.nr_erori == 1
    assert batch.nr_documente == 1
    assert batch.stare == "terminat_cu_erori"


def test_line_sum_mismatch_is_imported_with_stare_eroare(db_session_commit):
    """Criteriul 3 (cap. 14): suma liniilor = totalul = suma pe cote TVA, sau
    diferenta e raportata explicit -- documentul tot se importa (nu se respinge),
    doar cu starea si mesajul de eroare completate."""
    session = db_session_commit
    batch = start_batch(session, tip="scan_local", sursa="test-dezechilibrat")

    rezultat = ingest_file(
        session, batch, IngestFile(_read("factura_dezechilibrata.xml"), "factura_dezechilibrata.xml")
    )
    finish_batch(session, batch)
    session.commit()

    assert rezultat.stare == "importat"
    assert rezultat.invoice_id is not None

    invoice = session.get(Invoice, rezultat.invoice_id)
    assert invoice.stare == "eroare"
    assert "suma" in (invoice.eroare_mesaj or "").lower()
    assert invoice.eroare_detalii is not None
    # documentul tot exista cu toate liniile lui, in ciuda neconcordantei
    assert len(invoice.lines) == 1


def test_large_invoice_number_does_not_overflow(db_session_commit):
    """Regresie: numere de factura reale (secvente ERP) pot avea 10+ cifre,
    peste limita INTEGER pe 32 de biti a Postgres -- numar_numeric e
    BigInteger (a picat cu 'integer out of range' pe date reale)."""
    session = db_session_commit
    batch = start_batch(session, tip="scan_local", sursa="test-numar-mare")
    rezultat = ingest_file(
        session, batch, IngestFile(_read("factura_numar_mare.xml"), "factura_numar_mare.xml")
    )
    finish_batch(session, batch)
    session.commit()

    assert rezultat.stare == "importat"
    invoice = session.get(Invoice, rezultat.invoice_id)
    assert invoice.numar_numeric == 6030066180


def test_safe_ingest_file_isolates_unexpected_errors_per_file(db_session_commit, monkeypatch):
    """O eroare NEAȘTEPTATĂ (nu doar XML nevalid/CIF lipsă, deja gestionate
    fără să arunce) la un singur fișier nu trebuie să oprească sau să
    anuleze restul lotului -- regresie după un caz real în care o eroare de
    baza de date la un fișier a picat întreaga cerere de scanare, fără să
    păstreze fișierele deja procesate cu succes înaintea lui."""
    import app.services.ingest as ingest_module

    original_consolidate = ingest_module.consolidate_invoice

    def _boom(session, invoice):
        if invoice.numar_brut == "0000182":  # doar factura_storno.xml
            raise RuntimeError("eroare neașteptată simulată")
        return original_consolidate(session, invoice)

    monkeypatch.setattr(ingest_module, "consolidate_invoice", _boom)

    session = db_session_commit
    batch = start_batch(session, tip="scan_local", sursa="test-eroare-neasteptata")

    r1 = safe_ingest_file(session, batch, IngestFile(_read("factura_normala.xml"), "factura_normala.xml"))
    r2 = safe_ingest_file(session, batch, IngestFile(_read("factura_storno.xml"), "factura_storno.xml"))
    r3 = safe_ingest_file(session, batch, IngestFile(_read("factura_corectata.xml"), "factura_corectata.xml"))
    finish_batch(session, batch)
    session.commit()

    assert r1.stare == "importat"
    assert r2.stare == "eroare"
    assert "eroare neașteptată" in r2.mesaj
    assert r3.stare == "importat"

    assert batch.nr_erori == 1
    assert batch.nr_fisiere == 3

    # fisierul care a picat NU a lasat un rand Invoice orfan -- SAVEPOINT-ul
    # i-a anulat scrierile, desi apucase deja sa insereze invoice+linii+parti
    # inainte de eroarea simulata din consolidare.
    numere = {i.numar_brut for i in session.scalars(select(Invoice)).all()}
    assert numere == {"0001234", "0000183"}
