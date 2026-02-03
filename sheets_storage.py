from utils import parse_number


PERIODS_HEADERS = [
    "Period start",
    "Period end",
    "Invoice number",
    "Estimated water",
    "Due date",
    "Reading start",
    "Reading end",
    "Main usage",
    "AS-1 usage",
    "AS-2 usage",
    "Basic fees",
    "Usage fees",
    "AS-1 total",
    "AS-2 total",
    "Mismatch (m3)",
    "Mismatch (%)",
    "Saved at",
]

TRUEUPS_HEADERS = [
    "Period start",
    "Period end",
    "AS-1 usage",
    "AS-2 usage",
    "True-up amount",
    "AS-1 share",
    "AS-2 share",
    "Saved at",
]


def normalize_period_record(record: dict) -> dict:
    mismatch_pct = parse_number(record.get("Mismatch (%)"))
    if mismatch_pct is not None and mismatch_pct > 1:
        mismatch_pct = mismatch_pct / 100
    return {
        "period_start": record.get("Period start"),
        "period_end": record.get("Period end"),
        "invoice_number": record.get("Invoice number"),
        "estimated_water": parse_number(record.get("Estimated water")),
        "due_date": record.get("Due date"),
        "reading_start": record.get("Reading start"),
        "reading_end": record.get("Reading end"),
        "main_use": parse_number(record.get("Main usage")),
        "s1_use": parse_number(record.get("AS-1 usage")) or 0.0,
        "s2_use": parse_number(record.get("AS-2 usage")) or 0.0,
        "basic_fees": parse_number(record.get("Basic fees")) or 0.0,
        "usage_fees": parse_number(record.get("Usage fees")) or 0.0,
        "total_1": parse_number(record.get("AS-1 total")) or 0.0,
        "total_2": parse_number(record.get("AS-2 total")) or 0.0,
        "mismatch_m3": parse_number(record.get("Mismatch (m3)")),
        "mismatch_pct": mismatch_pct,
        "saved_at": record.get("Saved at"),
    }


def normalize_trueup_record(record: dict) -> dict:
    return {
        "period_start": record.get("Period start"),
        "period_end": record.get("Period end"),
        "s1_use": parse_number(record.get("AS-1 usage")) or 0.0,
        "s2_use": parse_number(record.get("AS-2 usage")) or 0.0,
        "trueup_amount": parse_number(record.get("True-up amount")) or 0.0,
        "share_1": parse_number(record.get("AS-1 share")) or 0.0,
        "share_2": parse_number(record.get("AS-2 share")) or 0.0,
        "saved_at": record.get("Saved at"),
    }
