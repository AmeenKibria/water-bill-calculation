from sheets_storage import normalize_period_record, normalize_trueup_record


def test_normalize_period_record():
    record = {
        "Period start": "01/01/2026",
        "Period end": "31/03/2026",
        "AS-1 usage": "27,366m3",
        "AS-2 usage": "14,100m3",
        "AS-1 adjusted": "27,366m3",
        "AS-2 adjusted": "14,100m3",
        "Basic fees": "84,03€",
        "Usage fees": "222,13€",
        "AS-1 total": "188,81€",
        "AS-2 total": "117,35€",
        "Mismatch policy": "ignore",
        "Mismatch (m3)": "0,000m3",
        "Mismatch (%)": "0%",
        "Saved at": "02/02/2026 10:00",
    }
    data = normalize_period_record(record)
    assert data["s1_use"] == 27.366
    assert data["basic_fees"] == 84.03


def test_normalize_trueup_record():
    record = {
        "Period start": "01/07/2026",
        "Period end": "30/09/2026",
        "AS-1 usage": "10,000m3",
        "AS-2 usage": "20,000m3",
        "True-up amount": "60,00€",
        "AS-1 share": "20,00€",
        "AS-2 share": "40,00€",
        "Saved at": "01/10/2026 08:00",
    }
    data = normalize_trueup_record(record)
    assert data["trueup_amount"] == 60.0
    assert data["share_2"] == 40.0
