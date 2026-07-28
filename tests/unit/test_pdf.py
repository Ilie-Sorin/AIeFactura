from pathlib import Path

import pytest

from app.config import Settings
from app.services import pdf as pdf_module

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

SYNTHETIC_XSL = b"""<?xml version="1.0"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
  xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
<xsl:output method="html"/>
<xsl:template match="/">
  <html><body><h1><xsl:value-of select="//cbc:ID"/></h1></body></html>
</xsl:template>
</xsl:stylesheet>"""


def _use_stylesheet(monkeypatch, path) -> None:
    monkeypatch.setattr(pdf_module, "get_settings", lambda: Settings(anaf_stylesheet_path=str(path)))
    pdf_module._load_stylesheet.cache_clear()


def test_missing_stylesheet_raises_clear_error(tmp_path, monkeypatch):
    _use_stylesheet(monkeypatch, tmp_path / "nu-exista.xsl")

    with pytest.raises(pdf_module.StylesheetMissingError):
        pdf_module.render_invoice_html(b"<Invoice/>")


def test_stylesheet_available_reflects_file_presence(tmp_path, monkeypatch):
    cale = tmp_path / "nu-exista.xsl"
    _use_stylesheet(monkeypatch, cale)
    assert pdf_module.stylesheet_available() is False

    cale.write_bytes(SYNTHETIC_XSL)
    assert pdf_module.stylesheet_available() is True


def test_render_invoice_html_transforms_with_stylesheet(tmp_path, monkeypatch):
    xsl_path = tmp_path / "test.xsl"
    xsl_path.write_bytes(SYNTHETIC_XSL)
    _use_stylesheet(monkeypatch, xsl_path)

    xml_bytes = (FIXTURES / "factura_normala.xml").read_bytes()
    html = pdf_module.render_invoice_html(xml_bytes)
    assert "0001234" in html


def test_render_invoice_html_invalid_xml_raises(tmp_path, monkeypatch):
    xsl_path = tmp_path / "test.xsl"
    xsl_path.write_bytes(SYNTHETIC_XSL)
    _use_stylesheet(monkeypatch, xsl_path)

    with pytest.raises(pdf_module.DocumentRenderError):
        pdf_module.render_invoice_html(b"<not-well-formed")
