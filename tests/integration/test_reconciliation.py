"""Motorul de reconciliere (cap. 7): scenariile MVP fata de contabilitate."""

from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.reconciliation import ImportProfile, ReconciliationResult, ReconciliationRule
from app.security import create_user
from app.services.external_import import import_external_records
from app.services.ingest import IngestFile, finish_batch, ingest_file, start_batch
from app.services.reconciliation import decide_result, run_reconciliation

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

REGULA_DEFINITIE = {
    "regula": "efactura_vs_registru_contabil",
    "sursa_a": "efactura.grup",
    "sursa_b": "import.registru_contabil",
    "grupare": ["cif_furnizor", "luna_document"],
    "componente": [
        {"camp": "numar_normalizat", "pondere": 40},
        {"camp": "cif_furnizor", "pondere": 25},
        {"camp": "total", "pondere": 25, "toleranta": 0.02},
        {"camp": "data_document", "pondere": 10, "toleranta_zile": 3},
    ],
    "praguri": {"acceptare_automata": 90, "exceptie_sub": 60},
}

MAPARE = {
    "cif": "CIF",
    "numar_brut": "Numar",
    "data": "Data",
    "total_fara_tva": "Fara TVA",
    "total_tva": "TVA",
    "total": "Total",
}


def _profil(session) -> ImportProfile:
    profile = ImportProfile(
        denumire="Registru contabil",
        tip_sursa="registru_contabil",
        format="csv",
        mapare=MAPARE,
        reguli_curatare={"format_data": "%d.%m.%Y", "separator_zecimal": ",", "separator_csv": ";"},
        activ=True,
    )
    session.add(profile)
    session.flush()
    return profile


def _regula(session, definitie=None) -> ReconciliationRule:
    rule = ReconciliationRule(
        denumire="e-Factura vs. registru contabil", definitie=definitie or REGULA_DEFINITIE, activa=True
    )
    session.add(rule)
    session.flush()
    return rule


def _importa_factura(session, nume: str):
    batch = start_batch(session, tip="scan_local", sursa="test")
    rezultat = ingest_file(session, batch, IngestFile((FIXTURES / nume).read_bytes(), nume))
    finish_batch(session, batch)
    return rezultat


def _importa_extern(session, profile, csv_text: str):
    batch = start_batch(session, tip="import_extern", sursa="test")
    records = import_external_records(session, profile, batch, csv_text.encode("utf-8-sig"))
    finish_batch(session, batch)
    return batch, records


def test_matching_pair_is_auto_resolved(db_session_commit):
    session = db_session_commit
    r_factura = _importa_factura(session, "factura_normala.xml")
    profile = _profil(session)
    _importa_extern(
        session,
        profile,
        "CIF;Numar;Data;Fara TVA;TVA;Total\n"
        "185472901;0001234;10.03.2026;15630,00;2969,70;18599,70\n",
    )
    rule = _regula(session)
    session.commit()

    run = run_reconciliation(session, rule)
    session.commit()

    assert run.nr_potriviri == 1
    assert run.nr_exceptii == 0
    assert run.nr_ambigue == 0

    rezultat = session.scalar(select(ReconciliationResult).where(ReconciliationResult.run_id == run.id))
    assert rezultat.stare == "rezolvata"
    assert rezultat.decizie == "potrivire_automata"
    assert rezultat.scor == 100
    assert rezultat.diferente["total"]["potrivire"] is True


def test_invoice_without_accounting_entry_is_an_exception(db_session_commit):
    session = db_session_commit
    _importa_factura(session, "factura_normala.xml")
    _profil(session)  # profil exista, dar nu se importa niciun external_record
    rule = _regula(session)
    session.commit()

    run = run_reconciliation(session, rule)
    session.commit()

    assert run.nr_exceptii == 1
    rezultat = session.scalar(select(ReconciliationResult).where(ReconciliationResult.run_id == run.id))
    assert rezultat.group_id is not None
    assert rezultat.external_record_id is None
    assert rezultat.diferente["tip"] == "lipsa_in_contabilitate"
    assert rezultat.stare == "noua"


def test_accounting_entry_without_invoice_is_an_exception(db_session_commit):
    session = db_session_commit
    profile = _profil(session)
    _importa_extern(
        session,
        profile,
        "CIF;Numar;Data;Fara TVA;TVA;Total\n"
        "185472901;0009999;10.03.2026;100,00;19,00;119,00\n",
    )
    rule = _regula(session)
    session.commit()

    run = run_reconciliation(session, rule)
    session.commit()

    assert run.nr_exceptii == 1
    rezultat = session.scalar(select(ReconciliationResult).where(ReconciliationResult.run_id == run.id))
    assert rezultat.group_id is None
    assert rezultat.external_record_id is not None
    assert rezultat.diferente["tip"] == "lipsa_in_efactura"


def test_value_mismatch_scores_below_threshold_and_reports_difference(db_session_commit):
    session = db_session_commit
    _importa_factura(session, "factura_normala.xml")
    profile = _profil(session)
    # acelasi numar/cif/data, dar total gresit cu 50 lei -- peste toleranta de 0.02
    _importa_extern(
        session,
        profile,
        "CIF;Numar;Data;Fara TVA;TVA;Total\n"
        "185472901;0001234;10.03.2026;15580,00;2969,70;18549,70\n",
    )
    rule = _regula(session)
    session.commit()

    run = run_reconciliation(session, rule)
    session.commit()

    assert run.nr_ambigue == 1  # scor 75 (40+25+10, fara componenta 'total') -- intre praguri
    rezultat = session.scalar(select(ReconciliationResult).where(ReconciliationResult.run_id == run.id))
    assert rezultat.scor == 75
    assert rezultat.diferente["total"]["potrivire"] is False
    assert rezultat.diferente["total"]["diferenta"] == "50.00"
    assert rezultat.stare == "noua"


def test_human_decision_survives_rerun_with_fresh_score(db_session_commit):
    session = db_session_commit
    r_factura = _importa_factura(session, "factura_normala.xml")
    profile = _profil(session)
    _importa_extern(
        session,
        profile,
        "CIF;Numar;Data;Fara TVA;TVA;Total\n"
        "185472901;0001234;10.03.2026;15630,00;2969,70;18599,70\n",
    )
    rule = _regula(session)
    session.commit()

    utilizator = create_user(session, "revizor", "parola123")
    session.commit()

    run1 = run_reconciliation(session, rule)
    session.commit()
    rezultat1 = session.scalar(select(ReconciliationResult).where(ReconciliationResult.run_id == run1.id))

    decide_result(
        session, rezultat1.id, "ignorata", utilizator_id=utilizator.id, decizie="fals_pozitiv",
        motiv="verificat manual, e ok",
    )
    session.commit()

    run2 = run_reconciliation(session, rule)
    session.commit()
    rezultat2 = session.scalar(select(ReconciliationResult).where(ReconciliationResult.run_id == run2.id))

    assert rezultat2.id != rezultat1.id  # rezultat nou, dar decizia s-a copiat
    assert rezultat2.stare == "ignorata"
    assert rezultat2.decizie == "fals_pozitiv"
    assert rezultat2.motiv == "verificat manual, e ok"
    assert rezultat2.utilizator_id == utilizator.id
    assert rezultat2.scor == 100  # scorul tot s-a recalculat


def test_decide_result_requires_motiv_for_dismiss_or_accept_diff(db_session_commit):
    session = db_session_commit
    _importa_factura(session, "factura_normala.xml")
    rule = _regula(session)
    utilizator = create_user(session, "revizor2", "parola123")
    session.commit()
    run = run_reconciliation(session, rule)
    session.commit()
    rezultat = session.scalar(select(ReconciliationResult).where(ReconciliationResult.run_id == run.id))

    with pytest.raises(ValueError):
        decide_result(session, rezultat.id, "ignorata", utilizator_id=utilizator.id, motiv="")

    with pytest.raises(ValueError):
        decide_result(session, rezultat.id, "acceptata_ca_diferenta", utilizator_id=utilizator.id, motiv=None)
