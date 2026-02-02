from __future__ import annotations

import io
from datetime import datetime


def format_number(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


def format_eur(value: float) -> str:
    return f"{format_number(value, 2)}€"


def format_m3(value: float) -> str:
    sign = "-" if value < 0 else ""
    raw = format_number(abs(value), 3)
    integer_part, fractional = raw.split(",")
    return f"{sign}{integer_part},{fractional}m3"


def parse_number(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = (
        text.replace("EUR", "")
        .replace("€", "")
        .replace("m3", "")
        .replace("m³", "")
        .replace("%", "")
        .replace(" ", "")
    )
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def validate_decimal_places(value: str | None, max_decimals: int) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    text = (
        text.replace("EUR", "")
        .replace("€", "")
        .replace("m3", "")
        .replace("m³", "")
        .replace("%", "")
        .replace(" ", "")
    )
    if text.count(",") > 1:
        return False
    if "," in text:
        integer_part, fractional = text.split(",", 1)
        if not integer_part.isdigit() and integer_part != "":
            return False
        if not fractional.isdigit():
            return False
        return len(fractional) <= max_decimals
    return text.isdigit()


def format_date(value: str | None) -> str | None:
    if not value:
        return value
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d/%m/%Y %H:%M"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.strftime("%d/%m/%Y")
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.strftime("%d/%m/%Y")
    except ValueError:
        return value


def wrap_lines(lines: list[str], max_len: int = 90) -> list[str]:
    wrapped = []
    for line in lines:
        if len(line) <= max_len:
            wrapped.append(line)
            continue
        current = line
        while len(current) > max_len:
            cut = current.rfind(" ", 0, max_len)
            if cut == -1:
                cut = max_len
            wrapped.append(current[:cut].rstrip())
            current = current[cut:].lstrip()
        if current:
            wrapped.append(current)
    return wrapped


def mismatch_status(mismatch_m3: float, mismatch_pct: float | None) -> str:
    abs_m3 = abs(mismatch_m3)
    abs_pct = abs(mismatch_pct) if mismatch_pct is not None else None

    if abs_m3 <= 1.0 or (abs_pct is not None and abs_pct <= 0.05):
        return "ok"
    if abs_m3 <= 3.0 or (abs_pct is not None and abs_pct <= 0.10):
        return "warning"
    return "investigate"


def compute_split(
    s1_use: float,
    s2_use: float,
    basic_fees: float,
    usage_fees: float,
    mismatch_policy: str = "ignore",
    main_use: float | None = None,
) -> dict:
    if s1_use < 0 or s2_use < 0:
        raise ValueError("Sub-meter usage cannot be negative.")
    sub_sum = s1_use + s2_use
    if sub_sum <= 0:
        raise ValueError("Total sub-meter usage must be greater than 0.")

    if mismatch_policy in {"half", "proportional"}:
        if main_use is None or main_use <= 0:
            raise ValueError("Main meter usage must be greater than 0.")

    mismatch_m3 = None
    mismatch_pct = None
    if main_use is not None and main_use > 0:
        mismatch_m3 = main_use - sub_sum
        mismatch_pct = mismatch_m3 / main_use

    adj_s1_use = s1_use
    adj_s2_use = s2_use
    if mismatch_policy == "half":
        diff = main_use - sub_sum
        adj_s1_use = s1_use + diff / 2
        adj_s2_use = s2_use + diff / 2
    elif mismatch_policy == "proportional":
        diff = main_use - sub_sum
        adj_s1_use = s1_use + diff * (s1_use / sub_sum)
        adj_s2_use = s2_use + diff * (s2_use / sub_sum)

    if adj_s1_use < 0 or adj_s2_use < 0:
        raise ValueError("Adjusted usage became negative.")

    adj_total = adj_s1_use + adj_s2_use
    usage_share_1 = usage_fees * (adj_s1_use / adj_total)
    usage_share_2 = usage_fees * (adj_s2_use / adj_total)
    basic_share = basic_fees / 2

    return {
        "adj_s1_use": adj_s1_use,
        "adj_s2_use": adj_s2_use,
        "usage_share_1": usage_share_1,
        "usage_share_2": usage_share_2,
        "basic_share": basic_share,
        "total_1": basic_share + usage_share_1,
        "total_2": basic_share + usage_share_2,
        "mismatch_m3": mismatch_m3,
        "mismatch_pct": mismatch_pct,
    }


def compute_trueup(s1_use: float, s2_use: float, trueup_amount: float) -> dict:
    if s1_use < 0 or s2_use < 0:
        raise ValueError("Sub-meter usage cannot be negative.")
    total_use = s1_use + s2_use
    if total_use <= 0:
        raise ValueError("Total usage must be greater than 0.")

    share_1 = trueup_amount * (s1_use / total_use)
    share_2 = trueup_amount * (s2_use / total_use)
    return {
        "share_1": share_1,
        "share_2": share_2,
        "total_use": total_use,
    }


def build_simple_pdf(lines: list[str]) -> bytes:
    def pdf_escape(text: str) -> str:
        return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    left_margin = 54
    leading = 14
    start_y = 792 - 72

    content_lines = []
    content_lines.append("BT")
    content_lines.append("/F1 11 Tf")
    content_lines.append(f"{left_margin} {start_y} Td")

    for i, line in enumerate(lines):
        if i == 0:
            content_lines.append("/F1 14 Tf")
            content_lines.append(f"({pdf_escape(line)}) Tj")
            content_lines.append("/F1 11 Tf")
            content_lines.append(f"0 -{leading} Td")
            continue
        safe_line = line.replace("€", "EUR")
        content_lines.append(f"({pdf_escape(safe_line)}) Tj")
        content_lines.append(f"0 -{leading} Td")

    content_lines.append("ET")
    content_stream = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects = []

    def add_object(obj_bytes: bytes):
        objects.append(obj_bytes)

    add_object(b"<< /Type /Catalog /Pages 2 0 R >>")
    add_object(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add_object(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
    )
    add_object(
        b"<< /Length %d >>\nstream\n%s\nendstream"
        % (len(content_stream), content_stream)
    )
    add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    xref_positions = []
    output = io.BytesIO()
    output.write(b"%PDF-1.4\n")
    for i, obj in enumerate(objects, start=1):
        xref_positions.append(output.tell())
        output.write(f"{i} 0 obj\n".encode("ascii"))
        output.write(obj)
        output.write(b"\nendobj\n")
    xref_start = output.tell()
    output.write(b"xref\n")
    output.write(f"0 {len(objects)+1}\n".encode("ascii"))
    output.write(b"0000000000 65535 f \n")
    for pos in xref_positions:
        output.write(f"{pos:010d} 00000 n \n".encode("ascii"))
    output.write(b"trailer\n")
    output.write(f"<< /Size {len(objects)+1} /Root 1 0 R >>\n".encode("ascii"))
    output.write(b"startxref\n")
    output.write(f"{xref_start}\n".encode("ascii"))
    output.write(b"%%EOF\n")
    return output.getvalue()
