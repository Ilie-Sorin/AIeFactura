from app.services.normalize_number import NumberFormattingConfig, normalize_invoice_number


def test_variants_of_same_conceptual_number_normalize_close():
    cases = ["FCT 0001234", "FCT1234", "0001234", "1234"]
    forms = [normalize_invoice_number(c) for c in cases]

    # Toate patru extrag aceeasi parte numerica (fara zerouri de umplere).
    assert all(f.numar_numeric == 1234 for f in forms)

    # "0001234" si "1234" (fara serie) au aceeasi forma normalizata.
    assert forms[2].normalizata == forms[3].normalizata == "1234"

    # "FCT 0001234" si "FCT1234" au aceeasi forma normalizata (separatori eliminati).
    assert forms[0].normalizata == forms[1].normalizata == "FCT1234"


def test_series_with_trailing_year_extracts_series_and_number_separately():
    forms = normalize_invoice_number("FCT-1234/2026")
    assert forms.serie == "FCT"
    assert forms.numar_numeric == 1234
    # forma bruta ramane fidela sursei
    assert forms.bruta == "FCT-1234/2026"


def test_bruta_form_preserves_original_up_to_trim():
    forms = normalize_invoice_number("  FCT 0001234  ")
    assert forms.bruta == "FCT 0001234"


def test_custom_config_disables_zero_stripping():
    config = NumberFormattingConfig(strip_zerouri_umplere=False)
    forms = normalize_invoice_number("FCT 0001234", config)
    assert forms.normalizata == "FCT0001234"
