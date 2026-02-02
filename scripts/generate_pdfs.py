from pathlib import Path


def estimate_text_width(text: str, font_size: int) -> float:
    return len(text) * font_size * 0.52


def build_pdf_multipage(text_path: Path, pdf_path: Path) -> None:
    """Build a PDF with automatic page breaks."""
    
    def pdf_escape(text: str) -> str:
        return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    lines = text_path.read_text(encoding="utf-8").splitlines()

    page_width = 612
    page_height = 792
    left_margin = 48
    top_margin = 48
    bottom_margin = 48
    leading = 12
    font_size = 9
    title_size = 12
    start_y = page_height - top_margin
    min_y = bottom_margin

    # Split lines into pages
    pages_content = []
    current_page_lines = []
    current_y = start_y

    for i, line in enumerate(lines):
        safe_line = line.replace("€", "EUR")
        
        if i == 0:
            # Title takes more space
            current_y -= title_size + 4
        else:
            current_y -= leading

        if current_y < min_y:
            # Start new page
            pages_content.append(current_page_lines)
            current_page_lines = []
            current_y = start_y - leading

        current_page_lines.append((i, safe_line))

    if current_page_lines:
        pages_content.append(current_page_lines)

    # Build content streams for each page
    content_streams = []
    for page_idx, page_lines in enumerate(pages_content):
        content_lines = []
        content_lines.append("BT")
        content_lines.append(f"/F1 {font_size} Tf")
        
        y = start_y
        for line_idx, line in page_lines:
            if line_idx == 0:
                # Center title
                title_width = estimate_text_width(line, title_size)
                title_x = max((page_width - title_width) / 2, left_margin)
                content_lines.append(f"/F1 {title_size} Tf")
                content_lines.append(f"{title_x:.2f} {y} Td")
                content_lines.append(f"({pdf_escape(line)}) Tj")
                y -= title_size + 4
                content_lines.append(f"/F1 {font_size} Tf")
                content_lines.append(f"{left_margin - title_x:.2f} {-(title_size + 4)} Td")
            else:
                if line_idx == page_lines[0][0] and page_idx > 0:
                    # First line of non-first page
                    content_lines.append(f"{left_margin} {y} Td")
                content_lines.append(f"({pdf_escape(line)}) Tj")
                content_lines.append(f"0 -{leading} Td")
                y -= leading

        content_lines.append("ET")
        content_stream = "\n".join(content_lines).encode("latin-1", errors="replace")
        content_streams.append(content_stream)

    num_pages = len(content_streams)
    
    # Build PDF objects
    objects = []

    def add_object(obj_bytes: bytes) -> None:
        objects.append(obj_bytes)

    # Object 1: Catalog
    add_object(b"<< /Type /Catalog /Pages 2 0 R >>")

    # Object 2: Pages (parent)
    page_refs = " ".join([f"{3 + i * 2} 0 R" for i in range(num_pages)])
    add_object(f"<< /Type /Pages /Kids [{page_refs}] /Count {num_pages} >>".encode("ascii"))

    # Objects for each page (Page + Contents pairs)
    font_obj_num = 3 + num_pages * 2  # Font object comes after all pages
    for i, content_stream in enumerate(content_streams):
        page_obj_num = 3 + i * 2
        content_obj_num = 4 + i * 2
        
        # Page object
        add_object(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Contents {content_obj_num} 0 R /Resources << /Font << /F1 {font_obj_num} 0 R >> >> >>".encode("ascii")
        )
        
        # Content stream object
        add_object(
            b"<< /Length %d >>\nstream\n%s\nendstream"
            % (len(content_stream), content_stream)
        )

    # Font object
    add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    # Write PDF file
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
    
    print(f"Generated {pdf_path.name} ({num_pages} page{'s' if num_pages > 1 else ''})")


def main() -> None:
    base = Path(__file__).resolve().parents[1] / "docs"
    build_pdf_multipage(base / "water-bill-guide.txt", base / "water-bill-guide.pdf")
    build_pdf_multipage(base / "water-bill-guide-fi.txt", base / "water-bill-guide-fi.pdf")
    build_pdf_multipage(base / "calculation-explained.txt", base / "calculation-explained.pdf")
    build_pdf_multipage(base / "calculation-explained-fi.txt", base / "calculation-explained-fi.pdf")


if __name__ == "__main__":
    main()
