"""What the exported DOCX contains.

Package checks (errors: things that stop Word opening the file), a Word-compatibility lint
(warnings), and the words with their effective formatting (document defaults, paragraph style,
character style, direct formatting), split into sections. PdfCraft writes one section per source
page, so sections line up with the PDF's pages."""

import collections
import posixpath
import zipfile
import xml.etree.ElementTree as ET

from .pdfref import Word, font_family, normalize, parse_color

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
V = "urn:schemas-microsoft-com:vml"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"

ALTERNATE = f"{{{MC}}}AlternateContent"
CHOICE = f"{{{MC}}}Choice"
FALLBACK = f"{{{MC}}}Fallback"
DEFAULT_SIZE = 10.0  # Word's size when nothing in the style hierarchy sets one


def w(tag):
    return f"{{{W}}}{tag}"


def wattr(el, name):
    return None if el is None else el.get(w(name))


def outer(el, tag):
    """Descendants with `tag`, without looking inside a match or inside mc:Fallback."""
    for child in el:
        if child.tag == FALLBACK:
            continue
        if child.tag == tag:
            yield child
        else:
            yield from outer(child, tag)


def choice(el):
    return el.find(CHOICE) if el.tag == ALTERNATE else None


def on(el):
    """A toggle property (w:b, w:i): present and not switched off."""
    return el is not None and wattr(el, "val") not in ("0", "false", "off")


def rpr_props(rpr):
    props = {}
    if rpr is None:
        return props
    for tag in ("b", "i"):
        el = rpr.find(w(tag))
        if el is not None:
            props[tag] = on(el)
    sz = rpr.find(w("sz"))
    if sz is not None:
        try:
            props["sz"] = int(wattr(sz, "val")) / 2
        except (TypeError, ValueError):
            pass
    color = rpr.find(w("color"))
    if color is not None:
        props["color"] = None if wattr(color, "val") in (None, "auto") else parse_color(wattr(color, "val"))
    fonts = rpr.find(w("rFonts"))
    if fonts is not None and (wattr(fonts, "ascii") or wattr(fonts, "hAnsi")):
        props["font"] = font_family(wattr(fonts, "ascii") or wattr(fonts, "hAnsi"))
    return props


class Section:
    def __init__(self):
        self.words = []
        self.size = None  # (width, height) in points
        self.paragraphs = self.tables = self.textboxes = self.images = self.headings = 0

    def summary(self):
        return {"size": self.size, "words": len(self.words), "paragraphs": self.paragraphs, "tables": self.tables,
                "textboxes": self.textboxes, "images": self.images, "headings": self.headings}


class Package:
    """Reads the zip, checks it, and walks the main document."""

    def __init__(self, path):
        self.errors = []
        self.lint = collections.Counter()
        self.sections = []
        self.parts = {}
        self.styles = {}
        self.defaults = {}
        self.default_para = None
        self.doc_rels = {}
        self.doc_pr_ids = collections.Counter()
        self._chars = []
        self._section = Section()
        self._read(path)

    # -- package ---------------------------------------------------------------------------------

    def _read(self, path):
        try:
            z = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as e:
            self.errors.append(f"not a zip package: {e}")
            return
        with z:
            bad = z.testzip()
            if bad:
                self.errors.append(f"corrupt zip entry {bad}")
                return
            names = {n for n in z.namelist() if not n.endswith("/")}
            for name in sorted(names):
                if name.endswith((".xml", ".rels")):
                    try:
                        self.parts[name] = ET.fromstring(z.read(name))
                    except ET.ParseError as e:
                        self.errors.append(f"{name}: not well-formed XML ({e})")
            self.media = sum(1 for n in names if "/media/" in n)
        self._check_content_types(names)
        rels = self._check_relationships(names)
        main = next((t for typ, t in rels.get("", {}).values() if typ.endswith("/officeDocument")), None)
        if main is None or main not in self.parts:
            self.errors.append("no main document (the officeDocument relationship is missing or broken)")
            return
        self.doc_rels = rels.get(main, {})
        styles = next((t for typ, t in self.doc_rels.values() if typ.endswith("/styles")), None)
        if styles in self.parts:
            self._read_styles(self.parts[styles])
        body = self.parts[main].find(w("body"))
        if body is None:
            self.errors.append(f"{main} has no w:body")
            return
        self._block_container(body, in_body=True)
        if len(body) and body[-1].tag != w("sectPr"):
            self.lint["the body does not end with w:sectPr"] += 1
        self._flush()
        if self._section.words or self._section.paragraphs or not self.sections:
            self.sections.append(self._section)
        for pid, n in self.doc_pr_ids.items():
            if n > 1:
                self.lint[f"duplicate drawing id {pid} (Word may call the file corrupt)"] += n - 1

    def _check_content_types(self, names):
        types = self.parts.get("[Content_Types].xml")
        if types is None:
            self.errors.append("[Content_Types].xml is missing")
            return
        defaults = {d.get("Extension", "").lower() for d in types.iter(f"{{{CT}}}Default")}
        overrides = {o.get("PartName", "") for o in types.iter(f"{{{CT}}}Override")}
        for name in sorted(names - {"[Content_Types].xml"}):
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if f"/{name}" not in overrides and ext not in defaults:
                self.errors.append(f"{name} has no content type")
        for part in sorted(overrides):
            if part.lstrip("/") not in names:
                self.lint[f"content type override for missing part {part}"] += 1

    def _check_relationships(self, names):
        """{source part ('' for the package): {rId: (type, target part)}}; broken internal targets are errors."""
        rels = {}
        for name in sorted(self.parts):
            if not name.endswith(".rels"):
                continue
            folder, _, file = name.rpartition("_rels/")
            source = folder + file[: -len(".rels")]
            base = posixpath.dirname(source) if source else ""
            entries = rels.setdefault(source, {})
            for rel in self.parts[name].iter(f"{{{PKG_REL}}}Relationship"):
                target = rel.get("Target", "")
                if rel.get("TargetMode") == "External":
                    continue
                part = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(base, target))
                entries[rel.get("Id")] = (rel.get("Type", ""), part)
                if part not in names:
                    self.errors.append(f"{name}: {rel.get('Id')} points at missing part {part}")
        return rels

    def _read_styles(self, root):
        rpr = root.find(f"{w('docDefaults')}/{w('rPrDefault')}/{w('rPr')}")
        self.defaults = rpr_props(rpr)
        for st in root.iter(w("style")):
            sid = wattr(st, "styleId")
            ppr = st.find(w("pPr"))
            outline = wattr(ppr.find(w("outlineLvl")) if ppr is not None else None, "val")
            self.styles[sid] = {
                "type": wattr(st, "type"),
                "name": (wattr(st.find(w("name")), "val") or "").lower(),
                "based_on": wattr(st.find(w("basedOn")), "val"),
                "rpr": rpr_props(st.find(w("rPr"))),
                "outline": outline,
            }
            if wattr(st, "type") == "paragraph" and wattr(st, "default") in ("1", "true", "on"):
                self.default_para = sid

    def _style_props(self, sid, seen=()):
        st = self.styles.get(sid)
        if st is None or sid in seen:
            return {}
        props = dict(self._style_props(st["based_on"], (*seen, sid)))
        props.update(st["rpr"])
        return props

    def _is_heading(self, sid, ppr):
        if ppr is not None and ppr.find(w("outlineLvl")) is not None:
            return wattr(ppr.find(w("outlineLvl")), "val") not in (None, "9")
        seen = set()
        while sid in self.styles and sid not in seen:
            st = self.styles[sid]
            if st["name"].startswith("heading") or st["name"] == "title" or st["outline"] not in (None, "9"):
                return True
            seen.add(sid)
            sid = st["based_on"]
        return False

    # -- body ------------------------------------------------------------------------------------

    def _block_container(self, el, in_body=False):
        for child in el:
            tag = child.tag
            if tag == w("p"):
                self._paragraph(child, in_body)
            elif tag == w("tbl"):
                self._table(child)
            elif tag == w("sdt"):
                content = child.find(w("sdtContent"))
                if content is not None:
                    self._block_container(content, in_body)
            elif tag == w("customXml"):
                self._block_container(child, in_body)
            elif tag == ALTERNATE and choice(child) is not None:
                self._block_container(choice(child), in_body)
            elif tag == w("sectPr") and in_body:
                self._end_section(child)

    def _paragraph(self, p, in_body):
        ppr = p.find(w("pPr"))
        sid = wattr(ppr.find(w("pStyle")) if ppr is not None else None, "val") or self.default_para
        self._section.paragraphs += 1
        if self._is_heading(sid, ppr):
            self._section.headings += 1
        self._flush()
        self._inline(p, sid)
        self._flush()
        sect = ppr.find(w("sectPr")) if ppr is not None else None
        if in_body and sect is not None:
            self._end_section(sect)

    def _inline(self, el, sid):
        for child in el:
            tag = child.tag
            if tag == w("r"):
                self._run(child, sid)
            elif tag in (w("hyperlink"), w("smartTag"), w("ins"), w("customXml"), w("fldSimple"), w("moveTo")):
                self._inline(child, sid)
            elif tag == w("sdt"):
                content = child.find(w("sdtContent"))
                if content is not None:
                    self._inline(content, sid)
            elif tag == ALTERNATE and choice(child) is not None:
                self._inline(choice(child), sid)

    def _run(self, r, sid):
        rpr = r.find(w("rPr"))
        rstyle = wattr(rpr.find(w("rStyle")) if rpr is not None else None, "val")
        props = {"b": False, "i": False, "sz": DEFAULT_SIZE, "color": None, "font": None}
        props.update(self.defaults)
        props.update(self._style_props(sid))
        props.update(self._style_props(rstyle))
        props.update(rpr_props(rpr))
        self._run_content(r, props)

    def _run_content(self, el, props):
        for child in el:
            tag = child.tag
            if tag == w("t"):
                self._chars.extend((c, props) for c in child.text or "")
            elif tag in (w("tab"), w("br"), w("cr"), w("ptab")):
                self._chars.append((" ", props))
            elif tag == w("noBreakHyphen"):
                self._chars.append(("-", props))
            elif tag == w("drawing"):
                self._drawing(child)
            elif tag in (w("pict"), w("object")):
                self._vml(child)
            elif tag == ALTERNATE and choice(child) is not None:
                self._run_content(choice(child), props)

    def _image_ref(self, rid):
        if not rid:
            return
        if rid not in self.doc_rels:
            self.errors.append(f"image reference {rid} has no relationship")
        self._section.images += 1

    def _drawing(self, el):
        for blip in outer(el, f"{{{A}}}blip"):
            self._image_ref(blip.get(f"{{{R}}}embed"))
        for pr in outer(el, f"{{{WP}}}docPr"):
            self.doc_pr_ids[pr.get("id")] += 1
        self._textboxes(el)

    def _vml(self, el):
        for data in outer(el, f"{{{V}}}imagedata"):
            self._image_ref(data.get(f"{{{R}}}id"))
        self._textboxes(el)

    def _textboxes(self, el):
        for box in outer(el, w("txbxContent")):
            self._flush()
            self._section.textboxes += 1
            self._block_container(box)
            self._flush()

    def _table(self, tbl):
        self._section.tables += 1
        grid = tbl.find(w("tblGrid"))
        columns = len(grid.findall(w("gridCol"))) if grid is not None else 0
        if grid is None:
            self.lint["table without w:tblGrid"] += 1
        rows = tbl.findall(w("tr"))
        if not rows:
            self.lint["table without rows (Word refuses the file)"] += 1
        for tr in rows:
            cells = tr.findall(w("tc"))
            if not cells:
                self.lint["table row without cells (Word refuses the file)"] += 1
            spans = 0
            for tc in cells:
                try:
                    spans += int(wattr(tc.find(f"{w('tcPr')}/{w('gridSpan')}"), "val") or 1)
                except ValueError:
                    spans += 1
                blocks = [c for c in tc if c.tag != w("tcPr")]
                if not blocks or blocks[-1].tag != w("p"):
                    self.lint["table cell not ending in a paragraph (Word refuses the file)"] += 1
                self._flush()
                self._block_container(tc)
                self._flush()
            if columns and spans > columns:
                self.lint["table row wider than its grid"] += 1

    def _end_section(self, sect):
        self._flush()
        size = sect.find(w("pgSz"))
        try:
            width, height = int(wattr(size, "w")), int(wattr(size, "h"))
            self._section.size = (width / 20, height / 20)
            if not (144 <= width <= 31680 and 144 <= height <= 31680):
                self.lint["page size outside Word's 0.1-22 inch range"] += 1
        except (TypeError, ValueError):
            pass
        self.sections.append(self._section)
        self._section = Section()

    def _flush(self):
        current = []
        for ch in self._chars:
            if ch[0].isspace():
                self._emit(current)
                current = []
            else:
                current.append(ch)
        self._emit(current)
        self._chars = []

    def _emit(self, chars):
        if not chars:
            return
        text = normalize("".join(c for c, _ in chars))
        bold = sum(1 for _, p in chars if p["b"]) * 2 >= len(chars)
        italic = sum(1 for _, p in chars if p["i"]) * 2 >= len(chars)
        size = collections.Counter(p["sz"] for _, p in chars).most_common(1)[0][0]
        color = chars[0][1]["color"]
        font = chars[0][1]["font"]
        for part in text.split():
            self._section.words.append(Word(part, bold, italic, size, color, font))


def read_docx(path):
    pkg = Package(path)
    totals = collections.Counter()
    for s in pkg.sections:
        totals.update({k: v for k, v in s.summary().items() if isinstance(v, int)})
    return {
        "errors": pkg.errors,
        "warnings": [f"{msg} (×{n})" if n > 1 else msg for msg, n in sorted(pkg.lint.items())],
        "sections": pkg.sections,
        "totals": dict(totals),
        "media": getattr(pkg, "media", 0),
    }
