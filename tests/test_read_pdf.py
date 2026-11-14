"""D232: Claude reads a PDF on this computer (T-MCP-37..39): text per page, the pages a label is
on, pages and a part of a page as pictures, pages with no text layer, the limits of one answer,
and the pictures handed to Claude as images over MCP."""

import base64
import io

import anyio
from PIL import Image

from chembook3d.claude_panel import NOTEBOOK_GUIDE
from chembook3d.mcp_server import INSTRUCTIONS
from chembook3d.mcp_tools import PANEL_ALLOWED
from chembook3d.services import pdfs
from tests.pdf_text import write_pdf
from tests.test_mcp import _app_bridge, _json

SI = [
    [
        "Supporting Information",
        "Synthesis of complex 24a",
        "24a: Ru, 1.2 g; compare 124a and 24ab.",
    ],
    "picture",
    None,
    ["Crystal data for 24A", "Space group P-1"],
]


def _png(text: str) -> Image.Image:
    data = base64.b64decode(text)
    assert data.startswith(b"\x89PNG")
    return Image.open(io.BytesIO(data))


def test_the_text_of_each_page_and_pages_without_a_text_layer(open_client, tmp_path):
    """T-MCP-37: the text of each page, a picture page and a blank page said to have no text
    layer, a label found whole in any case only, and named pages read with their matches."""
    pdf = write_pdf(tmp_path / "si.pdf", SI)
    read = open_client.get("/api/local-files/pdf", params={"path": str(pdf)})
    assert read.status_code == 200, read.text
    read = read.json()
    assert read["page_count"] == 4 and "notes" not in read and "images" not in read
    first, picture, blank, last = read["pages"]
    assert "Synthesis of complex 24a" in first["text"] and "no_text_layer" not in first
    assert picture["no_text_layer"] is True and "scan" in picture["note"]
    assert blank["no_text_layer"] is True and blank["note"] == "The page is blank."
    assert last["text"].splitlines() == ["Crystal data for 24A", "Space group P-1"]

    found = open_client.get("/api/local-files/pdf", params={"path": str(pdf), "find": "24a"})
    found = found.json()
    assert "pages" not in found
    assert [(m["page"], m["count"]) for m in found["matches"]] == [(1, 2), (4, 1)]
    assert found["matches"][0]["lines"] == [
        "Synthesis of complex 24a",
        "24a: Ru, 1.2 g; compare 124a and 24ab.",
    ]
    assert any("Pages 2 have no text layer" in note for note in found["notes"])
    spaced = {"path": str(pdf), "find": "space   GROUP", "pages": "1,4"}
    spaced = open_client.get("/api/local-files/pdf", params=spaced).json()
    assert [m["page"] for m in spaced["matches"]] == [4]
    assert [p["page"] for p in spaced["pages"]] == [1, 4]
    missing = {"path": str(pdf), "find": "25b"}
    missing = open_client.get("/api/local-files/pdf", params=missing).json()
    assert missing["matches"] == [] and "Not found" in missing["notes"][0]

    assert open_client.get("/api/canvas").json()["nodes"] == []


def test_pages_and_a_part_of_a_page_as_pictures(open_client, tmp_path):
    """T-MCP-38: named pages drawn at most 1600 px on the longer side; a region of one page
    drawn larger; images refused without pages, beyond 4 pages, a region of two pages."""
    pdf = write_pdf(tmp_path / "si.pdf", SI)
    drawn = {"path": str(pdf), "pages": "1-2", "images": True}
    drawn = open_client.get("/api/local-files/pdf", params=drawn).json()
    assert [i["page"] for i in drawn["images"]] == [1, 2]
    page = _png(drawn["images"][0]["png"])
    assert page.size == (1131, 1600) == (drawn["images"][0]["width"], drawn["images"][0]["height"])
    assert [p["page"] for p in drawn["pages"]] == [1, 2]  # the text comes too

    zoom = {"path": str(pdf), "pages": "2", "images": True, "region": "0.1,0.1,0.5,0.3"}
    zoom = open_client.get("/api/local-files/pdf", params=zoom).json()["images"][0]
    part = _png(zoom["png"])
    assert zoom["region"] == [0.1, 0.1, 0.5, 0.3]
    # 238 x 168.4 pt at 6x, the largest zoom (PDFium rounds the crop to whole pixels)
    assert part.width == 1428 and abs(part.height - 1010) <= 2
    assert part.getpixel((10, 10)) != (255, 255, 255)  # inside the grey picture

    six = write_pdf(tmp_path / "six.pdf", [["y" * 30]] * 6)
    many = {"path": str(six), "images": True, "pages": "1-4,6"}
    many = open_client.get("/api/local-files/pdf", params=many)
    assert many.status_code == 422 and "at most 4 pages" in many.json()["detail"]
    for params, reason in (
        ({"images": True}, "name the pages"),
        ({"images": True, "pages": "1-5"}, "the file has 4 pages"),
        ({"images": True, "pages": "1-2", "region": "0,0,1,1"}, "name one page"),
        ({"pages": "1", "region": "0,0,1,1"}, "ask for images too"),
        ({"images": True, "pages": "1", "region": "0,0.5,1"}, "left,top,right,bottom"),
        ({"images": True, "pages": "1", "region": "0.6,0,0.4,1"}, "left < right"),
        ({"pages": "0"}, "no range of pages"),
        ({"pages": "three"}, "give page numbers"),
    ):
        refused = open_client.get("/api/local-files/pdf", params={"path": str(pdf), **params})
        assert refused.status_code == 422, params
        assert reason in refused.json()["detail"], refused.text


def test_what_is_refused_and_the_limits_of_one_answer(open_client, tmp_path, monkeypatch):
    """T-MCP-39: a file that is no PDF, a broken PDF, a missing file and a page from another
    computer are refused; a long file gives its first 20 pages and says how to go on; a text
    too long for one answer leaves pages out and says which; summarize_file sends a PDF on."""
    text = tmp_path / "notes.pdf"
    text.write_text("not a pdf\n", encoding="utf-8")
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog")
    for path, reason in (
        (text, "is not a PDF"),
        (broken, "cannot be read as a PDF"),
        (tmp_path / "missing.pdf", "is not a file"),
    ):
        refused = open_client.get("/api/local-files/pdf", params={"path": str(path)})
        assert refused.status_code == 422 and reason in refused.json()["detail"], path
    pdf = write_pdf(tmp_path / "long.pdf", [[f"Page {n} of the SI", "x" * 30] for n in range(25)])
    foreign = {"origin": "https://example.com"}
    refused = open_client.get("/api/local-files/pdf", params={"path": str(pdf)}, headers=foreign)
    assert refused.status_code == 403

    read = open_client.get("/api/local-files/pdf", params={"path": str(pdf)}).json()
    assert len(read["pages"]) == 20 and "pages 1-20 are given" in read["notes"][0]
    many = open_client.get("/api/local-files/pdf", params={"path": str(pdf), "pages": "1-25"})
    assert "pages 21-25 were left out" in many.json()["notes"][0]
    monkeypatch.setattr(pdfs, "MAX_CHARS", 100)
    cut = open_client.get("/api/local-files/pdf", params={"path": str(pdf), "pages": "1-4"})
    cut = cut.json()
    assert [("text" in p) for p in cut["pages"]] == [True, True, False, False]
    assert "pages 3-4 was left out" in cut["notes"][0]

    summary = open_client.get("/api/local-files/summary", params={"path": str(pdf)})
    assert summary.status_code == 422 and "read_pdf" in summary.json()["detail"]


def test_claude_sees_the_pages_as_pictures(tmp_path):
    """T-MCP-38: over MCP the answer's text comes first, then each picture as an image after a
    line naming its page; read_pdf is not asked about in the panel, and both guides tell
    Claude how to use it."""
    pdf = write_pdf(tmp_path / "si.pdf", SI)

    async def run():
        bridge, _page = _app_bridge()
        result = await bridge.call("read_pdf", {"path": str(pdf), "pages": "1,2", "images": True})
        assert not result.is_error
        kinds = [c.type for c in result.content]
        assert kinds == ["text", "text", "image", "text", "image"]
        assert result.content[1].text == "Page 1 (1131x1600 px):"
        assert result.content[2].mime_type == "image/png"
        _png(result.content[4].data)
        read = _json(result)
        assert read["page_count"] == 4 and "images" not in read
        text_only = _json(await bridge.call("read_pdf", {"path": str(pdf), "find": "24a"}))
        assert [m["page"] for m in text_only["matches"]] == [1, 4]
        refused = await bridge.call("read_pdf", {"path": str(tmp_path / "none.pdf")})
        assert refused.is_error and "is not a file" in refused.content[0].text

    anyio.run(run)
    assert "read_pdf" in PANEL_ALLOWED
    for text in (INSTRUCTIONS, NOTEBOOK_GUIDE):
        assert "read_pdf" in text and "D232" in text
