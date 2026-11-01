"""Rebuild the minimal PDF fixture so it carries extractable text.

The fixture used to be a page with no content stream, so ``extract_text``
returned nothing and the only PDF test skipped itself. A PDF that cannot be
read is not a PDF the product claims to support, so the fixture is rebuilt
here -- deterministically, with a correct cross-reference table -- rather than
being checked in as opaque bytes.

Run this only when the fixture needs regenerating:

    .\\.venv\\Scripts\\python.exe tests\\make_minimal_pdf.py
"""

from __future__ import annotations

from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "minimal.pdf"

#: The text the fixture must yield when it is read.
TEXT_LINES = [
    "Municipal waste collection rates fell by 30 percent between 2019 and 2023.",
    "The decline was driven by a citywide composting programme introduced in 2020.",
]


def build_pdf() -> bytes:
    """Build a one-page PDF whose text is extractable."""
    content_ops = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
    for line in TEXT_LINES:
        content_ops.append(f"({line}) Tj")
        content_ops.append("T*")
    content_ops.append("ET")
    stream = "\n".join(content_ops).encode("latin-1")

    objects: list[bytes] = [
        # 1: the document information dictionary, read by the metadata extractor.
        b"<< /Producer (lmeditor test fixture) /Title (Municipal Waste Audit) "
        b"/Author (LMeditor) /Subject (Collection rates) "
        b"/Keywords (waste, composting, municipal) >>",
        # 2: the page tree.
        b"<< /Type /Pages /Count 1 /Kids [ 4 0 R ] >>",
        # 3: the document catalog.
        b"<< /Type /Catalog /Pages 2 0 R >>",
        # 4: the page, with a font resource and the content stream.
        b"<< /Type /Page /Parent 2 0 R /MediaBox [ 0 0 612 792 ] "
        b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
        b"/BaseFont /Helvetica >> >> >> /Contents 5 0 R >>",
        # 5: the content stream itself.
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_offset = len(out)
    count = len(objects) + 1
    out += f"xref\n0 {count}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {count} /Root 3 0 R /Info 1 0 R >>\n".encode("ascii")
    out += f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    return bytes(out)


def main() -> int:
    data = build_pdf()
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_bytes(data)

    # Verify with the same reader the product uses.
    from pypdf import PdfReader

    reader = PdfReader(str(FIXTURE))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert len(reader.pages) == 1, len(reader.pages)
    for line in TEXT_LINES:
        assert line in text, f"the fixture does not yield {line!r}: {text!r}"
    meta = reader.metadata
    assert meta is not None and meta.title == "Municipal Waste Audit", meta
    print(f"wrote {FIXTURE} ({len(data)} bytes)")
    print(f"pages: {len(reader.pages)}, title: {meta.title!r}")
    print(f"extracted text: {text.strip()!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
