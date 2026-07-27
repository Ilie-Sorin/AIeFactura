"""Parser XML pentru facturi UBL 2.1 / RO-CIUS (EN 16931).

Securitate (cap. „Stack impus"): `resolve_entities=False`, `no_network=True`,
`load_dtd=False` elimină expansiunea de entități externe/interne (XXE, billion
laughs) — un `<!DOCTYPE>` cu entități interne nu mai are efect fiindcă DTD-ul
nu se mai încarcă deloc.

Fiecare valoare de antet extrasă își reține XPath-ul de proveniență în
`xpath_map`; liniile au propriul câmp `xpath` (cap. 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from lxml import etree

NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}

_SIGNATURE_ROOT_LOCALNAMES = {"Signature", "XAdESSignatures"}
_INVOICE_ROOT_LOCALNAMES = {"Invoice", "CreditNote"}

_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    huge_tree=False,
)


class InvoiceXmlError(Exception):
    """XML neinterpretabil sau lipsă de câmpuri obligatorii — cu poziția în document."""

    def __init__(self, message: str, xpath: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.xpath = xpath


@dataclass
class ParsedParty:
    rol: str
    denumire: str | None = None
    cif_brut: str | None = None
    nr_reg_com: str | None = None
    adresa: str | None = None
    tara: str | None = None
    cod_tva: str | None = None
    cont_bancar: str | None = None
    contact: str | None = None


@dataclass
class ParsedLine:
    nr_crt: int | None
    cod_articol_furnizor: str | None
    cod_articol_client: str | None
    descriere: str | None
    cantitate: Decimal | None
    um: str | None
    pret_unitar: Decimal | None
    valoare_fara_tva: Decimal | None
    cota_tva: Decimal | None
    categorie_tva: str | None
    reducere: Decimal | None
    nr_comanda: str | None
    xpath: str | None


@dataclass
class ParsedTaxSummary:
    cota: Decimal | None
    categorie: str | None
    baza: Decimal
    tva: Decimal


@dataclass
class ParsedReference:
    tip: str  # storno | comanda | contract | aviz | receptie | comanda_initiator
    valoare: str
    xpath: str | None = None


@dataclass
class ParsedAttachment:
    nume: str | None
    mime: str | None
    descriere: str | None
    continut_base64: str | None


@dataclass
class ParsedInvoice:
    document_type: str  # 'Invoice' | 'CreditNote'
    numar_brut: str
    data_emitere: date
    data_scadenta: date | None
    tip_document: str | None
    moneda: str
    nr_contract: str | None
    nr_comanda: str | None
    perioada_start: date | None
    perioada_sfarsit: date | None
    versiune_cius: str | None
    total_fara_tva: Decimal
    total_tva: Decimal
    total_document: Decimal
    total_de_plata: Decimal | None
    parts: list[ParsedParty] = field(default_factory=list)
    lines: list[ParsedLine] = field(default_factory=list)
    tax_summaries: list[ParsedTaxSummary] = field(default_factory=list)
    references: list[ParsedReference] = field(default_factory=list)
    attachments: list[ParsedAttachment] = field(default_factory=list)
    xpath_map: dict[str, str] = field(default_factory=dict)


def _safe_parse(xml_bytes: bytes) -> etree._Element:
    if b"<!DOCTYPE" in xml_bytes[:4096]:
        raise InvoiceXmlError("DOCTYPE extern interzis (protecție XXE)")
    try:
        return etree.fromstring(xml_bytes, parser=_PARSER)
    except etree.XMLSyntaxError as exc:
        raise InvoiceXmlError(f"XML neinterpretabil: {exc}") from exc


def classify_xml_bytes(xml_bytes: bytes) -> str:
    """Distinge factura / semnătura ANAF / alt XML, pentru membrii unui ZIP."""
    try:
        root = _safe_parse(xml_bytes)
    except InvoiceXmlError:
        return "atasament"
    local = etree.QName(root).localname
    if local in _INVOICE_ROOT_LOCALNAMES:
        return "xml_factura"
    if local in _SIGNATURE_ROOT_LOCALNAMES:
        return "xml_semnatura"
    return "atasament"


def _el(node: etree._Element | None, xpath: str) -> etree._Element | None:
    if node is None:
        return None
    found = node.xpath(xpath, namespaces=NS)
    return found[0] if found else None


def _first_el(node: etree._Element | None, *xpaths: str) -> etree._Element | None:
    """Ca `_el`, dar încearcă mai multe XPath-uri în ordine și le întoarce pe
    primul găsit. NU folosim `_el(...) or _el(...)` — un `_Element` lxml e
    falsy când nu are copii (`__bool__` == `len(elem) > 0`), nu când e None,
    ceea ce ar sări peste un element frunză găsit corect (ex. CompanyID)."""
    for xp in xpaths:
        el = _el(node, xp)
        if el is not None:
            return el
    return None

def _els(node: etree._Element, xpath: str) -> list[etree._Element]:
    return node.xpath(xpath, namespaces=NS)


def _text(el: etree._Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t or None


def _decimal(el: etree._Element | None) -> Decimal | None:
    t = _text(el)
    if t is None:
        return None
    try:
        return Decimal(t)
    except InvalidOperation as exc:
        raise InvoiceXmlError(f"valoare numerică invalidă: {t!r}", xpath=_xpath_of(el)) from exc


def _date(el: etree._Element | None) -> date | None:
    t = _text(el)
    if t is None:
        return None
    try:
        return date.fromisoformat(t[:10])
    except ValueError as exc:
        raise InvoiceXmlError(f"dată invalidă: {t!r}", xpath=_xpath_of(el)) from exc


def _xpath_of(el: etree._Element | None) -> str | None:
    if el is None:
        return None
    return el.getroottree().getpath(el)


def _record(xpath_map: dict[str, str], key: str, el: etree._Element | None) -> None:
    xp = _xpath_of(el)
    if xp:
        xpath_map[key] = xp


def _format_address(address_el: etree._Element | None) -> str | None:
    if address_el is None:
        return None
    pieces = [
        _text(_el(address_el, "cbc:StreetName")),
        _text(_el(address_el, "cbc:AdditionalStreetName")),
        _text(_el(address_el, "cbc:CityName")),
        _text(_el(address_el, "cbc:PostalZone")),
        _text(_el(address_el, "cbc:CountrySubentity")),
    ]
    joined = ", ".join(p for p in pieces if p)
    return joined or None


def _parse_party(party_el: etree._Element, rol: str) -> ParsedParty:
    name_el = _first_el(
        party_el, "cac:PartyLegalEntity/cbc:RegistrationName", "cac:PartyName/cbc:Name"
    )
    cif_el = _first_el(
        party_el, "cac:PartyLegalEntity/cbc:CompanyID", "cac:PartyTaxScheme/cbc:CompanyID"
    )
    address_el = _el(party_el, "cac:PostalAddress")
    country_el = _el(address_el, "cac:Country/cbc:IdentificationCode")
    vat_el = _el(party_el, "cac:PartyTaxScheme/cbc:CompanyID")
    contact_name_el = _el(party_el, "cac:Contact/cbc:Name")
    contact_phone_el = _el(party_el, "cac:Contact/cbc:Telephone")
    contact_email_el = _el(party_el, "cac:Contact/cbc:ElectronicMail")

    contact = ", ".join(
        filter(None, [_text(contact_name_el), _text(contact_phone_el), _text(contact_email_el)])
    ) or None

    return ParsedParty(
        rol=rol,
        denumire=_text(name_el),
        cif_brut=_text(cif_el),
        adresa=_format_address(address_el),
        tara=_text(country_el),
        cod_tva=_text(vat_el),
        contact=contact,
    )


def _parse_line(line_el: etree._Element, qty_tag: str) -> ParsedLine:
    id_el = _el(line_el, "cbc:ID")
    qty_el = _el(line_el, qty_tag)
    item_el = _el(line_el, "cac:Item")
    price_el = _el(line_el, "cac:Price/cbc:PriceAmount")
    amount_el = _el(line_el, "cbc:LineExtensionAmount")
    order_line_el = _el(line_el, "cac:OrderLineReference/cbc:LineID")

    descriere_el = _first_el(item_el, "cbc:Description", "cbc:Name")
    seller_code_el = _el(item_el, "cac:SellersItemIdentification/cbc:ID")
    buyer_code_el = _el(item_el, "cac:BuyersItemIdentification/cbc:ID")
    tax_cat_el = _el(item_el, "cac:ClassifiedTaxCategory")
    cota_el = _el(tax_cat_el, "cbc:Percent")
    categorie_el = _el(tax_cat_el, "cbc:ID")

    reducere: Decimal | None = None
    for charge_el in _els(line_el, "cac:AllowanceCharge"):
        indicator = _text(_el(charge_el, "cbc:ChargeIndicator"))
        if indicator == "false":
            amt = _decimal(_el(charge_el, "cbc:Amount")) or Decimal("0")
            reducere = (reducere or Decimal("0")) + amt

    nr_crt_text = _text(id_el)
    nr_crt = int(nr_crt_text) if nr_crt_text and nr_crt_text.isdigit() else None

    return ParsedLine(
        nr_crt=nr_crt,
        cod_articol_furnizor=_text(seller_code_el),
        cod_articol_client=_text(buyer_code_el),
        descriere=_text(descriere_el),
        cantitate=_decimal(qty_el),
        um=qty_el.get("unitCode") if qty_el is not None else None,
        pret_unitar=_decimal(price_el),
        valoare_fara_tva=_decimal(amount_el),
        cota_tva=_decimal(cota_el),
        categorie_tva=_text(categorie_el),
        reducere=reducere,
        nr_comanda=_text(order_line_el),
        xpath=_xpath_of(line_el),
    )


def _parse_tax_summaries(root: etree._Element) -> list[ParsedTaxSummary]:
    summaries = []
    for sub in _els(root, "cac:TaxTotal/cac:TaxSubtotal"):
        baza = _decimal(_el(sub, "cbc:TaxableAmount"))
        tva = _decimal(_el(sub, "cbc:TaxAmount"))
        if baza is None or tva is None:
            continue
        summaries.append(
            ParsedTaxSummary(
                cota=_decimal(_el(sub, "cac:TaxCategory/cbc:Percent")),
                categorie=_text(_el(sub, "cac:TaxCategory/cbc:ID")),
                baza=baza,
                tva=tva,
            )
        )
    return summaries


def _parse_references(root: etree._Element) -> list[ParsedReference]:
    refs: list[ParsedReference] = []
    for ref_id_el in _els(root, "cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID"):
        val = _text(ref_id_el)
        if val:
            refs.append(ParsedReference(tip="storno", valoare=val, xpath=_xpath_of(ref_id_el)))
    for xpath, tip in (
        ("cac:OrderReference/cbc:ID", "comanda"),
        ("cac:ContractDocumentReference/cbc:ID", "contract"),
        ("cac:DespatchDocumentReference/cbc:ID", "aviz"),
        ("cac:ReceiptDocumentReference/cbc:ID", "receptie"),
        ("cac:OriginatorDocumentReference/cbc:ID", "comanda_initiator"),
    ):
        el = _el(root, xpath)
        val = _text(el)
        if val:
            refs.append(ParsedReference(tip=tip, valoare=val, xpath=_xpath_of(el)))
    return refs


def _parse_attachments(root: etree._Element) -> list[ParsedAttachment]:
    attachments = []
    for doc_ref_el in _els(root, "cac:AdditionalDocumentReference"):
        id_el = _el(doc_ref_el, "cbc:ID")
        desc_el = _el(doc_ref_el, "cbc:DocumentDescription")
        bin_el = _el(doc_ref_el, "cac:Attachment/cbc:EmbeddedDocumentBinaryObject")
        if bin_el is None:
            continue
        attachments.append(
            ParsedAttachment(
                nume=bin_el.get("filename") or _text(id_el),
                mime=bin_el.get("mimeCode"),
                descriere=_text(desc_el),
                continut_base64=_text(bin_el),
            )
        )
    return attachments


def parse_invoice_xml(xml_bytes: bytes) -> ParsedInvoice:
    root = _safe_parse(xml_bytes)
    local = etree.QName(root).localname
    if local == "Invoice":
        line_tag, qty_tag = "cac:InvoiceLine", "cbc:InvoicedQuantity"
    elif local == "CreditNote":
        line_tag, qty_tag = "cac:CreditNoteLine", "cbc:CreditedQuantity"
    else:
        raise InvoiceXmlError(
            f"rădăcină XML neașteptată: {local!r} (aștept Invoice sau CreditNote)"
        )

    xpath_map: dict[str, str] = {}

    id_el = _el(root, "cbc:ID")
    _record(xpath_map, "numar_brut", id_el)
    numar_brut = _text(id_el)
    if not numar_brut:
        raise InvoiceXmlError("lipsește cbc:ID (numărul facturii)", xpath="/*/cbc:ID")

    issue_el = _el(root, "cbc:IssueDate")
    _record(xpath_map, "data_emitere", issue_el)
    data_emitere = _date(issue_el)
    if data_emitere is None:
        raise InvoiceXmlError("lipsește cbc:IssueDate", xpath="/*/cbc:IssueDate")

    due_el = _el(root, "cbc:DueDate")
    _record(xpath_map, "data_scadenta", due_el)

    type_code_el = _first_el(root, "cbc:InvoiceTypeCode", "cbc:CreditNoteTypeCode")
    _record(xpath_map, "tip_document", type_code_el)

    currency_el = _el(root, "cbc:DocumentCurrencyCode")
    _record(xpath_map, "moneda", currency_el)

    customization_el = _el(root, "cbc:CustomizationID")
    profile_el = _el(root, "cbc:ProfileID")
    _record(xpath_map, "versiune_cius", customization_el)
    versiune_parts = [t for t in (_text(customization_el), _text(profile_el)) if t]

    contract_el = _el(root, "cac:ContractDocumentReference/cbc:ID")
    _record(xpath_map, "nr_contract", contract_el)

    order_el = _el(root, "cac:OrderReference/cbc:ID")
    _record(xpath_map, "nr_comanda", order_el)

    period_start_el = _el(root, "cac:InvoicePeriod/cbc:StartDate")
    period_end_el = _el(root, "cac:InvoicePeriod/cbc:EndDate")
    _record(xpath_map, "perioada_start", period_start_el)
    _record(xpath_map, "perioada_sfarsit", period_end_el)

    totals_el = _el(root, "cac:LegalMonetaryTotal")
    if totals_el is None:
        raise InvoiceXmlError(
            "lipsește cac:LegalMonetaryTotal", xpath="/*/cac:LegalMonetaryTotal"
        )
    tax_excl_el = _el(totals_el, "cbc:TaxExclusiveAmount")
    tax_incl_el = _el(totals_el, "cbc:TaxInclusiveAmount")
    payable_el = _el(totals_el, "cbc:PayableAmount")
    _record(xpath_map, "total_fara_tva", tax_excl_el)
    _record(xpath_map, "total_document", tax_incl_el)
    _record(xpath_map, "total_de_plata", payable_el)
    total_fara_tva = _decimal(tax_excl_el)
    total_document = _decimal(tax_incl_el)
    if total_fara_tva is None or total_document is None:
        raise InvoiceXmlError("totaluri document lipsă în cac:LegalMonetaryTotal")

    tax_total_amount_el = _el(root, "cac:TaxTotal/cbc:TaxAmount")
    _record(xpath_map, "total_tva", tax_total_amount_el)
    total_tva = _decimal(tax_total_amount_el)
    if total_tva is None:
        raise InvoiceXmlError("lipsește cac:TaxTotal/cbc:TaxAmount")

    parts: list[ParsedParty] = []
    for rol, xpath in (
        ("furnizor", "cac:AccountingSupplierParty/cac:Party"),
        ("client", "cac:AccountingCustomerParty/cac:Party"),
        ("reprezentant_fiscal", "cac:TaxRepresentativeParty"),
    ):
        party_el = _el(root, xpath)
        if party_el is not None:
            parts.append(_parse_party(party_el, rol))

    iban_el = _el(root, "cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID")
    iban = _text(iban_el)
    if iban:
        for p in parts:
            if p.rol == "furnizor":
                p.cont_bancar = iban

    lines = [_parse_line(line_el, qty_tag) for line_el in _els(root, line_tag)]

    return ParsedInvoice(
        document_type=local,
        numar_brut=numar_brut,
        data_emitere=data_emitere,
        data_scadenta=_date(due_el),
        tip_document=_text(type_code_el),
        moneda=_text(currency_el) or "RON",
        nr_contract=_text(contract_el),
        nr_comanda=_text(order_el),
        perioada_start=_date(period_start_el),
        perioada_sfarsit=_date(period_end_el),
        versiune_cius=" | ".join(versiune_parts) or None,
        total_fara_tva=total_fara_tva,
        total_tva=total_tva,
        total_document=total_document,
        total_de_plata=_decimal(payable_el),
        parts=parts,
        lines=lines,
        tax_summaries=_parse_tax_summaries(root),
        references=_parse_references(root),
        attachments=_parse_attachments(root),
        xpath_map=xpath_map,
    )
