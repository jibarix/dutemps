"""A minimal PDF writer: text, wrapped paragraphs, lines and filled shapes on
US Letter pages, in the 14 standard fonts' Helvetica family. Standard library
only (BRIEF D1).

Coordinates are points from the TOP-left corner, y growing downwards, because
that is how a layout is written; the flip to PDF's bottom-left origin happens
here and nowhere else. Text is encoded as WinAnsi (Python's cp1252), which
covers the accented letters and typographic punctuation the invoices use; a
character outside it is an error rather than a silent '?', since it would
otherwise print wrong on a client document.

Output is deterministic: the same document gives the same bytes, so a
re-rendered invoice can be compared with the one that was sent."""
import unicodedata
import zlib

LETTER = (612.0, 792.0)

FONTS = {"R": "Helvetica", "B": "Helvetica-Bold",
         "I": "Helvetica-Oblique", "BI": "Helvetica-BoldOblique"}

# Advance widths (1/1000 em) for codes 32..126, from Adobe's Helvetica and
# Helvetica-Bold AFM files. The oblique faces share their upright widths.
_REG = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333,
        278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278,
        584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278,
        500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944,
        667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556,
        278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500,
        278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]
_BOLD = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333,
         278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333,
         584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278,
         556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944,
         667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556,
         333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556,
         333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584]
# The non-ASCII characters that are not a plain letter plus an accent.
_EXTRA = {  # char: (regular, bold)
    "\u2014": (1000, 1000), "\u2013": (556, 556), "\u2019": (222, 278),
    "\u2018": (222, 278), "\u201c": (333, 500), "\u201d": (333, 500),
    "\u2022": (350, 350), "\u2026": (1000, 1000), "\u00a0": (278, 278),
    "\u00b0": (400, 400), "\u00d7": (584, 584), "\u00b7": (278, 278),
    "\u20ac": (556, 556), "\u00a9": (737, 737),
}


def _char_width(ch, bold):
    o = ord(ch)
    if 32 <= o <= 126:
        return (_BOLD if bold else _REG)[o - 32]
    if ch in _EXTRA:
        return _EXTRA[ch][bold]
    base = unicodedata.normalize("NFD", ch)[0]   # ó -> o: accents keep the width
    if base != ch and 32 <= ord(base) <= 126:
        return _char_width(base, bold)
    raise ValueError(f"no Helvetica width for {ch!r} (U+{o:04X})")


def width(s, font="R", size=10):
    bold = font in ("B", "BI")
    return sum(_char_width(c, bold) for c in s) * size / 1000.0


def _encode(s):
    try:
        b = s.encode("cp1252")
    except UnicodeEncodeError as e:
        raise ValueError(f"character {s[e.start]!r} cannot be printed in "
                         f"the PDF's standard fonts: {s!r}") from None
    return (b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
            .replace(b"\r", b"\\r").replace(b"\n", b"\\n"))


def wrap(s, w, font="R", size=10):
    """Greedy word wrap to width w. Explicit newlines are kept. A single word
    wider than w is broken by character rather than allowed to overflow."""
    out = []
    for para in s.split("\n"):
        line = ""
        for word in para.split(" "):
            cand = word if not line else line + " " + word
            if width(cand, font, size) <= w:
                line = cand
                continue
            if line:
                out.append(line)
            while width(word, font, size) > w:
                n = len(word)
                while n > 1 and width(word[:n], font, size) > w:
                    n -= 1
                out.append(word[:n])
                word = word[n:]
            line = word
        out.append(line)
    return out


def rgb(c):
    """'#1e3a8a' or (r, g, b) in 0..1 -> (r, g, b)."""
    if isinstance(c, str):
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return c


def _n(x):
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


class Page:
    def __init__(self, size=LETTER):
        self.w, self.h = size
        self.ops = []

    def _y(self, y):
        return self.h - y

    def _fill(self, c):
        self.ops.append("%s %s %s rg" % tuple(_n(v) for v in rgb(c)))

    def _stroke(self, c):
        self.ops.append("%s %s %s RG" % tuple(_n(v) for v in rgb(c)))

    def text(self, x, y, s, font="R", size=10, color="#000000", align="left"):
        """y is the baseline."""
        if not s:
            return
        if align == "right":
            x -= width(s, font, size)
        elif align == "center":
            x -= width(s, font, size) / 2
        self._fill(color)
        self.ops.append(b"BT /F%s %s Tf %s %s Td (" % (
            font.encode(), _n(size).encode(), _n(x).encode(), _n(self._y(y)).encode())
            + _encode(s) + b") Tj ET")

    def stamp(self, s, font="B", size=110, color="#F1F2F4", angle=35):
        """Large rotated text centred on the page, drawn UNDER everything
        already on it (a watermark; there is no transparency here)."""
        import math
        a = math.radians(angle)
        c, si = math.cos(a), math.sin(a)
        half = width(s, font, size) / 2
        x = self.w / 2 - half * c + size * 0.35 * si
        y = self.h / 2 - half * si - size * 0.35 * c
        r, g, b = rgb(color)
        op = (b"%s %s %s rg BT /F%s %s Tf %s %s %s %s %s %s Tm (" % tuple(
            v.encode() for v in (_n(r), _n(g), _n(b), font, _n(size), _n(c), _n(si),
                                 _n(-si), _n(c), _n(x), _n(y))) + _encode(s) + b") Tj ET")
        self.ops.insert(0, op)

    def paragraph(self, x, y, w, s, font="R", size=10, leading=None,
                  color="#000000", align="left"):
        """Wrapped text whose first baseline is y; returns the y below it."""
        leading = leading or size * 1.3
        for ln in wrap(s, w, font, size):
            ax = x + w if align == "right" else x
            self.text(ax, y, ln, font, size, color, align)
            y += leading
        return y

    def line(self, x1, y1, x2, y2, color="#000000", lw=0.5):
        self._stroke(color)
        self.ops.append(f"{_n(lw)} w {_n(x1)} {_n(self._y(y1))} m "
                        f"{_n(x2)} {_n(self._y(y2))} l S")

    def rect(self, x, y, w, h, fill=None, stroke=None, lw=0.5, r=0):
        """Top-left x, y. r > 0 rounds the corners."""
        if fill is None and stroke is None:
            return
        if fill is not None:
            self._fill(fill)
        if stroke is not None:
            self._stroke(stroke)
            self.ops.append(f"{_n(lw)} w")
        X, Y = x, self._y(y + h)              # bottom-left in PDF space
        if r <= 0:
            self.ops.append(f"{_n(X)} {_n(Y)} {_n(w)} {_n(h)} re")
        else:
            r = min(r, w / 2, h / 2)
            k = r * 0.5523                    # Bezier quarter-circle constant
            R, T = X + w, Y + h
            self.ops.append(
                f"{_n(X + r)} {_n(Y)} m {_n(R - r)} {_n(Y)} l "
                f"{_n(R - r + k)} {_n(Y)} {_n(R)} {_n(Y + r - k)} {_n(R)} {_n(Y + r)} c "
                f"{_n(R)} {_n(T - r)} l "
                f"{_n(R)} {_n(T - r + k)} {_n(R - r + k)} {_n(T)} {_n(R - r)} {_n(T)} c "
                f"{_n(X + r)} {_n(T)} l "
                f"{_n(X + r - k)} {_n(T)} {_n(X)} {_n(T - r + k)} {_n(X)} {_n(T - r)} c "
                f"{_n(X)} {_n(Y + r)} l "
                f"{_n(X)} {_n(Y + r - k)} {_n(X + r - k)} {_n(Y)} {_n(X + r)} {_n(Y)} c h")
        self.ops.append({(True, False): "f", (False, True): "S",
                         (True, True): "B"}[(fill is not None, stroke is not None)])

    def content(self):
        return b"\n".join(o if isinstance(o, bytes) else o.encode("ascii")
                          for o in self.ops)


class Document:
    def __init__(self, title="", author="", date=None):
        self.pages = []
        self.title, self.author = title, author
        self.date = date            # 'YYYYMMDD'; fixed, so output is reproducible

    def page(self, size=LETTER):
        p = Page(size)
        self.pages.append(p)
        return p

    def render(self):
        objs = []                               # index i -> object number i + 1

        def add(body):
            objs.append(body)
            return len(objs)

        def ref(n):
            return f"{n} 0 R".encode()

        def lit(s):
            return b"(" + _encode(s) + b")"

        catalog = add(None)
        pages = add(None)
        font_ids = {k: add(b"<< /Type /Font /Subtype /Type1 /BaseFont /" + v.encode()
                           + b" /Encoding /WinAnsiEncoding >>")
                    for k, v in FONTS.items()}
        fonts = b" ".join(b"/F" + k.encode() + b" " + ref(n) for k, n in font_ids.items())
        kids = []
        for p in self.pages:
            data = zlib.compress(p.content(), 9)
            cs = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data)
                     + data + b"\nendstream")
            kids.append(add(b"<< /Type /Page /Parent " + ref(pages)
                            + b" /MediaBox [0 0 %s %s]" % (_n(p.w).encode(), _n(p.h).encode())
                            + b" /Resources << /Font << " + fonts + b" >> >>"
                            + b" /Contents " + ref(cs) + b" >>"))
        objs[catalog - 1] = b"<< /Type /Catalog /Pages " + ref(pages) + b" >>"
        objs[pages - 1] = (b"<< /Type /Pages /Count %d /Kids [" % len(kids)
                           + b" ".join(ref(k) for k in kids) + b"] >>")
        info = b"<< /Producer (DuTemps)"
        if self.title:
            info += b" /Title " + lit(self.title)
        if self.author:
            info += b" /Author " + lit(self.author)
        if self.date:
            info += b" /CreationDate (D:" + self.date.encode() + b"000000)"
        info = add(info + b" >>")

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        out += (b"trailer\n<< /Size %d /Root " % (len(objs) + 1) + ref(catalog)
                + b" /Info " + ref(info) + b" >>\nstartxref\n%d\n%%%%EOF\n" % xref)
        return bytes(out)
