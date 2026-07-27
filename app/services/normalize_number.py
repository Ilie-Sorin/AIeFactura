"""Normalizarea numărului de factură — cap. 5.

Se păstrează trei forme, potrivirea încercând în ordine descrescătoare de
certitudine: brută (exact cum apare), normalizată (majuscule, fără separatori,
fără zerouri de umplere) și componente (serie + parte numerică, comparate
separat). Regula e configurabilă per furnizor (`numbering_rule`), cu o regulă
implicită globală când nu există override.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.numbering import NumberingRule

DEFAULT_SEPARATORS = " \t-_/.\\"
DEFAULT_COMPONENT_REGEX = r"^(?P<serie>[A-Z]*)[^0-9A-Z]*(?P<numar>\d+)"


@dataclass(frozen=True)
class NumberFormattingConfig:
    separatori: str = DEFAULT_SEPARATORS
    strip_zerouri_umplere: bool = True
    regex_componente: str = DEFAULT_COMPONENT_REGEX

    @classmethod
    def from_json(cls, data: dict | None) -> "NumberFormattingConfig":
        data = data or {}
        return cls(
            separatori=data.get("separatori", DEFAULT_SEPARATORS),
            strip_zerouri_umplere=data.get("strip_zerouri_umplere", True),
            regex_componente=data.get("regex_componente", DEFAULT_COMPONENT_REGEX),
        )


@dataclass(frozen=True)
class NumberForms:
    bruta: str
    normalizata: str
    serie: str | None
    numar_numeric: int | None


def _strip_leading_zeros_in_digit_runs(s: str) -> str:
    return re.sub(r"\d+", lambda m: str(int(m.group())), s)


def normalize_invoice_number(
    raw: str, config: NumberFormattingConfig | None = None
) -> NumberForms:
    config = config or NumberFormattingConfig()
    bruta = raw.strip()

    fara_separatori = "".join(c for c in bruta if c not in config.separatori).upper()
    normalizata = (
        _strip_leading_zeros_in_digit_runs(fara_separatori)
        if config.strip_zerouri_umplere
        else fara_separatori
    )

    serie: str | None = None
    numar_numeric: int | None = None
    match = re.match(config.regex_componente, bruta.upper())
    if match:
        groups = match.groupdict()
        serie = groups.get("serie") or None
        numar_text = groups.get("numar")
        if numar_text:
            numar_numeric = int(numar_text)

    return NumberForms(
        bruta=bruta,
        normalizata=normalizata or bruta.upper(),
        serie=serie,
        numar_numeric=numar_numeric,
    )


def resolve_numbering_config(session: Session, cif_emitent: str | None) -> NumberFormattingConfig:
    """Regula per furnizor (dacă există și e activă), altfel regula implicită
    globală (`cif_emitent IS NULL`), altfel valorile implicite din bibliotecă."""
    rule = None
    if cif_emitent:
        rule = session.scalar(
            select(NumberingRule).where(
                NumberingRule.cif_emitent == cif_emitent, NumberingRule.activa.is_(True)
            )
        )
    if rule is None:
        rule = session.scalar(
            select(NumberingRule).where(
                NumberingRule.cif_emitent.is_(None), NumberingRule.activa.is_(True)
            )
        )
    if rule is None:
        return NumberFormattingConfig()
    return NumberFormattingConfig.from_json(rule.configuratie)
