from datetime import date
from decimal import Decimal

from app.services.integrity import check_cif_valid, check_line_sum_vs_total
from app.services.xml_parser import ParsedInvoice, ParsedLine, ParsedTaxSummary


def _line(valoare: Decimal) -> ParsedLine:
    return ParsedLine(
        nr_crt=1,
        cod_articol_furnizor=None,
        cod_articol_client=None,
        denumire="x",
        descriere="x",
        cantitate=Decimal("1"),
        um="H87",
        pret_unitar=valoare,
        valoare_fara_tva=valoare,
        cota_tva=Decimal("19"),
        categorie_tva="S",
        reducere=None,
        nr_comanda=None,
        xpath=None,
    )


def _parsed(total_fara_tva, total_tva, total_document, lines, tax_summaries) -> ParsedInvoice:
    return ParsedInvoice(
        document_type="Invoice",
        numar_brut="1",
        data_emitere=date(2026, 1, 1),
        data_scadenta=None,
        tip_document="380",
        moneda="RON",
        nr_contract=None,
        nr_comanda=None,
        perioada_start=None,
        perioada_sfarsit=None,
        versiune_cius=None,
        total_fara_tva=total_fara_tva,
        total_tva=total_tva,
        total_document=total_document,
        total_de_plata=total_document,
        lines=lines,
        tax_summaries=tax_summaries,
    )


def test_balanced_invoice_has_no_issue():
    parsed = _parsed(
        Decimal("100.00"),
        Decimal("19.00"),
        Decimal("119.00"),
        [_line(Decimal("100.00"))],
        [ParsedTaxSummary(cota=Decimal("19"), categorie="S", baza=Decimal("100.00"), tva=Decimal("19.00"))],
    )
    assert check_line_sum_vs_total(parsed) is None


def test_mismatched_line_sum_is_flagged():
    parsed = _parsed(
        Decimal("100.00"),
        Decimal("19.00"),
        Decimal("119.00"),
        [_line(Decimal("90.00"))],
        [ParsedTaxSummary(cota=Decimal("19"), categorie="S", baza=Decimal("100.00"), tva=Decimal("19.00"))],
    )
    issue = check_line_sum_vs_total(parsed)
    assert issue is not None
    assert issue.cod == "suma_linii_vs_total"


def test_small_rounding_within_tolerance_is_ok():
    parsed = _parsed(
        Decimal("100.00"),
        Decimal("19.00"),
        Decimal("119.01"),
        [_line(Decimal("100.00"))],
        [ParsedTaxSummary(cota=Decimal("19"), categorie="S", baza=Decimal("100.00"), tva=Decimal("19.00"))],
    )
    assert check_line_sum_vs_total(parsed) is None


def test_cif_valid_and_invalid():
    assert check_cif_valid("185472901") is None
    issue = check_cif_valid("185472900")
    assert issue is not None
    assert issue.cod == "cif_invalid"


def test_cif_foreign_or_indeterminate_not_flagged():
    assert check_cif_valid("DE123456789") is None
    assert check_cif_valid(None) is None
