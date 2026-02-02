from pathlib import Path


def estimate_text_width(text: str, font_size: int) -> float:
    return len(text) * font_size * 0.52


def build_pdf_one_page(text_path: Path, pdf_path: Path) -> None:
    def pdf_escape(text: str) -> str:
        return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    lines = text_path.read_text(encoding="utf-8").splitlines()

    page_width = 612
    left_margin = 48
    leading = 11
    start_y = 792 - 48

    content_lines = []
    content_lines.append("BT")
    content_lines.append("/F1 9 Tf")
    content_lines.append(f"{left_margin} {start_y} Td")

    for i, line in enumerate(lines):
        if i == 0:
            title_size = 12
            title_width = estimate_text_width(line, title_size)
            title_x = max((page_width - title_width) / 2, left_margin)
            content_lines.append("ET")
            content_lines.append("BT")
            content_lines.append(f"/F1 {title_size} Tf")
            content_lines.append(f"{title_x:.2f} {start_y} Td")
            content_lines.append(f"({pdf_escape(line)}) Tj")
            content_lines.append("ET")
            content_lines.append("BT")
            content_lines.append("/F1 9 Tf")
            content_lines.append(f"{left_margin} {start_y - leading} Td")
            continue
        safe_line = line.replace("€", "EUR")
        content_lines.append(f"({pdf_escape(safe_line)}) Tj")
        content_lines.append(f"0 -{leading} Td")

    content_lines.append("ET")
    content_stream = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects = []

    def add_object(obj_bytes: bytes) -> None:
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
    with pdf_path.open("wb") as f:
        f.write(b"%PDF-1.4\n")
        for i, obj in enumerate(objects, start=1):
            xref_positions.append(f.tell())
            f.write(f"{i} 0 obj\n".encode("ascii"))
            f.write(obj)
            f.write(b"\nendobj\n")
        xref_start = f.tell()
        f.write(b"xref\n")
        f.write(f"0 {len(objects)+1}\n".encode("ascii"))
        f.write(b"0000000000 65535 f \n")
        for pos in xref_positions:
            f.write(f"{pos:010d} 00000 n \n".encode("ascii"))
        f.write(b"trailer\n")
        f.write(f"<< /Size {len(objects)+1} /Root 1 0 R >>\n".encode("ascii"))
        f.write(b"startxref\n")
        f.write(f"{xref_start}\n".encode("ascii"))
        f.write(b"%%EOF\n")


def main() -> None:
    base = Path(__file__).resolve().parents[1] / "docs"
    build_pdf_one_page(base / "water-bill-guide.txt", base / "water-bill-guide.pdf")
    build_pdf_one_page(base / "water-bill-guide-fi.txt", base / "water-bill-guide-fi.pdf")


if __name__ == "__main__":
    main()
