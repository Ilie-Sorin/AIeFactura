from app.services.normalize_cif import is_valid_cif, normalize_cif


def test_normalize_cif_strips_prefix_spaces_zeros():
    assert normalize_cif("RO 018547290") == "18547290"
    assert normalize_cif("ro18547290") == "18547290"
    assert normalize_cif("0018547290") == "18547290"
    assert normalize_cif(None) is None
    assert normalize_cif("") is None


def test_valid_cif_examples_pass_control_digit():
    # calculate cu algoritmul ANAF: cheie [7,5,3,2,1,7,5,3,2], mod 11
    assert is_valid_cif("185472903") is True
    assert is_valid_cif("RO185472903") is True
    assert is_valid_cif("143981006") is True


def test_invalid_control_digit_fails():
    assert is_valid_cif("185472901") is False
    assert is_valid_cif("143981001") is False


def test_non_numeric_or_malformed_cif_is_invalid():
    assert is_valid_cif("ABC123") is False
    assert is_valid_cif("1") is False
    assert is_valid_cif(None) is False
