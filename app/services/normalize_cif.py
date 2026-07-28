"""Normalizare CIF/CUI și validarea cifrei de control (cap. 5).

Algoritmul cifrei de control CUI (folosit și de ANAF): cele maxim 9 cifre ale
părții numerice (fără cifra de control), completate la stânga cu zerouri până
la 9 poziții, se înmulțesc cu cheia [7,5,3,2,1,7,5,3,2], se însumează, suma se
înmulțește cu 10 și se reduce modulo 11; dacă restul e 10, cifra de control e 0,
altfel chiar restul.
"""

import re

_CONTROL_KEY = (7, 5, 3, 2, 1, 7, 5, 3, 2)


def normalize_cif(raw: str | None) -> str | None:
    """Elimină prefixul de țară, spațiile și zerourile inițiale."""
    if not raw:
        return None
    s = re.sub(r"\s+", "", raw.strip().upper())
    if s.startswith("RO"):
        s = s[2:]
    s = s.lstrip("0")
    return s or None


def _control_digit(body9: str) -> int:
    total = sum(int(d) * k for d, k in zip(body9, _CONTROL_KEY))
    remainder = (total * 10) % 11
    return 0 if remainder == 10 else remainder


def is_valid_cif(raw: str | None) -> bool:
    """Validează cifra de control CUI. CIF-uri străine/formate atipice (nu doar
    cifre) nu pot fi validate prin acest algoritm și întorc False."""
    normalized = normalize_cif(raw)
    if not normalized or not normalized.isdigit():
        return False
    if not (2 <= len(normalized) <= 10):
        return False
    *body, control = normalized
    body9 = "".join(body).zfill(9)
    if len(body9) > 9:
        return False
    return _control_digit(body9) == int(control)
