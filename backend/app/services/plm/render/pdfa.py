"""PDF/A-2b post-processing for ReportLab output (Phase 3B, 3-13).

PDF/A-2b conformance needs, on top of a plain PDF:
- all fonts embedded (fail-closed: a font without an embedded file raises);
- an XMP metadata packet declaring ``pdfaid:part = 2`` / ``pdfaid:conformance = B``;
- an OutputIntent with a real sRGB ICC profile (``/S /GTS_PDFA1``).

The sRGB ICC profile is generated at runtime from LittleCMS (``liblcms2``)
— the same library Pillow links against — so no binary blob is checked into
the repo. If no ICC profile can be obtained (no LittleCMS, no configured
file), the output still carries the XMP packet and embedded fonts, but is
marked ``pdfa_output_intent: False`` and the caller must warn: without the
OutputIntent it is *not* fully PDF/A-2b conformant. An explicitly configured
profile path (``FORMUNEX_PDFA_ICC_PATH``) wins over the generated one.

Post-processing uses pypdf (a declared dependency); ReportLab itself has no
PDF/A support. WeasyPrint is deliberately not used (D-2: ReportLab is the
agreed renderer).
"""
from __future__ import annotations

import ctypes
import io
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from xml.sax.saxutils import escape as xml_escape

try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )
except ImportError:  # pragma: no cover - pypdf is a declared dependency
    PdfReader = None  # type: ignore[assignment]


class PdfaError(ValueError):
    """The input PDF cannot be made PDF/A-2b conformant."""


def _xmp_packet(title: str, author: str, now: datetime) -> bytes:
    stamp = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return f"""<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="FormuNex">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">
   <pdfaid:part>2</pdfaid:part>
   <pdfaid:conformance>B</pdfaid:conformance>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{xml_escape(title)}</rdf:li></rdf:Alt></dc:title>
   <dc:creator><rdf:Seq><rdf:li>{xml_escape(author)}</rdf:li></rdf:Seq></dc:creator>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">
   <xmp:CreatorTool>FormuNex (ReportLab)</xmp:CreatorTool>
   <xmp:CreateDate>{stamp}</xmp:CreateDate>
   <xmp:ModifyDate>{stamp}</xmp:ModifyDate>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
""".encode("utf-8")


@lru_cache(maxsize=1)
def srgb_icc_bytes() -> bytes | None:
    """Return a real sRGB ICC profile, or None if none can be obtained."""
    configured = os.environ.get("FORMUNEX_PDFA_ICC_PATH")
    if configured:
        if not os.path.isfile(configured):
            raise PdfaError(f"FORMUNEX_PDFA_ICC_PATH 指向的文件不存在：{configured}")
        with open(configured, "rb") as fh:
            return fh.read()
    for lib_name in ("liblcms2.so.2", "liblcms2.so", "liblcms2.dylib", "lcms2.dll"):
        try:
            lcms = ctypes.CDLL(lib_name)
        except OSError:
            continue
        try:
            lcms.cmsCreate_sRGBProfile.restype = ctypes.c_void_p
            lcms.cmsSaveProfileToMem.restype = ctypes.c_bool
            lcms.cmsSaveProfileToMem.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            lcms.cmsCloseProfile.argtypes = [ctypes.c_void_p]
            profile = lcms.cmsCreate_sRGBProfile()
            if not profile:
                continue
            try:
                size = ctypes.c_uint32(0)
                if not lcms.cmsSaveProfileToMem(profile, None, ctypes.byref(size)) or not size.value:
                    continue
                buf = ctypes.create_string_buffer(size.value)
                if not lcms.cmsSaveProfileToMem(profile, buf, ctypes.byref(size)):
                    continue
                return buf.raw[: size.value]
            finally:
                lcms.cmsCloseProfile(profile)
        except (AttributeError, OSError, ValueError):
            continue
    return None


# The 14 standard PDF fonts: no descriptor by design, viewers must provide
# them, and PDF/A does not require them to be embedded.
_STANDARD_14 = {
    "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique",
    "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique",
    "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
    "Symbol", "ZapfDingbats",
}


def _embedded_font_files(font: dict) -> bool:
    """True if every font in a page /Font dict embeds its program."""
    for key, value in font.items():
        if key == "/BaseFont":
            continue
        obj = value.get_object()
        if not isinstance(obj, dict):
            continue
        base = str(obj.get("/BaseFont") or "").lstrip("/")
        if "+" in base:
            base = base.split("+", 1)[1]
        if base in _STANDARD_14:
            continue  # standard 14: exempt from the embedding requirement
        if obj.get("/Subtype") == "/Type0":
            descendants = obj.get("/DescendantFonts")
            if descendants is None:
                return False
            for desc in descendants:
                desc_obj = desc.get_object()
                descriptor = desc_obj.get("/FontDescriptor") if isinstance(desc_obj, dict) else None
                if descriptor is None:
                    return False
                descriptor = descriptor.get_object() if hasattr(descriptor, "get_object") else descriptor
                if not any(k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3")):
                    return False
        else:
            descriptor = obj.get("/FontDescriptor")
            if descriptor is None:
                return False
            descriptor = descriptor.get_object() if hasattr(descriptor, "get_object") else descriptor
            if not any(k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3")):
                return False
    return True


def _check_fonts_embedded(reader: Any) -> None:
    for page in reader.pages:
        resources = page.get("/Resources")
        if resources is None:
            continue
        fonts = resources.get("/Font")
        if fonts is None:
            continue
        if not _embedded_font_files(fonts):
            raise PdfaError("PDF/A-2b 要求全部字体嵌入：文档中存在未嵌入字体的字形")


def apply_pdfa_2b(pdf_bytes: bytes, *, title: str = "", author: str = "") -> tuple[bytes, dict]:
    """Convert ReportLab output to PDF/A-2b (see module docstring).

    Returns (pdf_bytes, info). ``info["pdfa_output_intent"]`` is False when
    no sRGB ICC profile could be obtained — the output is then only
    PDF/A-2b-*ready*, not fully conformant, and the caller must warn.
    """
    if PdfReader is None:  # pragma: no cover
        raise PdfaError("PDF/A-2b 需要 pypdf，未安装")
    reader = PdfReader(io.BytesIO(pdf_bytes))
    _check_fonts_embedded(reader)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    now = datetime.now(timezone.utc)
    stamp = now.strftime("D:%Y%m%d%H%M%S+00'00'")
    writer.add_metadata({
        "/Title": title,
        "/Author": author,
        "/Subject": "PDF/A-2b",
        "/Keywords": "PDF/A-2b",
        "/Creator": "FormuNex (ReportLab)",
        "/Producer": "FormuNex (ReportLab)",
        "/Trapped": "/False",
        "/CreationDate": stamp,
        "/ModDate": stamp,
    })
    xmp = DecodedStreamObject()
    xmp.set_data(_xmp_packet(title, author, now))
    xmp.update({NameObject("/Type"): NameObject("/Metadata"), NameObject("/Subtype"): NameObject("/XML")})
    xmp_ref = writer._add_object(xmp)
    writer._root_object[NameObject("/Metadata")] = xmp_ref
    icc = srgb_icc_bytes()
    if icc is not None:
        icc_stream = DecodedStreamObject()
        icc_stream.set_data(icc)
        icc_stream.update({NameObject("/N"): NumberObject(3)})
        icc_ref = writer._add_object(icc_stream)
        intent = DictionaryObject({
            NameObject("/Type"): NameObject("/OutputIntent"),
            NameObject("/S"): NameObject("/GTS_PDFA1"),
            NameObject("/OutputConditionIdentifier"): TextStringObject("sRGB IEC61966-2.1"),
            NameObject("/RegistryName"): TextStringObject("http://www.color.org"),
            NameObject("/DestOutputProfile"): icc_ref,
        })
        writer._root_object[NameObject("/OutputIntents")] = ArrayObject([writer._add_object(intent)])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), {"pdfa": True, "pdfa_part": 2, "pdfa_conformance": "B",
                            "pdfa_output_intent": icc is not None, "pdfa_fonts_embedded": True}
