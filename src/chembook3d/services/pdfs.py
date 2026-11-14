"""Claude reads a PDF on this computer (D232, FR-MCP-20): a paper or its supporting information
the user saved, or one Claude's web fetch saved. Text page by page, the pages a word or a
compound's label appears on, and pages or a part of a page drawn as pictures, so Claude can
read structures and schemes. Nothing is written anywhere.

PDFium does the reading, through pypdfium2 (BSD-3-Clause or Apache-2.0, wheels for Windows and
Linux with no program to install); PyMuPDF was left out for its AGPL-3.0 licence. PDFium is not
safe to call from two threads at once, so every use holds `_LOCK`.
"""

import base64
import io
import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

# A162: what one answer holds at most, so a 200-page SI cannot flood the conversation.
MAX_TEXT_PAGES = 20
MAX_CHARS = 100_000
MAX_IMAGE_PAGES = 4
MAX_SIDE = 1600  # px, the longer side of a picture; Claude sees no more detail than this
MAX_ZOOM = 6.0  # times 72 dpi, for a part of a page (432 dpi)
MIN_TEXT = 20  # characters other than spaces below which a page has no text layer
MAX_MATCH_PAGES = 50
LINES_PER_MATCH = 3
MAX_LINE = 200
MAX_SIZE = 200_000_000  # bytes, as the Inspect window

_LOCK = threading.Lock()


class PdfFailed(Exception):
    """The file cannot be read as asked; the message says why, for the user and Claude."""


def is_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"%PDF-" in handle.read(1024)
    except OSError:
        return False


def page_numbers(spec: str, count: int) -> list[int]:
    """Pages as '3', '2-5' or '1,4,7-9', numbered from 1 as a PDF reader shows them."""
    pages: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if match is None:
            raise PdfFailed(f"Pages '{spec}': give page numbers as 3, 2-5 or 1,4,7-9")
        first = int(match.group(1))
        last = int(match.group(2) or first)
        if first < 1 or last < first:
            raise PdfFailed(f"Pages '{spec}': {part} is no range of pages")
        if last > count:
            raise PdfFailed(f"Pages '{spec}': the file has {count} pages")
        pages.extend(p for p in range(first, last + 1) if p not in pages)
    if not pages:
        raise PdfFailed(f"Pages '{spec}': no page named")
    return pages


def region_box(spec: str) -> tuple[float, float, float, float]:
    """A part of a page as 'left,top,right,bottom' in fractions of its width and height, from
    the top left corner (0,0 to 1,1 is the whole page)."""
    try:
        left, top, right, bottom = (float(v) for v in spec.split(","))
    except ValueError as exc:
        raise PdfFailed(
            f"Region '{spec}': give left,top,right,bottom as fractions, e.g. 0,0.5,1,1"
        ) from exc
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise PdfFailed(f"Region '{spec}': each value from 0 to 1, left < right, top < bottom")
    return left, top, right, bottom


def _clean(text: str) -> str:
    # NFKC turns the ligatures of typeset text (ﬁ, ﬂ) back into letters.
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip("\n")


def _pattern(find: str) -> re.Pattern[str]:
    """A word or label as a whole (24a, not 124a or 24ab), any case, any spacing."""
    words = find.split()
    if not words:
        raise PdfFailed("find: give the word or label to look for")
    body = r"\s+".join(re.escape(w) for w in words)
    start = r"(?<![0-9A-Za-z])" if find.strip()[0].isalnum() else ""
    end = r"(?![0-9A-Za-z])" if find.strip()[-1].isalnum() else ""
    return re.compile(start + body + end, re.IGNORECASE)


@dataclass
class _Page:
    text: str
    pictures: int


def _page(document: pdfium.PdfDocument, number: int) -> _Page:
    page = document[number - 1]
    try:
        textpage = page.get_textpage()
        try:
            text = _clean(textpage.get_text_bounded())
        finally:
            textpage.close()
        pictures = sum(
            1 for obj in page.get_objects(max_depth=3) if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE
        )
    finally:
        page.close()
    return _Page(text, pictures)


def _page_answer(number: int, page: _Page, with_text: bool) -> dict:
    answer: dict = {"page": number}
    if len("".join(page.text.split())) < MIN_TEXT:
        answer["no_text_layer"] = True
        answer["note"] = (
            "No text layer: the page is a picture, perhaps a scan. Look at it with images "
            "rather than guessing what it says."
            if page.pictures
            else "The page is blank."
        )
        if with_text and page.text:
            answer["text"] = page.text
        return answer
    if with_text:
        answer["text"] = page.text
    return answer


def _render(
    document: pdfium.PdfDocument,
    number: int,
    region: tuple[float, float, float, float] | None,
) -> dict:
    page = document[number - 1]
    try:
        width, height = page.get_size()
        left, top, right, bottom = region or (0.0, 0.0, 1.0, 1.0)
        part_w, part_h = (right - left) * width, (bottom - top) * height
        scale = min(MAX_SIDE / max(part_w, part_h), MAX_ZOOM)
        # PDFium's crop is what is cut off each side, in points: left, bottom, right, top.
        crop = (left * width, (1 - bottom) * height, (1 - right) * width, top * height)
        bitmap = page.render(scale=scale, crop=crop)
        try:
            image = bitmap.to_pil()
        finally:
            bitmap.close()
    finally:
        page.close()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    answer = {
        "page": number,
        "width": image.width,
        "height": image.height,
        "png": base64.b64encode(buffer.getvalue()).decode("ascii"),
    }
    if region is not None:
        answer["region"] = list(region)
    return answer


def _open(path: Path) -> pdfium.PdfDocument:
    if not path.is_file():
        raise PdfFailed(f"{path} is not a file")
    if path.stat().st_size > MAX_SIZE:
        raise PdfFailed(f"{path} is larger than {MAX_SIZE // 1_000_000} MB")
    if not is_pdf(path):
        raise PdfFailed(f"{path} is not a PDF")
    try:
        return pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        reason = str(exc)
        if "password" in reason.lower():
            raise PdfFailed(f"{path} is protected by a password") from exc
        raise PdfFailed(f"{path} cannot be read as a PDF: {reason}") from exc


def read(
    path: Path,
    pages: str | None = None,
    find: str | None = None,
    images: bool = False,
    region: str | None = None,
) -> dict:
    """What `read_pdf` answers. With `find` and no `pages`: only the pages the words are on,
    with their lines. Otherwise the text of the pages asked (the first MAX_TEXT_PAGES when
    none are), and with `images` those pages as PNG pictures (named pages only)."""
    box = region_box(region) if region else None
    if box is not None and not images:
        raise PdfFailed("region: ask for images too; a region is a part of a page's picture")
    pattern = _pattern(find) if find is not None else None
    with _LOCK:
        document = _open(path)
        try:
            return _read(document, path, pages, pattern, images, box)
        finally:
            document.close()


def _read(
    document: pdfium.PdfDocument,
    path: Path,
    pages: str | None,
    pattern: re.Pattern[str] | None,
    images: bool,
    box: tuple[float, float, float, float] | None,
) -> dict:
    count = len(document)
    answer: dict = {"path": str(path), "page_count": count}
    title = (document.get_metadata_dict().get("Title") or "").strip()
    if title:
        answer["title"] = title
    notes: list[str] = []
    named = page_numbers(pages, count) if pages else None
    if images:
        if named is None:
            raise PdfFailed(f"images: name the pages to draw (at most {MAX_IMAGE_PAGES})")
        if len(named) > MAX_IMAGE_PAGES:
            raise PdfFailed(
                f"images: at most {MAX_IMAGE_PAGES} pages at a time; {len(named)} were named"
            )
        if box is not None and len(named) != 1:
            raise PdfFailed("region: name one page")

    if pattern is not None:
        searched = named or list(range(1, count + 1))
        matches = []
        unreadable = []
        for number in searched:
            page = _page(document, number)
            if len("".join(page.text.split())) < MIN_TEXT and page.pictures:
                unreadable.append(number)
            lines = [line.strip() for line in page.text.split("\n") if pattern.search(line)]
            hits = len(pattern.findall(page.text))
            if hits:
                matches.append(
                    {
                        "page": number,
                        "count": hits,
                        "lines": [line[:MAX_LINE] for line in lines[:LINES_PER_MATCH]],
                    }
                )
        answer["find"] = pattern.pattern
        if len(matches) > MAX_MATCH_PAGES:
            notes.append(
                f"Found on {len(matches)} pages; the first {MAX_MATCH_PAGES} are listed. "
                "Look for something more particular, or name pages."
            )
            matches = matches[:MAX_MATCH_PAGES]
        answer["matches"] = matches
        if not matches:
            notes.append("Not found in the text of the pages searched.")
        if unreadable:
            notes.append(
                f"Pages {_ranges(unreadable)} have no text layer, so they were not searched: "
                "look at them with images."
            )
        if named is None:
            notes.append("Read the pages found with `pages` (and `images` to see them).")
            answer["notes"] = notes
            return answer

    if named is None:
        named = list(range(1, min(count, MAX_TEXT_PAGES) + 1))
        if count > MAX_TEXT_PAGES:
            notes.append(
                f"The file has {count} pages; pages 1-{MAX_TEXT_PAGES} are given. Use `find` "
                "to see which pages hold what you look for, or name pages."
            )
    elif len(named) > MAX_TEXT_PAGES:
        notes.append(
            f"At most {MAX_TEXT_PAGES} pages of text at a time; pages "
            f"{_ranges(named[MAX_TEXT_PAGES:])} were left out."
        )
        named = named[:MAX_TEXT_PAGES]

    out = []
    chars = 0
    cut: list[int] = []
    for number in named:
        page = _page(document, number)
        if chars + len(page.text) > MAX_CHARS and out:
            cut.append(number)
            out.append(_page_answer(number, page, with_text=False))
            continue
        chars += len(page.text)
        out.append(_page_answer(number, page, with_text=True))
    if cut:
        notes.append(
            f"The text of pages {_ranges(cut)} was left out to keep the answer under "
            f"{MAX_CHARS} characters; ask for them on their own."
        )
    answer["pages"] = out
    if images:
        answer["images"] = [_render(document, number, box) for number in named]
    if notes:
        answer["notes"] = notes
    return answer


def _ranges(numbers: list[int]) -> str:
    parts = []
    start = previous = numbers[0]
    for number in [*numbers[1:], None]:
        if number is not None and number == previous + 1:
            previous = number
            continue
        parts.append(str(start) if start == previous else f"{start}-{previous}")
        if number is not None:
            start = previous = number
    return ", ".join(parts)
