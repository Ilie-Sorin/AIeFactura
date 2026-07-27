from decimal import Decimal
from pathlib import Path

import pytest

from app.services.xml_parser import (
    InvoiceXmlError,
    classify_xml_bytes,
    parse_invoice_xml,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_parse_factura_normala_header():
    parsed = parse_invoice_xml(_load("factura_normala.xml"))

    assert parsed.document_type == "Invoice"
    assert parsed.numar_brut == "0001234"
    assert parsed.data_emitere.isoformat() == "2026-03-10"
    assert parsed.moneda == "RON"
    assert parsed.total_fara_tva == Decimal("15630.00")
    assert parsed.total_tva == Decimal("2969.70")
    assert parsed.total_document == Decimal("18599.70")
    assert parsed.total_de_plata == Decimal("18599.70")
    assert parsed.nr_comanda == "CMD-2026-0099"
    assert "CIUS-RO" in (parsed.versiune_cius or "")


def test_parse_factura_normala_xpath_provenance():
    parsed = parse_invoice_xml(_load("factura_normala.xml"))

    # lxml getpath() foloseste '*' pt. elementele fara prefix inregistrat la acel
    # nivel (namespace implicit) - ramane un XPath valid, evaluabil pe document.
    assert parsed.xpath_map["numar_brut"] == "/*/cbc:ID"
    assert parsed.xpath_map["total_document"].endswith("TaxInclusiveAmount")
    for line in parsed.lines:
        assert line.xpath is not None and line.xpath.startswith("/*/cac:InvoiceLine")


def test_parse_factura_normala_parties():
    parsed = parse_invoice_xml(_load("factura_normala.xml"))
    roles = {p.rol: p for p in parsed.parts}

    assert roles["furnizor"].denumire == "Furnizor Exemplu SRL"
    assert roles["furnizor"].cif_brut == "185472901"
    assert roles["furnizor"].cod_tva == "RO185472901"
    assert roles["furnizor"].cont_bancar == "RO49AAAA1B31007593840000"
    assert roles["furnizor"].tara == "RO"

    assert roles["client"].denumire == "Client Exemplu SA"
    assert roles["client"].cif_brut == "143981007"
    assert "reprezentant_fiscal" not in roles


def test_parse_factura_normala_lines_and_tax_summary():
    parsed = parse_invoice_xml(_load("factura_normala.xml"))

    assert len(parsed.lines) == 2
    line1 = parsed.lines[0]
    assert line1.nr_crt == 1
    assert line1.cod_articol_furnizor == "SKU-100"
    assert line1.cantitate == Decimal("1")
    assert line1.um == "H87"
    assert line1.pret_unitar == Decimal("10000.00")
    assert line1.valoare_fara_tva == Decimal("10000.00")
    assert line1.cota_tva == Decimal("19.00")

    suma_linii = sum((l.valoare_fara_tva for l in parsed.lines), Decimal("0"))
    assert suma_linii == parsed.total_fara_tva

    assert len(parsed.tax_summaries) == 1
    assert parsed.tax_summaries[0].baza == Decimal("15630.00")
    assert parsed.tax_summaries[0].tva == Decimal("2969.70")


def test_parse_factura_storno_references_original():
    parsed = parse_invoice_xml(_load("factura_storno.xml"))

    assert parsed.document_type == "CreditNote"
    assert parsed.numar_brut == "0000182"
    storno_refs = [r for r in parsed.references if r.tip == "storno"]
    assert len(storno_refs) == 1
    assert storno_refs[0].valoare == "0001234"


def test_parse_malformed_xml_raises_with_message():
    with pytest.raises(InvoiceXmlError):
        parse_invoice_xml(_load("factura_invalida.xml"))


def test_xxe_entity_is_not_resolved():
    # Cu load_dtd=False entitatea externa nu se mai defineste, deci referinta &xxe;
    # ramane nerezolvata -> XML tratat ca neinterpretabil, nu se scurge continut de fisier.
    with pytest.raises(InvoiceXmlError):
        parse_invoice_xml(_load("factura_xxe.xml"))


def test_classify_xml_bytes():
    assert classify_xml_bytes(_load("factura_normala.xml")) == "xml_factura"
    assert classify_xml_bytes(_load("factura_storno.xml")) == "xml_factura"
    assert classify_xml_bytes(b"<not-xml-at-all") == "atasament"


def test_missing_mandatory_field_raises():
    xml = b"""<?xml version="1.0"?>
    <Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
             xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
        <cbc:IssueDate>2026-01-01</cbc:IssueDate>
    </Invoice>"""
    with pytest.raises(InvoiceXmlError):
        parse_invoice_xml(xml)
