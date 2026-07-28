"""Vizualizarea documentului cu foaia de stil oficială ANAF (cap. 3).

Stack-ul impus (lxml) poate transforma XML-ul facturii cu XSLT-ul oficial
ANAF în HTML — exact ce face orice vizualizator RO-CIUS. Un fișier .pdf
binar propriu-zis ar cere un motor suplimentar de randare HTML→PDF
(weasyprint/wkhtmltopdf), în afara stack-ului impus prin specificație și cu
dependențe native fragile pe Windows. În loc, vizualizarea se servește ca
HTML stilizat identic cu al ANAF; PDF-ul se obține din browser
(Print → Salvează ca PDF), la fel ca la orice alt viewer bazat pe același
stylesheet.

Stylesheet-ul oficial NU e distribuit în acest depozit (fișier extern, de la
ANAF) — vezi `app/resources/README.md`. Dacă lipsește la calea configurată,
`render_invoice_html` ridică `StylesheetMissingError` cu un mesaj clar, în loc
să eșueze silențios sau să folosească un șablon propriu.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from lxml import etree

from app.config import get_settings

_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)


class StylesheetMissingError(Exception):
    pass


class DocumentRenderError(Exception):
    pass


@lru_cache(maxsize=1)
def _load_stylesheet(cale: str) -> etree.XSLT:
    path = Path(cale)
    if not path.exists():
        raise StylesheetMissingError(
            f"Stylesheet-ul ANAF nu a fost găsit la '{cale}'. Adaugă fișierul oficial "
            "(obținut separat de la ANAF — vezi app/resources/README.md) la această "
            "cale sau configurează ANAF_STYLESHEET_PATH în .env."
        )
    xslt_doc = etree.parse(str(path), parser=_XML_PARSER)
    return etree.XSLT(xslt_doc)


def stylesheet_available() -> bool:
    return Path(get_settings().anaf_stylesheet_path).exists()


def render_invoice_html(xml_bytes: bytes) -> str:
    transform = _load_stylesheet(get_settings().anaf_stylesheet_path)

    try:
        xml_doc = etree.fromstring(xml_bytes, parser=_XML_PARSER)
    except etree.XMLSyntaxError as exc:
        raise DocumentRenderError(f"XML neinterpretabil: {exc}") from exc

    try:
        rezultat = transform(xml_doc)
    except etree.XSLTApplyError as exc:
        raise DocumentRenderError(f"transformarea XSLT a eșuat: {exc}") from exc

    return str(rezultat)
