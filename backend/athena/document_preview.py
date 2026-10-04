"""Bounded DOCX body structure, never rendered document HTML or active content.

Only the main WordprocessingML body and paragraph styles are read. Relationships,
macros, fonts, pictures, embedded objects and external resources are never loaded.
The caller retains and serves the unchanged original independently of this preview.
"""
from dataclasses import dataclass
import io
import re
import struct
import xml.etree.ElementTree as ET
import zipfile

WORD_NAMESPACES = {"http://schemas.openxmlformats.org/wordprocessingml/2006/main", "http://purl.oclc.org/ooxml/wordprocessingml/main"}
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_XML_NODES = 60000
MAX_XML_DEPTH = 64
MAX_OUTPUT_CHARS = 200000
MAX_OUTPUT_BLOCKS = 1000
MAX_OUTPUT_CELLS = 3000


def _word(element):
    if not isinstance(element.tag, str) or not element.tag.startswith("{"):
        return ""
    namespace, _, local = element.tag[1:].partition("}")
    return local if namespace in WORD_NAMESPACES else ""


def _attribute(element, name):
    if element is None:
        return None
    return next((element.get(f"{{{namespace}}}{name}") for namespace in WORD_NAMESPACES if f"{{{namespace}}}{name}" in element.attrib), None)


def _child(element, name):
    return next((child for child in element if _word(child) == name), None) if element is not None else None


class _BoundedXML(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.nodes = 0
        self.depth = 0

    def doctype(self, *_):
        # Parser-level rejection also covers UTF-16/32 declarations that a byte
        # substring scan can miss. No declared entities are expanded.
        raise ValueError("Document declarations are not supported")

    def start(self, tag, attrs):
        self.nodes += 1
        self.depth += 1
        if self.nodes > MAX_XML_NODES or self.depth > MAX_XML_DEPTH:
            raise ValueError("Document structure is too large")
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)


def _read_xml(archive, name):
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_BYTES or info.flag_bits & 1:
        raise ValueError("Document part is too large or encrypted")
    with archive.open(info) as handle:
        content = handle.read(MAX_XML_BYTES + 1)
    if len(content) > MAX_XML_BYTES:
        raise ValueError("Document part is too large")
    return ET.fromstring(content, parser=ET.XMLParser(target=_BoundedXML()))


def _check_directory(data):
    # Bound the central directory before ZipFile constructs an object per entry.
    # ZIP64/spanned packages are unnecessary for this small preview and fail closed.
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Document archive is too large")
    offset = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
    if offset < 0 or offset + 22 > len(data):
        raise ValueError("Document archive has no directory")
    _, disk, directory_disk, disk_entries, entries, directory_size, directory_offset, comment_size = struct.unpack_from("<4s4H2LH", data, offset)
    if (disk or directory_disk or disk_entries != entries or entries > 2048 or directory_size > 1024 * 1024
            or directory_offset + directory_size > offset or offset + 22 + comment_size != len(data)):
        raise ValueError("Document archive directory is unsupported")


def _outline(properties):
    value = _attribute(_child(properties, "outlineLvl"), "val")
    if value is None or not re.fullmatch(r"[0-9]", value):
        return None
    return min(int(value) + 1, 6) if value != "9" else 0


def _styles(archive):
    if "word/styles.xml" not in archive.namelist():
        return {}
    root = _read_xml(archive, "word/styles.xml")
    styles = {}
    for element in root:
        if _word(element) != "style" or _attribute(element, "type") != "paragraph":
            continue
        identifier = _attribute(element, "styleId")
        if identifier:
            styles[identifier] = (_outline(_child(element, "pPr")), _attribute(_child(element, "basedOn"), "val"))
    return styles


def _heading(paragraph, styles):
    properties = _child(paragraph, "pPr")
    direct = _outline(properties)
    if direct is not None:
        return direct
    identifier = _attribute(_child(properties, "pStyle"), "val")
    seen = set()
    for _ in range(16):
        if not identifier or identifier in seen:
            break
        seen.add(identifier)
        if identifier in styles:
            level, identifier = styles[identifier]
            if level is not None:
                return level
        else:
            match = re.fullmatch(r"heading\s*([1-9])", identifier, re.IGNORECASE)
            return min(int(match[1]), 6) if match else 0
    return 0


@dataclass
class _Budget:
    chars: int = MAX_OUTPUT_CHARS
    blocks: int = MAX_OUTPUT_BLOCKS
    cells: int = MAX_OUTPUT_CELLS
    truncated: bool = False

    def text(self, value):
        result = value[:self.chars]
        self.truncated |= len(result) < len(value)
        self.chars -= len(result)
        return result


def _paragraph_text(paragraph, budget):
    pieces = []
    stack = [paragraph]
    containers = {"p", "r", "hyperlink", "smartTag", "sdt", "sdtContent", "ins", "customXml", "fldSimple"}
    while stack:
        element = stack.pop()
        name = _word(element)
        if name == "t":
            pieces.append(budget.text(element.text or ""))
        elif name in {"tab", "br", "cr", "noBreakHyphen", "softHyphen"}:
            pieces.append(budget.text({"tab": "\t", "br": "\n", "cr": "\n", "noBreakHyphen": "\u2011", "softHyphen": "\u00ad"}[name]))
        elif name in containers:
            if name == "r":
                properties = _child(element, "rPr")
                hidden = [_child(properties, flag) for flag in ("vanish", "webHidden")]
                if any(flag is not None and _attribute(flag, "val") not in {"0", "false", "off"} for flag in hidden):
                    continue
            stack.extend(reversed(element))
        # Deleted text, field instructions, drawings, alternate HTML and objects
        # are not part of the visible body text. Hyperlink labels stay plain text.
    return "".join(pieces)


def _blocks(container, styles, budget, depth=0):
    result = []
    if depth > 8:
        budget.truncated = True
        return result
    for element in container:
        name = _word(element)
        if name in {"sdt", "sdtContent", "customXml", "ins"}:
            result.extend(_blocks(element, styles, budget, depth + 1))
            continue
        if name not in {"p", "tbl"}:
            continue
        if budget.blocks <= 0:
            budget.truncated = True
            break
        budget.blocks -= 1
        if name == "p":
            result.append({"kind": "paragraph", "text": _paragraph_text(element, budget), "heading": _heading(element, styles)})
            continue
        rows = []
        for row in (child for child in element if _word(child) == "tr"):
            if len(rows) >= 200 or budget.cells <= 0:
                budget.truncated = True
                break
            cells = []
            columns = 0
            for cell in (child for child in row if _word(child) == "tc"):
                if columns >= 40 or budget.cells <= 0:
                    budget.truncated = True
                    break
                span = _attribute(_child(_child(cell, "tcPr"), "gridSpan"), "val") or "1"
                requested = int(span) if re.fullmatch(r"[0-9]{1,4}", span) else 1
                colspan = max(1, min(requested, 40 - columns))
                budget.truncated |= requested > colspan
                budget.cells -= 1
                cells.append({"colspan": colspan, "blocks": _blocks(cell, styles, budget, depth + 1)})
                columns += colspan
            header = _child(_child(row, "trPr"), "tblHeader")
            rows.append({"header": header is not None and _attribute(header, "val") not in {"0", "false", "off"}, "cells": cells})
        result.append({"kind": "table", "rows": rows})
    return result


def docx_preview(data):
    _check_directory(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(entries) > 2048 or len(set(names)) != len(names) or sum(entry.file_size for entry in entries) > 32 * 1024 * 1024:
            raise ValueError("Document archive is too large or ambiguous")
        document = _read_xml(archive, "word/document.xml")
        if _word(document) != "document":
            raise ValueError("Not a Word document")
        body = _child(document, "body")
        if body is None:
            raise ValueError("Document body is missing")
        budget = _Budget()
        blocks = _blocks(body, _styles(archive), budget)
        return {"kind": "document", "blocks": blocks, "truncated": budget.truncated}
