"""Import plain external documents without silently flattening rich structures."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn


class ImportError(ValueError):
    """Input cannot be imported without potential content loss."""


def _flatten(blocks):
    result = []
    for kind, value in blocks:
        if isinstance(value, str):
            result.append(value)
        elif isinstance(value, dict) and set(value) == {"heading", "body"}:
            result.append(value["heading"] + value["body"])
        else:
            raise ImportError(f"Unsupported block for comparison: {kind}")
    return result


def compare_content(title, blocks, output):
    """Verify exact paragraph text and order against a generated DOCX."""
    expected = [title, *_flatten(blocks)]
    try:
        doc = Document(str(output))
    except Exception as exc:
        raise ImportError(f"Cannot open output DOCX for comparison: {exc}") from exc
    actual = [p.text for p in doc.paragraphs]
    if actual != expected:
        index = next((i for i, (a, b) in enumerate(zip(expected, actual)) if a != b), min(len(expected), len(actual)))
        raise ImportError(f"Content mismatch at paragraph {index + 1}; expected {len(expected)} paragraphs, got {len(actual)}")
    if doc.tables or doc.inline_shapes:
        raise ImportError("Output has unexpected tables or images")
    return {"ok": True, "paragraphs": len(expected)}


def _style_kind(paragraph):
    style = paragraph.style
    visited = set()
    kind = None
    while style is not None and style.style_id not in visited:
        visited.add(style.style_id)
        ppr = style._element.find(qn('w:pPr'))
        if ppr is not None and ppr.find(qn('w:numPr')) is not None:
            raise ImportError("Automatic numbering in paragraph style is unsupported")
        if kind is None:
            name = (style.name or '').lower()
            if name in ('heading 1', 'heading 2', 'heading 3'):
                kind = 'heading' + name[-1]
        style = style.base_style
    return kind or 'body'


def _read_docx(path):
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            forbidden = ('word/media/', 'word/embeddings/', 'word/activeX/', 'word/comments',
                         'word/footnotes', 'word/endnotes', 'word/vbaProject', 'word/header', 'word/footer')
            found = next((name for name in names if any(name.startswith(p) for p in forbidden)), None)
            if found:
                raise ImportError(f"Unsupported DOCX part: {found}")
        doc = Document(str(path))
    except ImportError:
        raise
    except Exception as exc:
        raise ImportError(f"Cannot open DOCX: {exc}") from exc
    body = doc._element.body
    if doc.styles._element.xpath('.//w:vanish | .//w:webHidden | .//w:specVanish'):
        raise ImportError("Hidden text in document styles is unsupported")
    if doc._element.xpath('.//w:vanish | .//w:webHidden | .//w:specVanish'):
        raise ImportError("Hidden text is unsupported")
    if any(el.tag not in (qn('w:p'), qn('w:sectPr')) for el in body):
        raise ImportError("Tables or other unsupported document structures")
    if len(doc.sections) != 1:
        raise ImportError("Multiple sections are unsupported")
    sect = body.find(qn('w:sectPr'))
    if sect is not None and sect.xpath('.//w:headerReference | .//w:footerReference | .//w:sectPrChange'):
        raise ImportError("Header/footer or section revisions are unsupported")
    paragraphs = doc.paragraphs
    if not paragraphs:
        raise ImportError("Empty DOCX")
    texts, kinds = [], []
    for index, p in enumerate(paragraphs, 1):
        ppr = p._p.find(qn('w:pPr'))
        if ppr is not None and ppr.xpath('.//w:numPr | .//w:sectPr | .//w:pPrChange | .//w:ins | .//w:del | .//w:rPrChange'):
            raise ImportError(f"Paragraph {index}: numbering, section or tracked changes unsupported")
        if any(el.tag not in (qn('w:pPr'), qn('w:r')) for el in p._p):
            raise ImportError(f"Paragraph {index}: hyperlinks, fields, revisions, bookmarks or other structures unsupported")
        for run in p.runs:
            for el in run._r:
                if el.tag not in (qn('w:rPr'), qn('w:t'), qn('w:br')):
                    raise ImportError(f"Paragraph {index}: field, drawing or unsupported run content")
                if el.tag == qn('w:br') and el.get(qn('w:type'), 'textWrapping') != 'textWrapping':
                    raise ImportError(f"Paragraph {index}: page/column break unsupported")
                if el.tag == qn('w:rPr') and el.xpath('.//w:rPrChange | .//w:ins | .//w:del'):
                    raise ImportError(f"Paragraph {index}: tracked changes unsupported")
        text = p.text
        if not text.strip() or any(ord(c) < 32 and c != '\n' for c in text):
            raise ImportError(f"Paragraph {index}: empty paragraph or unsupported control character")
        texts.append(text)
        kinds.append(_style_kind(p))
    if '\n' in texts[0]:
        raise ImportError("Title must be a single line")
    return texts[0], list(zip(kinds[1:], texts[1:]))


def _read_txt(path):
    try:
        text = Path(path).read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError) as exc:
        raise ImportError(f"TXT requires readable UTF-8: {exc}") from exc
    lines = text.splitlines()
    if not lines or any(not line.strip() or any(ord(c) < 32 for c in line) for line in lines):
        raise ImportError("TXT must contain nonempty lines without control characters; each line is one paragraph")
    return lines[0], [('body', line) for line in lines[1:]]


def _read_legacy(path):
    if os.name != 'nt':
        raise ImportError("DOC/WPS import requires Windows Word/WPS COM for source-content verification; LibreOffice conversion alone cannot verify source fidelity")
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if not shell:
        raise ImportError("DOC/WPS import requires PowerShell and installed Word/WPS COM")
    helper = Path(__file__).with_name('legacy_import.ps1')
    with tempfile.TemporaryDirectory(prefix='official-import-') as tmp:
        target = Path(tmp) / 'converted.docx'
        stdout_path, stderr_path = Path(tmp) / 'stdout.json', Path(tmp) / 'stderr.txt'
        try:
            with stdout_path.open('wb') as stdout, stderr_path.open('wb') as stderr:
                result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-File', str(helper),
                                         str(Path(path).resolve()), str(target)], stdout=stdout,
                                        stderr=stderr, timeout=120)
            output = stdout_path.read_text(encoding='utf-8-sig', errors='replace').strip()
            error = stderr_path.read_text(encoding='utf-8-sig', errors='replace').strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ImportError(f"Legacy converter failed (check for an orphaned Word/WPS process after timeout): {exc}") from exc
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise ImportError(f"Legacy converter failed: {error or output}") from exc
        if result.returncode != 0 or not data.get('ok'):
            raise ImportError(f"Legacy converter rejected document: {data.get('error', error)}")
        title, blocks = _read_docx(target)
        if [title, *_flatten(blocks)] != data['paragraphs']:
            raise ImportError("Legacy conversion changed paragraph content; conversion rejected")
        return title, blocks


def import_source(path):
    """Return (title, [(kind, text), ...]); never write to the input path."""
    path = Path(path)
    if not path.is_file():
        raise ImportError(f"Input file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == '.docx':
        return _read_docx(path)
    if suffix == '.txt':
        return _read_txt(path)
    if suffix in ('.doc', '.wps'):
        raise ImportError("DOC/WPS automatic conversion is disabled until safety is verified; save a copy as DOCX in Word/WPS first, then import the DOCX")
    if suffix in ('.md', '.markdown'):
        try:
            from format_document import parse_markdown
            return parse_markdown(path.read_text(encoding='utf-8-sig'))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ImportError(f"Cannot import Markdown: {exc}") from exc
    raise ImportError(f"Unsupported input extension: {suffix}")
