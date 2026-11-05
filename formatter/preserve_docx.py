#!/usr/bin/env python3
"""Conservatively format existing DOCX body paragraphs without rebuilding the package."""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

import format_document as fmt


class PreserveError(ValueError):
    pass


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(element):
    return etree.tostring(element, method="c14n")


def _style_kind(paragraph):
    style = paragraph.style
    seen = set()
    kind = None
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        props = style._element.find(qn('w:pPr'))
        if props is not None and props.find(qn('w:numPr')) is not None:
            return None, 'automatic numbering in style'
        if props is not None:
            outline = props.find(qn('w:outlineLvl'))
            if outline is not None:
                value = outline.get(qn('w:val'))
                if value in ('0', '1', '2'):
                    if kind is None:
                        kind = 'heading' + str(int(value) + 1)
                elif value != '9':
                    return None, 'unsupported or invalid heading level in style'
        if style._element.xpath('.//w:vanish | .//w:webHidden | .//w:specVanish | .//w:rPrChange | .//w:pPrChange'):
            return None, 'hidden or revised style'
        name = (style.name or '').lower()
        heading_match = re.fullmatch(r'(?:official )?heading\s+(\d+)', name)
        if heading_match and int(heading_match.group(1)) >= 4:
            return None, 'unsupported heading level in style'
        if kind is None:
            if name in ('title', 'official title'):
                kind = 'title'
            elif name in ('heading 1', 'heading 2', 'heading 3'):
                kind = 'heading' + name[-1]
            elif name in ('official heading 1', 'official heading 2', 'official heading 3'):
                kind = 'heading' + name[-1]
        style = style.base_style
    return kind or 'body', None


def _safety(paragraph, in_field):
    p = paragraph._p
    if in_field:
        return None, 'complex field range'
    if any(child.tag not in (qn('w:pPr'), qn('w:r')) for child in p):
        return None, 'complex paragraph (link, bookmark, revision or object)'
    ppr = p.find(qn('w:pPr'))
    if ppr is not None and ppr.xpath('.//w:numPr | .//w:sectPr | .//w:pPrChange | .//w:ins | .//w:del | .//w:rPrChange | .//w:framePr'):
        return None, 'numbering, section, frame or tracked properties'
    if ppr is not None:
        ind = ppr.find(qn('w:ind'))
        if ind is not None and (ind.get(qn('w:hanging')) is not None or ind.get(qn('w:hangingChars')) is not None):
            return None, 'hanging indentation requires review'
    kind, reason = _style_kind(paragraph)
    if reason:
        return None, reason
    if ppr is not None:
        outline = ppr.find(qn('w:outlineLvl'))
        if outline is not None:
            value = outline.get(qn('w:val'))
            if value in ('0', '1', '2'):
                kind = 'heading' + str(int(value) + 1)
            elif value == '9':
                kind = 'body'
            else:
                return None, 'unsupported or invalid paragraph heading level'
    for run in paragraph.runs:
        if any(child.tag not in (qn('w:rPr'), qn('w:t')) for child in run._r):
            return None, 'field, image, break or other run object'
        rpr = run._r.find(qn('w:rPr'))
        if rpr is not None and rpr.xpath('.//w:rPrChange | .//w:ins | .//w:del | .//w:vanish | .//w:webHidden | .//w:specVanish'):
            return None, 'hidden or revised run'
        try:
            style = run.style
            seen = set()
            while style is not None and style.style_id not in seen:
                seen.add(style.style_id)
                if style._element.xpath('.//w:vanish | .//w:webHidden | .//w:specVanish | .//w:rPrChange'):
                    return None, 'hidden or revised character style'
                style = style.base_style
        except (KeyError, ValueError):
            return None, 'unresolved character style'
    text = paragraph.text
    if not text.strip():
        return None, 'empty paragraph'
    try:
        segments = fmt.segment_text(text)
    except fmt.FormatError:
        return None, 'unmatched double quotes'
    classes = []
    for content, quoted, mark in segments:
        classes.extend([(quoted, mark)] * len(content))
    offset = 0
    for run in paragraph.runs:
        run_classes = set(classes[offset:offset + len(run.text)])
        offset += len(run.text)
        if len(run_classes) > 1:
            return None, 'quote formatting changes within an existing run; cannot safely split'
    return (kind, classes), None


def _field_ranges(body):
    depth = 0
    result = {}
    malformed = False
    for block in body:
        active = depth > 0
        for element in block.iter(qn('w:fldChar')):
            active = True
            typ = element.get(qn('w:fldCharType'))
            if typ == 'begin':
                depth += 1
            elif typ == 'end':
                depth -= 1
                if depth < 0:
                    malformed = True
                    depth = 0
        if block.tag == qn('w:p'):
            result[block] = active
    return result, malformed or depth != 0


def _stripped(paragraph):
    clone = copy.deepcopy(paragraph)
    def strip_attributes(parent, rules):
        for child in list(parent):
            if child.tag not in rules:
                continue
            for attr in rules[child.tag]:
                child.attrib.pop(qn('w:' + attr), None)
            if not len(child) and not child.attrib and not child.text:
                parent.remove(child)
    ppr = clone.find(qn('w:pPr'))
    if ppr is not None:
        strip_attributes(ppr, {qn('w:jc'): ('val',),
                               qn('w:spacing'): ('before', 'after', 'line', 'lineRule'),
                               qn('w:widowControl'): ('val',),
                               qn('w:ind'): ('left', 'right', 'firstLine', 'firstLineChars')})
        if not len(ppr) and not ppr.attrib and not ppr.text:
            clone.remove(ppr)
    for run in clone.findall(qn('w:r')):
        rpr = run.find(qn('w:rPr'))
        if rpr is not None:
            strip_attributes(rpr, {qn('w:rFonts'): ('ascii', 'hAnsi', 'eastAsia', 'cs', 'asciiTheme', 'hAnsiTheme', 'eastAsiaTheme', 'cstheme'),
                                   qn('w:b'): ('val',), qn('w:sz'): ('val',), qn('w:szCs'): ('val',)})
            if not len(rpr) and not rpr.attrib and not rpr.text:
                run.remove(rpr)
    return _canonical(clone)


def preserve_file(source, output, config=None):
    source, output = Path(source), Path(output)
    requested_config = config
    config = fmt.resolve_config(config)
    if isinstance(requested_config, dict) and any(key in requested_config for key in ('top_margin_mm', 'bottom_margin_mm', 'left_margin_mm', 'right_margin_mm')):
        raise PreserveError('Margin overrides are unsupported in preserve mode; existing page setup is retained')
    if source.suffix.lower() != '.docx' or output.suffix.lower() != '.docx':
        raise PreserveError('Input and output must be DOCX files')
    if source.resolve() == output.resolve() or (source.exists() and output.exists() and os.path.samefile(source, output)):
        raise PreserveError('Input and output must differ')
    if output.exists():
        raise PreserveError('Output already exists')
    original_bytes = source.read_bytes()
    with zipfile.ZipFile(io.BytesIO(original_bytes)) as archive:
        infos = archive.infolist()
        names = [entry.filename for entry in infos]
        if len(names) != len(set(names)):
            raise PreserveError('Duplicate package entries unsupported')
        signed = any(name.lower().startswith('_xmlsignatures/') for name in names)
        for info in infos:
            if info.filename.endswith('.rels') or info.filename == '[Content_Types].xml':
                data = archive.read(info).lower()
                if b'digital-signature' in data or b'digital-signature' in info.filename.lower().encode():
                    signed = True
        if signed:
            raise PreserveError('Digitally signed DOCX cannot be modified without invalidating signature')
        if any(entry.flag_bits & 1 for entry in infos):
            raise PreserveError('Encrypted DOCX unsupported')
        if 'word/document.xml' not in names:
            raise PreserveError('Missing main document part')
        parts = {entry.filename: archive.read(entry) for entry in infos}
        archive_comment = archive.comment
    doc = Document(io.BytesIO(original_bytes))
    body = doc._element.body
    defaults = doc.styles._element.xpath('./w:docDefaults')
    if defaults and any(node.tag in {qn('w:vanish'), qn('w:webHidden'), qn('w:specVanish')} for node in defaults[0].iter()):
        raise PreserveError('Hidden text in document defaults is unsupported')
    before_body = copy.deepcopy(body)
    field_ranges, malformed = _field_ranges(body)
    if malformed:
        raise PreserveError('Unbalanced complex field boundaries; cannot safely identify field ranges')
    report = {'formatted': [], 'skipped': []}
    paragraphs = list(doc.paragraphs)
    first_content = next((i for i, p in enumerate(paragraphs, 1) if p.text.strip()), None)
    for index, paragraph in enumerate(paragraphs, 1):
        safe, reason = _safety(paragraph, field_ranges.get(paragraph._p, False))
        if not reason and safe[0] == 'body' and index == first_content:
            reason = 'first nonempty paragraph has no explicit title or heading style; review title'
        if not reason and safe[0] == 'body' and re.match(r'^\s*(?:[一二三四五六七八九十百]+、|[（(][一二三四五六七八九十百0-9]+[）)]|[0-9]+[.．])', paragraph.text):
            reason = 'numbered paragraph may be a heading; review style'
        if not reason and safe[0].startswith('heading') and re.search(r'[。！？]\s*\S', paragraph.text):
            reason = 'heading may contain inline body; review boundary'
        if reason:
            report['skipped'].append({'paragraph': index, 'reason': reason})
            continue
        kind, classes = safe
        before = _stripped(paragraph._p)
        fmt.set_paragraph_format(paragraph, kind, config)
        offset = 0
        for run in paragraph.runs:
            run_text = run.text
            if not run_text:
                continue
            quoted, mark = classes[offset]
            offset += len(run_text)
            font = config['styles']['body' if quoted else kind]
            fmt.set_font(run, font['font'], font['size_pt'], False if quoted else font['bold'],
                         font['font'] if mark else config['latin_font'])
        if _stripped(paragraph._p) != before:
            raise PreserveError(f'Paragraph {index}: non-formatting content changed')
        report['formatted'].append({'paragraph': index, 'kind': kind})
    # Restoring only the edited paragraphs must reconstruct the complete body tree.
    current = copy.deepcopy(body)
    current_paragraphs = current.xpath('./w:p')
    for index in (item['paragraph'] for item in report['formatted']):
        old = copy.deepcopy(before_body.xpath('./w:p')[index - 1])
        node = current_paragraphs[index - 1]
        node.getparent().replace(node, old)
    if _canonical(current) != _canonical(before_body):
        raise PreserveError('Unexpected changes to body structure')
    parts['word/document.xml'] = etree.tostring(doc._element, xml_declaration=True, encoding='UTF-8', standalone=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.comment = archive_comment
        for info in infos:
            archive.writestr(info, parts[info.filename])
    result_bytes = buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(result_bytes)) as archive:
        if archive.namelist() != names:
            raise PreserveError('Package entry list changed')
        for name in names:
            if name != 'word/document.xml' and _digest(archive.read(name)) != _digest(parts[name]):
                raise PreserveError(f'Package part changed: {name}')
    # Ensure serialized XML round-trips with the same semantics, not just in-memory XML.
    reopened = Document(io.BytesIO(result_bytes))
    if _canonical(reopened._element) != _canonical(doc._element):
        raise PreserveError('Serialized document XML changed unexpectedly')
    if source.read_bytes() != original_bytes:
        raise PreserveError('Input changed during processing')
    created = False
    try:
        with output.open('xb') as stream:
            created = True
            stream.write(result_bytes)
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    status = 'no_changes' if not report['formatted'] else 'partial' if report['skipped'] else 'body_formatted'
    return {'ok': True, 'status': status, 'output': str(output.resolve()), 'source_sha256': _digest(original_bytes),
            'formatted': report['formatted'], 'skipped': report['skipped'],
            'preserved': {'tables': len(doc.tables), 'sections': len(doc.sections),
                          'headers_footers_and_page_setup': 'unchanged', 'default_page_number_added': False},
            'verification': {'source_unchanged': True, 'non_document_parts_byte_identical': True,
                             'body_structure_preserved': True, 'run_content_preserved': True},
            'limitations': ['Headers, footers, tables and page setup are preserved unchanged',
                            'Complex paragraphs and auto-numbering are skipped',
                            'Paragraph kind uses explicit Word styles only; other paragraphs are body',
                            'Font availability and actual page rendering are not checked']}


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise PreserveError(message)


def main(argv=None):
    parser = _JsonArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--config-json')
    try:
        args = parser.parse_args(argv)
        config = json.loads(args.config_json) if args.config_json else None
        print(json.dumps(preserve_file(args.input, args.output, config), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
