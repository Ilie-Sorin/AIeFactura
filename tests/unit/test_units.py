from app.services.units import format_um


def test_known_codes_translated():
    assert format_um("H87") == "buc"
    assert format_um("h87") == "buc"
    assert format_um("KGM") == "kg"
    assert format_um("HUR") == "oră"


def test_unknown_code_falls_back_to_raw():
    assert format_um("XYZ") == "XYZ"


def test_empty_or_none_returns_none():
    assert format_um(None) is None
    assert format_um("") is None
