"""Notes pinned to node cards (D85, A35, FR-NOTE-01…05, T-NOTE-*): the cleaned text, the
pictures (raster and SVG, cleaned of anything that could run), the history and the copy."""

from datetime import timedelta

import pytest

from chembook3d.models import NoteImage
from chembook3d.services import notes
from chembook3d.services.records import RecordError
from tests.test_pathway import get, history, node, patch, post
from tests.test_snapshot import data_of, export, page  # noqa: F401 (fixture)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
CHEMDRAW_SVG = b"""<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
     width="120pt" height="40pt" viewBox="0 0 120 40" onload="alert(1)">
  <style>@import url(https://example.org/x.css); text { fill: url(https://example.org/p); }</style>
  <script>alert(2)</script>
  <defs><path id="b" d="M0 0 L10 10"/></defs>
  <g onclick="alert(3)" style="stroke:#000;background:url(http://example.org/i.png)">
    <use xlink:href="#b"/>
    <use href="javascript:alert(4)"/>
    <image href="https://example.org/tracker.png"/>
    <a href="javascript:alert(5)"><text x="1" y="20">Ru</text></a>
    <foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>
    <set attributeName="href" to="javascript:alert(6)"/>
  </g>
</svg>"""


def upload(client, data: bytes, status: int = 201) -> dict:
    response = client.post("/api/note-images", content=data)
    assert response.status_code == status, response.text
    return response.json()


def test_clean_body_keeps_formatting_and_drops_the_rest():
    # FR-NOTE-02
    dirty = (
        '<p style="color:red" onclick="x()">A <b>bold</b> <i>word</i> H<sub>2</sub>O'
        "<script>alert(1)</script><style>p{}</style></p>"
        '<ul><li>one<li>two</ul><a href="javascript:alert(1)">bad</a> '
        '<a href="https://doi.org/10.1/x" target="_blank">doi</a>'
        '<img src="https://example.org/t.png"><img data-note-image="' + "a" * 64 + '" src="x">'
        "<span>kept text</span> 1 &lt; 2 <iframe>gone</iframe><table><tr><td>cell"
    )
    clean = notes.clean_body(dirty)
    assert clean == (
        "<p>A <b>bold</b> <i>word</i> H<sub>2</sub>O</p>"
        "<ul><li>one<li>two</li></li></ul><a>bad</a> "
        '<a href="https://doi.org/10.1/x">doi</a>'
        '<img data-note-image="' + "a" * 64 + '">kept text 1 &lt; 2 cell'
    )


def test_note_on_a_node_moves_through_the_history(open_client):
    # FR-NOTE-01, FR-NOTE-04, A35
    n = node(open_client, label="Ru-CAAC")
    note = post(open_client, f"/nodes/{n['id']}/notes", {"title": "Rotamer!", "body": "<p>Hi</p>"})
    assert note["corner"] == "top-right" and note["colour"] == "yellow" and not note["collapsed"]
    second = post(open_client, f"/nodes/{n['id']}/notes", {"corner": "bottom-left"})
    canvas = get(open_client, "/canvas")
    assert [x["id"] for x in canvas["notes"]] == [note["id"], second["id"]]

    patch(
        open_client,
        f"/notes/{note['id']}",
        {"body": "<p>Hi <b>there</b></p>", "corner": "top-left"},
    )
    patch(open_client, f"/notes/{note['id']}", {"collapsed": True, "width": 9999})
    patch(open_client, f"/notes/{note['id']}", {"title": "Rotamer!"})  # no change
    changed = get(open_client, "/notes")[0]
    assert changed["collapsed"] and changed["width"] == notes.MAX_WIDTH
    response = open_client.delete(f"/api/notes/{second['id']}")
    assert response.status_code == 204

    entries = [e for e in history(open_client, n["id"]) if e["record_type"] == "note"]
    assert [(e["action"], e["field"]) for e in entries] == [
        ("delete", None),
        ("update", "body"),
        ("update", "corner"),
        ("create", None),
        ("create", None),
    ]
    assert entries[2]["old_value"] == {"id": note["id"], "title": "Rotamer!", "corner": "top-right"}
    assert entries[1]["new_value"]["body"] == "<p>Hi <b>there</b></p>"

    # Deleting the node takes its notes with it.
    assert open_client.delete(f"/api/nodes/{n['id']}").status_code in (200, 204)
    assert get(open_client, "/notes") == []


def test_refusals(open_client):
    n = node(open_client)
    species = node(open_client, kind="species")
    post(open_client, f"/nodes/{species['id']}/notes", {}, status=422)
    post(open_client, "/nodes/nope/notes", {}, status=404)
    post(open_client, f"/nodes/{n['id']}/notes", {"corner": "middle"}, status=422)
    post(open_client, f"/nodes/{n['id']}/notes", {"colour": "#ff0000"}, status=422)
    # A picture the investigation does not hold
    body = f'<img data-note-image="{"b" * 64}">'
    post(open_client, f"/nodes/{n['id']}/notes", {"body": body}, status=422)


def test_raster_pictures_are_stored_once_and_served_safely(open_client):
    # FR-NOTE-03
    first = upload(open_client, PNG)
    again = upload(open_client, PNG)
    assert first == again and first["media_type"] == "image/png"
    response = open_client.get(f"/api/note-images/{first['id']}")
    assert response.content == PNG and response.headers["content-type"] == "image/png"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    upload(open_client, b"GIF89a....", 201)
    upload(open_client, b"RIFF\x00\x00\x00\x00WEBPVP8 ", 201)
    upload(open_client, b"%PDF-1.7", 422)
    upload(open_client, b"\x00" * (notes.MAX_IMAGE + 1), 422)


def test_svg_from_chemdraw_is_kept_as_svg_without_scripts_or_outside_links(open_client):
    # FR-NOTE-03, T-NOTE-02
    image = upload(open_client, CHEMDRAW_SVG)
    assert image["media_type"] == "image/svg+xml"
    svg = open_client.get(f"/api/note-images/{image['id']}").text
    for bad in ("script", "alert", "onload", "onclick", "javascript", "example.org", "@import",
                "foreignObject", "<set"):  # fmt: skip
        assert bad not in svg, bad
    for kept in ('viewBox="0 0 120 40"', 'width="120pt"', 'href="#b"', ">Ru</text>", "stroke:#000"):
        assert kept in svg, kept


def test_svg_pasted_as_text_without_namespaces():
    stored, media_type = notes.picture(b'<svg width="10" height="10"><use xlink:href="#a"/></svg>')
    assert media_type == "image/svg+xml"
    assert b'xmlns="http://www.w3.org/2000/svg"' in stored and b"xlink" in stored


@pytest.mark.parametrize(
    "bad",
    [
        b'<!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg">&a;</svg>',
        b"<svg xmlns='http://www.w3.org/2000/svg'><g></svg>",
        b'<html><body><svg xmlns="http://www.w3.org/2000/svg"/></body></html>',
    ],
)
def test_svg_refusals(bad):
    with pytest.raises(RecordError):
        notes.picture(bad)


def test_unused_pictures_are_removed_after_a_while(open_client):
    n = node(open_client)
    kept = upload(open_client, PNG)["id"]
    dropped = upload(open_client, PNG + b"x")["id"]
    note = post(open_client, f"/nodes/{n['id']}/notes", {"body": f'<img data-note-image="{kept}">'})
    # Just added: kept, it may still be in the editor.
    patch(open_client, f"/notes/{note['id']}", {"title": "t"})
    assert open_client.get(f"/api/note-images/{dropped}").status_code == 200

    investigation = open_client.app.state.investigation
    with investigation.sessions.begin() as session:
        for image in session.query(NoteImage):
            image.created_at -= notes.UNUSED_IMAGE_GRACE + timedelta(minutes=1)
    patch(open_client, f"/notes/{note['id']}", {"title": "u"})
    assert open_client.get(f"/api/note-images/{dropped}").status_code == 404
    assert open_client.get(f"/api/note-images/{kept}").status_code == 200


def test_the_copy_holds_the_notes_and_their_pictures(open_client, page):  # noqa: F811
    # FR-NOTE-05
    n = node(open_client, label="TS1")
    image = upload(open_client, CHEMDRAW_SVG)["id"]
    unused = upload(open_client, PNG)["id"]
    post(
        open_client,
        f"/nodes/{n['id']}/notes",
        {"body": f'<p>See</p><img data-note-image="{image}">'},
    )
    data = data_of(export(open_client))
    assert [x["node_id"] for x in data["canvas"]["notes"]] == [n["id"]]
    assert list(data["note_images"]) == [image]
    assert data["note_images"][image].startswith("data:image/svg+xml;base64,")
    assert unused not in data["note_images"]


def test_a_note_floats_apart_from_its_card_and_can_be_resized(open_client):
    # FR-NOTE-06, D87, A37
    n = node(open_client)
    note = post(open_client, f"/nodes/{n['id']}/notes", {"title": "Apart"})
    assert (note["placement"], note["offset_x"], note["offset_y"], note["height"]) == (
        "corner",
        40,
        40,
        None,
    )
    moved = patch(
        open_client,
        f"/notes/{note['id']}",
        {"placement": "line", "offset_x": -30, "offset_y": 99999, "width": 420, "height": 10},
    )
    assert (moved["placement"], moved["offset_x"], moved["offset_y"]) == ("line", -30, 5000)
    assert (moved["width"], moved["height"]) == (420, notes.MIN_HEIGHT)
    freed = patch(open_client, f"/notes/{note['id']}", {"placement": "free", "height": None})
    assert freed["placement"] == "free" and freed["height"] is None
    patch(open_client, f"/notes/{note['id']}", {"placement": "middle"}, status=422)
    patch(open_client, f"/notes/{note['id']}", {"offset_x": "left"}, status=422)
    patch(open_client, f"/notes/{note['id']}", {"height": 2.5}, status=422)
    # Layout: none of it is in the history.
    entries = [e for e in history(open_client, n["id"]) if e["record_type"] == "note"]
    assert [e["action"] for e in entries] == ["create"]
