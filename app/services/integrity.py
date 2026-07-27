"""Controale de integritate la nivel de document (cap. 8, subset rulat la
ingestie): tripla verificare sumă linii / total / TVA pe cote, CIF invalid."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.normalize_cif import is_valid_cif
from app.services.xml_parser import ParsedInvoice

TOLERANTA_IMPLICITA = Decimal("0.02")


@dataclass
class IntegrityIssue:
    cod: str
    mesaj: str
    detalii: dict


def check_line_sum_vs_total(
    parsed: ParsedInvoice, toleranta: Decimal = TOLERANTA_IMPLICITA
) -> IntegrityIssue | None:
    suma_linii = sum((l.valoare_fara_tva or Decimal("0") for l in parsed.lines), Decimal("0"))
    suma_tva_pe_cote = sum((t.tva for t in parsed.tax_summaries), Decimal("0"))

    diff_linii = abs(suma_linii - parsed.total_fara_tva)
    diff_total = abs((parsed.total_fara_tva + parsed.total_tva) - parsed.total_document)
    diff_tva_cote = abs(suma_tva_pe_cote - parsed.total_tva)

    if diff_linii <= toleranta and diff_total <= toleranta and diff_tva_cote <= toleranta:
        return None

    return IntegrityIssue(
        cod="suma_linii_vs_total",
        mesaj="Suma liniilor, totalul documentului și suma pe cote TVA nu coincid.",
        detalii={
            "suma_linii_fara_tva": str(suma_linii),
            "total_fara_tva_document": str(parsed.total_fara_tva),
            "diferenta_linii": str(diff_linii),
            "suma_tva_pe_cote": str(suma_tva_pe_cote),
            "total_tva_document": str(parsed.total_tva),
            "diferenta_tva_cote": str(diff_tva_cote),
            "diferenta_total": str(diff_total),
        },
    )


def check_cif_valid(cif_normalizat: str | None, *, rol: str = "furnizor") -> IntegrityIssue | None:
    """CIF-uri străine sau needeterminabile nu se resping — algoritmul cifrei
    de control e specific CUI-ului românesc."""
    if not cif_normalizat or not cif_normalizat.isdigit():
        return None
    if is_valid_cif(cif_normalizat):
        return None
    return IntegrityIssue(
        cod="cif_invalid",
        mesaj=f"CIF {rol} nu trece cifra de control ({cif_normalizat}).",
        detalii={"cif": cif_normalizat, "rol": rol},
    )
