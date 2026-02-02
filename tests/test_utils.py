from utils import (
    format_date,
    format_eur,
    format_m3,
    mismatch_status,
    parse_number,
)


def test_format_eur():
    assert format_eur(103.58) == "103,58€"


def test_format_m3():
    assert format_m3(27.366) == "27,366m3"


def test_format_date():
    assert format_date("2026-01-01") == "01/01/2026"
    assert format_date("01/02/2026") == "01/02/2026"


def test_parse_number():
    assert parse_number("222,13€") == 222.13
    assert parse_number("27,366m3") == 27.366
    assert parse_number("5%") == 5.0


def test_mismatch_status():
    assert mismatch_status(0.5, 0.01) == "ok"
    assert mismatch_status(2.0, 0.06) == "warning"
    assert mismatch_status(4.0, 0.12) == "investigate"
