"""Notes pinned to a corner of a node's card (D85, FR-NOTE): formatted text and pictures.

A note's text is stored as a small subset of HTML, rebuilt here from what the editor sends:
only the tags and attributes listed below survive, and every text and attribute value is
escaped again, so nothing that could run in a page is kept. A picture is a `NoteImage`,
referred to as `<img data-note-image="<sha256>">`. SVG pictures (from ChemDraw's "Save As
SVG", or SVG markup pasted as text) are cleaned too: no scripts, event handlers, embedded
pages or outside links. The interface draws them with `<img>`, which never runs scripts, and
the server sends them with a policy that forbids scripts even when opened on their own."""

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import timedelta
from html import escape
from html.parser import HTMLParser
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from chembook3d.models import Node, NodeKind, NodeNote, NoteImage, utcnow
from chembook3d.services import history
from chembook3d.services.records import RecordError, get, text_value

CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
COLOURS = ("yellow", "blue", "green", "pink", "grey")
MIN_WIDTH, MAX_WIDTH = 160, 640
MAX_BODY = 500_000  # characters of cleaned HTML; pictures are stored apart from it
MAX_IMAGE = 10 * 1024 * 1024  # bytes
# Pictures no note refers to are removed when a note is saved or deleted, unless they were
# added within this time: they may belong to a note still open in the editor.
UNUSED_IMAGE_GRACE = timedelta(hours=1)

# Fields in the history (A35); `collapsed` and `width` are layout.
RECORDED = ("title", "body", "corner", "colour")


# ---------- the note's text ----------

# Tag → attributes it may keep. Anything else is dropped, keeping the text inside it.
ALLOWED_TAGS: dict[str, tuple[str, ...]] = {
    **{tag: () for tag in ("p", "div", "br", "hr", "blockquote", "pre", "code")},
    **{tag: () for tag in ("b", "strong", "i", "em", "u", "s", "strike", "sub", "sup")},
    **{tag: () for tag in ("ul", "ol", "li", "h1", "h2", "h3", "h4")},
    "a": ("href",),
    "img": ("data-note-image", "alt"),
}
VOID_TAGS = {"br", "hr", "img"}
# Dropped together with everything inside them.
SKIPPED_TAGS = {"script", "style", "template", "iframe", "object", "embed", "noscript", "svg",
                "math", "head", "title", "textarea", "select", "button"}  # fmt: skip
IMAGE_ID = re.compile(r"^[0-9a-f]{64}$")
SAFE_LINK = re.compile(r"^(https?:|mailto:)", re.IGNORECASE)
IMAGE_REF = re.compile(r'data-note-image="([0-9a-f]{64})"')


class _Cleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []
        self.skipping: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skipping:
            if tag in SKIPPED_TAGS:
                self.skipping.append(tag)
            return
        if tag in SKIPPED_TAGS:
            self.skipping.append(tag)
            return
        if tag not in ALLOWED_TAGS:
            return
        kept: list[str] = []
        for name, value in attrs:
            if name not in ALLOWED_TAGS[tag] or value is None:
                continue
            value = value.strip()
            if name == "href" and not SAFE_LINK.match(value):
                continue
            if name == "data-note-image" and not IMAGE_ID.match(value):
                continue
            kept.append(f' {name}="{escape(value, quote=True)}"')
        if tag == "img" and not any(k.startswith(" data-note-image=") for k in kept):
            return  # only pictures stored in the investigation are shown
        self.out.append(f"<{tag}{''.join(kept)}>")
        if tag not in VOID_TAGS:
            self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS and self.open and self.open[-1] == tag and not self.skipping:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.skipping:
            if tag == self.skipping[-1]:
                self.skipping.pop()
            return
        if tag not in self.open:
            return
        while self.open:
            last = self.open.pop()
            self.out.append(f"</{last}>")
            if last == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.out.append(escape(data, quote=False))

    def result(self) -> str:
        self.close()
        return "".join(self.out) + "".join(f"</{tag}>" for tag in reversed(self.open))


def clean_body(html: str) -> str:
    """The note's HTML with only the allowed tags and attributes, every value escaped."""
    cleaner = _Cleaner()
    cleaner.feed(html)
    return cleaner.result()


def images_in(body: str) -> set[str]:
    return set(IMAGE_REF.findall(body))


# ---------- pictures ----------

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)
# Elements that run scripts, embed other documents or change attributes over time.
SVG_DROPPED = {"script", "foreignObject", "iframe", "object", "embed", "handler", "listener",
               "set", "animate", "animateMotion", "animateTransform", "animateColor",
               "audio", "video"}  # fmt: skip
SAFE_HREF = re.compile(r"^(#|data:image/(png|jpeg|gif|webp);base64,)", re.IGNORECASE)
OUTSIDE_URL = re.compile(r"url\(\s*(?!['\"]?\s*#)[^)]*\)", re.IGNORECASE)
CSS_IMPORT = re.compile(r"@import[^;]*;?", re.IGNORECASE)

RASTER = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _clean_css(text: str) -> str:
    return OUTSIDE_URL.sub("none", CSS_IMPORT.sub("", text))


def clean_svg(data: bytes) -> bytes:
    """An SVG picture without scripts, event handlers, embedded documents or outside links."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RecordError("The SVG picture is not UTF-8 text") from exc
    if re.search(r"<!ENTITY", text, re.IGNORECASE):
        raise RecordError("The SVG picture declares entities, which are not accepted")
    # SVG pasted from a web page often leaves out the namespaces a stand-alone file needs.
    start = re.search(r"<svg\b[^>]*>", text)
    if start is None:
        raise RecordError("This is not an SVG picture")
    tag = start.group(0)
    added = ""
    if "xmlns=" not in tag:
        added += f' xmlns="{SVG_NS}"'
    if "xlink:" in text and "xmlns:xlink=" not in tag:
        added += f' xmlns:xlink="{XLINK_NS}"'
    if added:
        text = text[: start.start()] + "<svg" + added + tag[4:] + text[start.end() :]
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise RecordError(f"The SVG picture could not be read: {exc}") from exc
    if root.tag != f"{{{SVG_NS}}}svg":
        raise RecordError("This is not an SVG picture")

    def walk(element: ET.Element) -> None:
        for child in list(element):
            if not isinstance(child.tag, str) or _local(child.tag) in SVG_DROPPED:
                element.remove(child)
                continue
            walk(child)
        for name in list(element.attrib):
            local = _local(name).lower()
            value = element.attrib[name]
            if local.startswith("on"):
                del element.attrib[name]
            elif local == "href" and not SAFE_HREF.match(value.strip()):
                del element.attrib[name]
            elif local == "style":
                element.attrib[name] = _clean_css(value)
            elif "url(" in value.lower():
                element.attrib[name] = _clean_css(value)
        if _local(element.tag) == "style" and element.text:
            element.text = _clean_css(element.text)

    walk(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def picture(data: bytes) -> tuple[bytes, str]:
    """The bytes to store and their media type; the type is read from the bytes."""
    if len(data) > MAX_IMAGE:
        raise RecordError(f"The picture is larger than {MAX_IMAGE // (1024 * 1024)} MB")
    for magic, media_type in RASTER:
        if data.startswith(magic):
            return data, media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return data, "image/webp"
    if b"<svg" in data:
        return clean_svg(data), "image/svg+xml"
    raise RecordError("Only PNG, JPEG, GIF, WebP and SVG pictures can go in a note")


def add_image(session: Session, data: bytes) -> NoteImage:
    stored, media_type = picture(data)
    image_id = hashlib.sha256(stored).hexdigest()
    found = session.get(NoteImage, image_id)
    if found is None:
        found = NoteImage(id=image_id, media_type=media_type, data=stored)
        session.add(found)
        session.flush()
    return found


def remove_unused_images(session: Session) -> int:
    used: set[str] = set()
    for body in session.scalars(select(NodeNote.body)):
        used |= images_in(body)
    cutoff = utcnow() - UNUSED_IMAGE_GRACE
    unused = [
        i
        for i in session.scalars(select(NoteImage.id).where(NoteImage.created_at < cutoff))
        if i not in used
    ]
    if unused:
        session.execute(delete(NoteImage).where(NoteImage.id.in_(unused)))
    return len(unused)


# ---------- notes ----------


def _field(field: str, value: Any) -> Any:
    if field == "title":
        return text_value("Title", value).strip()[:200]
    if field == "body":
        body = clean_body(text_value("Text", value))
        if len(body) > MAX_BODY:
            raise RecordError("The note's text is too long")
        return body
    if field == "corner":
        if value not in CORNERS:
            raise RecordError(f"corner must be one of {', '.join(CORNERS)}")
        return value
    if field == "colour":
        if value not in COLOURS:
            raise RecordError(f"colour must be one of {', '.join(COLOURS)}")
        return value
    if field == "collapsed":
        if not isinstance(value, bool):
            raise RecordError("collapsed must be true or false")
        return value
    if field == "width":
        if not isinstance(value, int) or isinstance(value, bool):
            raise RecordError("width must be a whole number")
        return max(MIN_WIDTH, min(MAX_WIDTH, value))
    raise RecordError(f"unknown field '{field}'")


def _check_images(session: Session, body: str) -> None:
    for image_id in images_in(body):
        if session.get(NoteImage, image_id) is None:
            raise RecordError("A picture in the note was not stored; paste or drop it again")


def snapshot(note: NodeNote) -> dict[str, Any]:
    return {"id": note.id, **{field: getattr(note, field) for field in RECORDED}}


def list_all(session: Session) -> list[NodeNote]:
    return list(session.scalars(select(NodeNote).order_by(NodeNote.seq)))


def create(session: Session, node_id: str, fields: dict[str, Any]) -> NodeNote:
    node = get(session, Node, node_id, "Node")
    if node.kind != NodeKind.NODE:
        raise RecordError("A free species is not on the canvas, so it has no card to pin a note to")
    note = NodeNote(node_id=node.id)
    for field, value in fields.items():
        setattr(note, field, _field(field, value))
    _check_images(session, note.body or "")
    session.add(note)
    session.flush()
    # A35: a note's changes are recorded on its node, so the node's history shows them.
    history.record(session, "note", node.id, "create", new=snapshot(note))
    return note


def update(session: Session, note_id: str, changes: dict[str, Any]) -> NodeNote:
    note = get(session, NodeNote, note_id, "Note")
    for field, value in changes.items():
        new = _field(field, value)
        if field == "body":
            _check_images(session, new)
        old = getattr(note, field)
        if old == new:
            continue
        setattr(note, field, new)
        if field in RECORDED:
            history.record(
                session,
                "note",
                note.node_id,
                "update",
                field,
                {"id": note.id, "title": note.title, field: old},
                {"id": note.id, "title": note.title, field: new},
            )
    session.flush()
    remove_unused_images(session)
    return note


def delete_note(session: Session, note_id: str) -> None:
    note = get(session, NodeNote, note_id, "Note")
    history.record(session, "note", note.node_id, "delete", old=snapshot(note))
    session.delete(note)
    session.flush()
    remove_unused_images(session)


def image_data(session: Session, image_ids: set[str]) -> dict[str, NoteImage]:
    if not image_ids:
        return {}
    found = session.scalars(select(NoteImage).where(NoteImage.id.in_(image_ids)))
    return {image.id: image for image in found}
