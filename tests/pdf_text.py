"""Small PDFs written for the tests of `read_pdf` (D232): pages of text in Helvetica, pages that
hold only a picture (as a scanned page does) and blank pages. Made here, so no paper is copied
into the repository."""

import zlib
from pathlib import Path

WIDTH, HEIGHT = 595, 842  # A4 in points


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _text_stream(lines: list[str]) -> bytes:
    parts = ["BT", "/F1 11 Tf", "14 TL", f"56 {HEIGHT - 72} Td"]
    for line in lines:
        parts.append(f"({_escape(line)}) Tj T*")
    parts.append("ET")
    return "\n".join(parts).encode("latin-1")


def _image_stream() -> bytes:
    return f"q {WIDTH - 112} 0 0 {HEIGHT - 144} 56 72 cm /Im1 Do Q".encode()


def write_pdf(path: Path, pages: list[list[str] | None | str]) -> Path:
    """Each page is a list of text lines, None for a blank page, or "picture" for a page that
    holds one grey image and no text."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    def stream(data: bytes, extra: str = "") -> bytes:
        packed = zlib.compress(data)
        head = f"<< /Length {len(packed)} /Filter /FlateDecode {extra}>>\nstream\n".encode()
        return head + packed + b"\nendstream"

    catalog = add(b"")  # filled in at the end
    tree = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pixels = bytes((x * 7 + y * 3) % 256 for y in range(64) for x in range(64))
    image = add(
        stream(
            pixels,
            "/Type /XObject /Subtype /Image /Width 64 /Height 64 "
            "/ColorSpace /DeviceGray /BitsPerComponent 8 ",
        )
    )
    kids = []
    for page in pages:
        if page == "picture":
            content = add(stream(_image_stream()))
            resources = f"<< /XObject << /Im1 {image} 0 R >> >>"
        elif page is None:
            content = add(stream(b""))
            resources = "<< >>"
        else:
            content = add(stream(_text_stream(list(page))))
            resources = f"<< /Font << /F1 {font} 0 R >> >>"
        kids.append(
            add(
                f"<< /Type /Page /Parent {tree} 0 R /MediaBox [0 0 {WIDTH} {HEIGHT}] "
                f"/Resources {resources} /Contents {content} 0 R >>".encode()
            )
        )
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {tree} 0 R >>".encode()
    refs = " ".join(f"{kid} 0 R" for kid in kids)
    objects[tree - 1] = f"<< /Type /Pages /Kids [{refs}] /Count {len(kids)} >>".encode()

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\n".encode()
    out += f"startxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path
