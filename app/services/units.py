"""Traducerea codurilor de unitate de măsură UN/CEFACT Recomandarea 20 în
etichetă înțeleasă de utilizator — un cod precum „H87" nu are nicio
semnificație vizibilă. Nu e o listă exhaustivă (recomandarea are sute de
coduri) — doar cele mai des întâlnite în facturi RO-CIUS; un cod
nemapat se afișează ca atare, nu ca eroare."""

UNITATI_MASURA = {
    "C62": "buc",
    "H87": "buc",
    "EA": "buc",
    "NAR": "buc",
    "KGM": "kg",
    "GRM": "g",
    "TNE": "tonă",
    "LTR": "l",
    "MLT": "ml",
    "MTR": "m",
    "CMT": "cm",
    "MMT": "mm",
    "MTK": "m²",
    "MTQ": "m³",
    "HUR": "oră",
    "MIN": "min",
    "DAY": "zi",
    "WEE": "săptămână",
    "MON": "lună",
    "ANN": "an",
    "PR": "pereche",
    "SET": "set",
    "PA": "pachet",
    "PK": "pachet",
    "XPP": "pachet",
    "BX": "cutie",
    "CT": "cutie",
    "XBX": "cutie",
    "PF": "palet",
    "KWH": "kWh",
    "KWT": "kW",
}


def format_um(cod: str | None) -> str | None:
    if not cod:
        return None
    return UNITATI_MASURA.get(cod.upper(), cod)
