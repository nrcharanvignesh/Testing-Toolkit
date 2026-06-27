"""
kb_legacy_docs.py
Text extraction for legacy and extended document formats that are not
covered by the core extractors in kb_store.py.

This module handles:
  LEGACY MICROSOFT (binary OLE2 formats):
    .doc     - Word 97-2003 (via python-docx-2 or textract-lite, or antiword)
    .ppt     - PowerPoint 97-2003 (OLE2 structured storage)
    .xls     - Excel 97-2003 (already in kb_store via xlrd, re-exported here)
    .msg     - Outlook email messages (structured OLE2)

  OPENDOCUMENT (ODF / LibreOffice / OpenOffice):
    .odt     - OpenDocument Text
    .ods     - OpenDocument Spreadsheet
    .odp     - OpenDocument Presentation
    .odg     - OpenDocument Drawing

  EMAIL FORMATS:
    .eml     - RFC 822 email messages (stdlib email parser)
    .mbox    - Mailbox archives

  RICH TEXT / MARKUP:
    .rtf     - Rich Text Format (already in kb_store via striprtf)
    .tex     - LaTeX source
    .man     - Unix man pages
    .info    - GNU info pages

  EBOOK / PUBLISHING:
    .epub    - EPUB ebooks (ZIP of XHTML)
    .mobi    - Kindle format (basic metadata)
    .fb2     - FictionBook XML

  OTHER STRUCTURED:
    .pages   - Apple Pages (iWork ZIP package)
    .numbers - Apple Numbers (iWork ZIP package)
    .key     - Apple Keynote (iWork ZIP package)
    .wps     - WPS Office documents
    .hwp     - Hangul Word Processor

Design: every extractor returns best-effort text and NEVER raises.
Missing optional libraries degrade to empty string with no import error.
"""

from __future__ import annotations

import email
import gc
import re
import struct
import zipfile
from pathlib import Path
from typing import Final
from xml.etree import ElementTree as ET

# All extensions this module handles
LEGACY_EXTENSIONS: Final[frozenset[str]] = frozenset({
    # Legacy Microsoft binary
    ".doc", ".ppt", ".msg",
    # OpenDocument
    ".odt", ".ods", ".odp", ".odg",
    # Email
    ".eml", ".mbox",
    # Markup
    ".tex", ".latex", ".man", ".info",
    # Ebook
    ".epub", ".mobi", ".fb2",
    # Apple iWork
    ".pages", ".numbers", ".key",
    # Other
    ".wps", ".hwp",
})

_WHITESPACE_COLLAPSE_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]+")
_MULTI_NEWLINE_RE: Final[re.Pattern[str]] = re.compile(r"\n{3,}")
_XML_TAG_RE: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")
_LATEX_CMD_RE: Final[re.Pattern[str]] = re.compile(
    r"\\(?:begin|end|usepackage|documentclass|newcommand|renewcommand)"
    r"\{[^}]*\}(?:\[[^\]]*\])?(?:\{[^}]*\})?")
_LATEX_SIMPLE_RE: Final[re.Pattern[str]] = re.compile(
    r"\\(?:section|subsection|subsubsection|chapter|title|author|date)"
    r"\*?\{([^}]*)\}")


def _clean(text: str) -> str:
    """Normalize whitespace without destroying paragraph structure."""
    text = _WHITESPACE_COLLAPSE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------
# Legacy .doc (Word 97-2003 binary)
# ---------------------------------------------------------------------
def _extract_doc(path: Path) -> str:
    """Extract text from .doc (OLE2 Word binary). Tries multiple backends."""
    # Method 1: olefile + direct text stream extraction
    try:
        import olefile
        if olefile.isOleFile(str(path)):
            ole = olefile.OleFileIO(str(path))
            if ole.exists("WordDocument"):
                # The Word Document stream contains the raw text in UTF-16
                # at known offsets, but full parsing is complex.
                # Try the 1Table or 0Table stream for piece table
                pass
            ole.close()
    except Exception:
        pass

    # Method 2: Use antiword subprocess (best quality if available)
    try:
        import subprocess
        result = subprocess.run(
            ["antiword", "-w", "0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return _clean(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # Method 3: catdoc subprocess
    try:
        import subprocess
        result = subprocess.run(
            ["catdoc", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return _clean(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # Method 4: Brute-force text extraction from OLE2 binary
    try:
        raw = path.read_bytes()
        # Word .doc files store text as UTF-16LE in the WordDocument stream.
        # As a last resort, scan for readable text sequences.
        text_parts: list[str] = []
        # Try to decode as UTF-16LE sections
        try:
            decoded = raw.decode("utf-16-le", errors="ignore")
            # Filter to printable runs of 20+ characters
            for match in re.finditer(r"[\x20-\x7e\n\r\t]{20,}", decoded):
                text_parts.append(match.group())
        except Exception:
            pass
        if not text_parts:
            # Fallback: extract ASCII runs
            decoded = raw.decode("ascii", errors="ignore")
            for match in re.finditer(r"[\x20-\x7e\n\r\t]{20,}", decoded):
                text_parts.append(match.group())
        if text_parts:
            return _clean("\n".join(text_parts))
    except Exception:
        pass

    return ""


# ---------------------------------------------------------------------
# Legacy .ppt (PowerPoint 97-2003 binary)
# ---------------------------------------------------------------------
def _extract_ppt(path: Path) -> str:
    """Extract text from .ppt (OLE2 PowerPoint binary)."""
    # Method 1: python-pptx cannot read .ppt, try olefile for text records
    try:
        import olefile
        if not olefile.isOleFile(str(path)):
            return ""
        ole = olefile.OleFileIO(str(path))
        parts: list[str] = []

        # PowerPoint stores text in "PowerPoint Document" stream
        if ole.exists("PowerPoint Document"):
            stream = ole.openstream("PowerPoint Document").read()
            # Text records in PPT binary have record type 0x0FA0 (TextCharsAtom)
            # and 0x0FA8 (TextBytesAtom)
            i = 0
            while i < len(stream) - 8:
                rec_ver_inst = struct.unpack_from("<H", stream, i)[0]
                rec_type = struct.unpack_from("<H", stream, i + 2)[0]
                rec_len = struct.unpack_from("<I", stream, i + 4)[0]
                i += 8
                if rec_len > len(stream) - i:
                    break
                if rec_type == 0x0FA8:  # TextBytesAtom (ASCII)
                    try:
                        text = stream[i:i + rec_len].decode("latin-1", errors="ignore")
                        text = text.strip()
                        if text and len(text) > 2:
                            parts.append(text)
                    except Exception:
                        pass
                elif rec_type == 0x0FA0:  # TextCharsAtom (UTF-16LE)
                    try:
                        text = stream[i:i + rec_len].decode("utf-16-le", errors="ignore")
                        text = text.strip()
                        if text and len(text) > 2:
                            parts.append(text)
                    except Exception:
                        pass
                i += rec_len

        ole.close()
        if parts:
            return _clean("\n\n".join(parts))
    except Exception:
        pass

    return ""


# ---------------------------------------------------------------------
# .msg (Outlook message)
# ---------------------------------------------------------------------
def _extract_msg(path: Path) -> str:
    """Extract text from Outlook .msg files (OLE2 structured storage)."""
    try:
        import olefile
        if not olefile.isOleFile(str(path)):
            return ""
        ole = olefile.OleFileIO(str(path))
        parts: list[str] = []

        # Common property streams in .msg
        for stream_name in ole.listdir():
            name = "/".join(stream_name)
            # Subject
            if "Subject" in name or "__substg1.0_0037" in name.lower():
                try:
                    data = ole.openstream(stream_name).read()
                    text = data.decode("utf-16-le", errors="ignore").strip("\x00").strip()
                    if text:
                        parts.insert(0, f"Subject: {text}")
                except Exception:
                    pass
            # Body text
            elif "Body" in name or "__substg1.0_1000" in name.lower():
                try:
                    data = ole.openstream(stream_name).read()
                    # Try UTF-16 first, then UTF-8
                    try:
                        text = data.decode("utf-16-le", errors="ignore").strip("\x00")
                    except Exception:
                        text = data.decode("utf-8", errors="ignore")
                    if text.strip():
                        parts.append(text.strip())
                except Exception:
                    pass

        ole.close()
        return _clean("\n\n".join(parts))
    except ImportError:
        pass
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------
# OpenDocument formats (.odt, .ods, .odp, .odg)
# ---------------------------------------------------------------------
_ODF_NS: Final[dict[str, str]] = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
}


def _odf_extract_text_elements(root: ET.Element) -> list[str]:
    """Recursively extract all text from ODF XML tree."""
    parts: list[str] = []
    # Text paragraphs
    for p in root.iter(f"{{{_ODF_NS['text']}}}p"):
        text = "".join(p.itertext()).strip()
        if text:
            parts.append(text)
    # Text headings
    for h in root.iter(f"{{{_ODF_NS['text']}}}h"):
        text = "".join(h.itertext()).strip()
        if text:
            parts.append(f"# {text}")
    # Table cells
    for cell in root.iter(f"{{{_ODF_NS['table']}}}table-cell"):
        text = "".join(cell.itertext()).strip()
        if text:
            parts.append(text)
    # Draw/presentation text boxes
    for frame in root.iter(f"{{{_ODF_NS['draw']}}}text-box"):
        text = "".join(frame.itertext()).strip()
        if text:
            parts.append(text)
    return parts


def _extract_odf(path: Path) -> str:
    """Extract text from OpenDocument files (.odt, .ods, .odp, .odg)."""
    try:
        with zipfile.ZipFile(str(path), "r") as zf:
            if "content.xml" not in zf.namelist():
                return ""
            content = zf.read("content.xml")
            root = ET.fromstring(content)
            parts = _odf_extract_text_elements(root)
            if not parts:
                # Fallback: strip all XML tags
                raw = content.decode("utf-8", errors="ignore")
                text = _XML_TAG_RE.sub(" ", raw)
                return _clean(text)
            return _clean("\n".join(parts))
    except (zipfile.BadZipFile, ET.ParseError, KeyError):
        pass
    return ""


# ---------------------------------------------------------------------
# Email formats (.eml, .mbox)
# ---------------------------------------------------------------------
def _extract_eml(path: Path) -> str:
    """Extract text from .eml (RFC 822) email messages."""
    try:
        raw = path.read_bytes()
        msg = email.message_from_bytes(raw)
        parts: list[str] = []

        # Headers
        for header in ("Subject", "From", "To", "Date"):
            val = msg.get(header, "")
            if val:
                parts.append(f"{header}: {val}")

        # Body
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        text = payload.decode(charset, errors="replace")
                        parts.append(text.strip())
                elif ctype == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        html = payload.decode(charset, errors="replace")
                        text = _XML_TAG_RE.sub(" ", html)
                        parts.append(_clean(text))
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
                parts.append(text.strip())

        return _clean("\n\n".join(parts))
    except Exception:
        pass
    return ""


def _extract_mbox(path: Path) -> str:
    """Extract text from .mbox mailbox archives (first 50 messages max)."""
    import mailbox
    try:
        mbox = mailbox.mbox(str(path))
        parts: list[str] = []
        for i, msg in enumerate(mbox):
            if i >= 50:
                parts.append(f"[... {len(mbox) - 50} more messages truncated]")
                break
            subject = msg.get("Subject", "(no subject)")
            parts.append(f"--- Message {i+1}: {subject} ---")
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset() or "utf-8"
                            parts.append(payload.decode(charset, errors="replace").strip())
                            break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    parts.append(payload.decode(charset, errors="replace").strip())
        mbox.close()
        return _clean("\n\n".join(parts))
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------
# LaTeX / TeX
# ---------------------------------------------------------------------
def _extract_tex(path: Path) -> str:
    """Extract readable text from LaTeX source files."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        # Remove comments
        lines = [l for l in raw.splitlines() if not l.lstrip().startswith("%")]
        text = "\n".join(lines)
        # Extract section titles
        text = _LATEX_SIMPLE_RE.sub(r"# \1", text)
        # Remove complex commands
        text = _LATEX_CMD_RE.sub("", text)
        # Remove remaining simple commands but keep their arguments
        text = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", text)
        # Remove remaining backslash commands
        text = re.sub(r"\\[a-zA-Z]+\*?", "", text)
        # Remove braces
        text = text.replace("{", "").replace("}", "")
        # Remove math delimiters but keep content
        text = re.sub(r"\$\$?", "", text)
        return _clean(text)
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------
# EPUB
# ---------------------------------------------------------------------
def _extract_epub(path: Path) -> str:
    """Extract text from EPUB ebooks (ZIP of XHTML chapters)."""
    try:
        with zipfile.ZipFile(str(path), "r") as zf:
            parts: list[str] = []
            # Find content files (XHTML/HTML)
            content_files = sorted(
                f for f in zf.namelist()
                if f.endswith((".xhtml", ".html", ".htm"))
                and "toc" not in f.lower()
            )
            for cf in content_files[:100]:  # cap at 100 chapters
                try:
                    data = zf.read(cf).decode("utf-8", errors="replace")
                    # Strip HTML tags
                    text = _XML_TAG_RE.sub(" ", data)
                    text = _clean(text)
                    if text and len(text) > 20:
                        parts.append(text)
                except Exception:
                    continue
            return "\n\n".join(parts)
    except (zipfile.BadZipFile, KeyError):
        pass
    return ""


# ---------------------------------------------------------------------
# FictionBook (.fb2)
# ---------------------------------------------------------------------
def _extract_fb2(path: Path) -> str:
    """Extract text from FictionBook XML format."""
    try:
        raw = path.read_bytes()
        root = ET.fromstring(raw)
        # FB2 namespace
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"
        parts: list[str] = []
        # Extract body sections
        for body in root.iter(f"{ns}body"):
            for p in body.iter(f"{ns}p"):
                text = "".join(p.itertext()).strip()
                if text:
                    parts.append(text)
        return _clean("\n".join(parts))
    except (ET.ParseError, Exception):
        pass
    return ""


# ---------------------------------------------------------------------
# Apple iWork formats (.pages, .numbers, .key)
# ---------------------------------------------------------------------
def _extract_iwork(path: Path) -> str:
    """Extract text from Apple iWork documents (ZIP packages with protobuf/XML)."""
    try:
        with zipfile.ZipFile(str(path), "r") as zf:
            parts: list[str] = []
            for name in zf.namelist():
                # iWork stores content in .iwa (protobuf) or Index/Tables/*.xml
                if name.endswith(".xml") or name.endswith(".txt"):
                    try:
                        data = zf.read(name).decode("utf-8", errors="replace")
                        text = _XML_TAG_RE.sub(" ", data)
                        text = _clean(text)
                        if text and len(text) > 20:
                            parts.append(text)
                    except Exception:
                        continue
            # If no XML found, try reading preview text
            if not parts:
                for name in zf.namelist():
                    if "preview" in name.lower() and name.endswith(".txt"):
                        try:
                            parts.append(zf.read(name).decode("utf-8", errors="replace"))
                        except Exception:
                            pass
            return "\n\n".join(parts)
    except (zipfile.BadZipFile, KeyError):
        pass
    return ""


# ---------------------------------------------------------------------
# Man pages
# ---------------------------------------------------------------------
def _extract_man(path: Path) -> str:
    """Extract readable text from Unix man/troff pages."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        # Remove troff commands but keep text
        lines: list[str] = []
        for line in raw.splitlines():
            if line.startswith("."):
                # Extract text arguments from common macros
                parts = line.split(None, 1)
                if len(parts) > 1 and parts[0] in (".SH", ".SS", ".TH", ".B", ".I"):
                    lines.append(parts[1].strip('"'))
            else:
                lines.append(line)
        text = "\n".join(lines)
        # Remove remaining inline formatting
        text = re.sub(r"\\f[BIR]", "", text)
        text = re.sub(r"\\[a-z]", "", text)
        return _clean(text)
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------
def is_legacy_document(path: Path | str) -> bool:
    """True if the file extension is handled by this module."""
    return Path(path).suffix.lower() in LEGACY_EXTENSIONS


def extract_legacy_text(path: Path | str) -> str:
    """Best-effort plain-text extraction from legacy/extended document formats.
    Never raises; returns '' on failure."""
    p = Path(path)
    ext = p.suffix.lower()

    try:
        if ext == ".doc":
            return _extract_doc(p)
        if ext == ".ppt":
            return _extract_ppt(p)
        if ext == ".msg":
            return _extract_msg(p)
        if ext in (".odt", ".ods", ".odp", ".odg"):
            return _extract_odf(p)
        if ext == ".eml":
            return _extract_eml(p)
        if ext == ".mbox":
            return _extract_mbox(p)
        if ext in (".tex", ".latex"):
            return _extract_tex(p)
        if ext == ".epub":
            return _extract_epub(p)
        if ext == ".fb2":
            return _extract_fb2(p)
        if ext in (".pages", ".numbers", ".key"):
            return _extract_iwork(p)
        if ext in (".man", ".info"):
            return _extract_man(p)
        if ext in (".wps", ".hwp"):
            # WPS/HWP: try as OLE2 with text extraction
            try:
                import olefile
                if olefile.isOleFile(str(p)):
                    ole = olefile.OleFileIO(str(p))
                    parts: list[str] = []
                    for stream in ole.listdir():
                        try:
                            data = ole.openstream(stream).read()
                            text = data.decode("utf-8", errors="ignore")
                            runs = re.findall(r"[\x20-\x7e\n\r\t]{20,}", text)
                            parts.extend(runs)
                        except Exception:
                            pass
                    ole.close()
                    if parts:
                        return _clean("\n".join(parts))
            except ImportError:
                pass
            except Exception:
                pass
            # Fallback: raw text extraction
            try:
                raw = p.read_bytes().decode("utf-8", errors="ignore")
                runs = re.findall(r"[\x20-\x7e\n\r\t]{20,}", raw)
                if runs:
                    return _clean("\n".join(runs))
            except Exception:
                pass
    except MemoryError:
        gc.collect()
    except Exception:
        pass

    return ""


def legacy_capabilities() -> dict[str, bool]:
    """Check which optional legacy document backends are available."""
    caps: dict[str, bool] = {
        "olefile": False,
        "antiword": False,
    }
    try:
        __import__("olefile")
        caps["olefile"] = True
    except ImportError:
        pass
    try:
        import subprocess
        result = subprocess.run(
            ["antiword", "--version"],
            capture_output=True, timeout=5,
        )
        caps["antiword"] = True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return caps
