"""Regression tests for preservation-first DOCX formatting."""
import contextlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from PIL import Image

import preserve_docx as preserve


class PreserveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'in.docx'
        self.output = self.root / 'out.docx'

    def rich_document(self):
        image = self.root / 'image.png'
        Image.new('RGB', (4, 4), 'red').save(image)
        doc = Document()
        doc.add_paragraph('标题', style='Title')
        doc.add_paragraph('一、标题', style='Heading 1')
        doc.add_paragraph('正文ABC123。')
        doc.add_paragraph('（一）“重点”正文。')  # one run crosses quote styles, must skip
        doc.add_paragraph('图').add_run().add_picture(str(image))
        table = doc.add_table(rows=1, cols=1)
        table.cell(0, 0).text = '表格保留'
        p = doc.add_paragraph('书签')
        bm = OxmlElement('w:bookmarkStart'); bm.set(qn('w:id'), '1'); bm.set(qn('w:name'), 'test')
        p._p.append(bm)
        doc.sections[0].header.paragraphs[0].text = '页眉'
        doc.sections[0].footer.paragraphs[0].text = '页脚'
        doc.save(self.source)

    def test_preserves_parts_and_objects(self):
        self.rich_document()
        source = self.source.read_bytes()
        result = preserve.preserve_file(self.source, self.output, {'line_spacing_pt': 30})
        self.assertEqual(self.source.read_bytes(), source)
        self.assertTrue(result['ok'])
        self.assertEqual([x['paragraph'] for x in result['formatted']], [1, 2, 3])
        self.assertEqual([x['paragraph'] for x in result['skipped']], [4, 5, 6])
        doc = Document(self.output)
        self.assertEqual(doc.paragraphs[2].text, '正文ABC123。')
        self.assertEqual(doc.paragraphs[2].paragraph_format.line_spacing, Pt(30))
        self.assertEqual(doc.tables[0].cell(0, 0).text, '表格保留')
        with zipfile.ZipFile(self.source) as left, zipfile.ZipFile(self.output) as right:
            self.assertEqual(left.namelist(), right.namelist())
            for name in left.namelist():
                if name != 'word/document.xml':
                    self.assertEqual(left.read(name), right.read(name), name)

    def test_cross_paragraph_fields_and_numbering(self):
        doc = Document()
        begin = doc.add_paragraph().add_run()._r
        field = OxmlElement('w:fldChar'); field.set(qn('w:fldCharType'), 'begin'); begin.append(field)
        doc.add_paragraph('inside field')
        end = doc.add_paragraph().add_run()._r
        field = OxmlElement('w:fldChar'); field.set(qn('w:fldCharType'), 'end'); end.append(field)
        p = doc.add_paragraph('numbered')
        p._p.get_or_add_pPr().append(OxmlElement('w:numPr'))
        doc.add_paragraph('ordinary')
        doc.save(self.source)
        result = preserve.preserve_file(self.source, self.output)
        self.assertEqual([x['paragraph'] for x in result['skipped']], [1, 2, 3, 4])
        self.assertEqual(result['formatted'][0]['paragraph'], 5)

    def test_revision_hyperlink_and_unbalanced_quote_skipped(self):
        doc = Document()
        p = doc.add_paragraph('revision')
        p._p.append(OxmlElement('w:ins'))
        p = doc.add_paragraph('link')
        p._p.append(OxmlElement('w:hyperlink'))
        doc.add_paragraph('bad “quote')
        doc.save(self.source)
        result = preserve.preserve_file(self.source, self.output)
        self.assertEqual(len(result['skipped']), 3)
        self.assertFalse(result['formatted'])

    def test_unmatched_field_fails_without_output(self):
        doc = Document(); p = doc.add_paragraph(); run = p.add_run()
        field = OxmlElement('w:fldChar'); field.set(qn('w:fldCharType'), 'begin'); run._r.append(field)
        doc.save(self.source)
        with self.assertRaisesRegex(preserve.PreserveError, 'Unbalanced'):
            preserve.preserve_file(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_existing_output_and_invalid_configuration(self):
        Document().save(self.source)
        self.output.write_bytes(b'keep')
        with self.assertRaises(preserve.PreserveError):
            preserve.preserve_file(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b'keep')
        with self.assertRaises(ValueError):
            preserve.preserve_file(self.source, self.root / 'other.docx', {'invalid': 1})
        self.assertFalse((self.root / 'other.docx').exists())
        with self.assertRaisesRegex(preserve.PreserveError, 'Margin overrides'):
            preserve.preserve_file(self.source, self.root / 'other.docx', {'top_margin_mm': 30})
        self.assertFalse((self.root / 'other.docx').exists())

    def test_failed_write_does_not_leave_output(self):
        Document().save(self.source)
        with mock.patch.object(preserve, '_canonical', side_effect=preserve.PreserveError('verification failed')):
            with self.assertRaises(preserve.PreserveError):
                preserve.preserve_file(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_unrelated_property_damage_is_detected(self):
        doc = Document()
        p = doc.add_paragraph('标题', style='Title')
        p.paragraph_format.keep_with_next = True
        run = p.runs[0]
        color = OxmlElement('w:color'); color.set(qn('w:val'), 'FF0000')
        run._r.get_or_add_rPr().append(color)
        doc.save(self.source)
        original_set_font = preserve.fmt.set_font
        def break_color(owner, *args, **kwargs):
            original_set_font(owner, *args, **kwargs)
            owner._element.rPr.remove(owner._element.rPr.find(qn('w:color')))
        with mock.patch.object(preserve.fmt, 'set_font', side_effect=break_color):
            with self.assertRaisesRegex(preserve.PreserveError, 'non-formatting'):
                preserve.preserve_file(self.source, self.output)
        self.assertFalse(self.output.exists())
        original_set_paragraph = preserve.fmt.set_paragraph_format
        def break_keep(owner, *args, **kwargs):
            original_set_paragraph(owner, *args, **kwargs)
            owner._p.pPr.remove(owner._p.pPr.find(qn('w:keepNext')))
        with mock.patch.object(preserve.fmt, 'set_paragraph_format', side_effect=break_keep):
            with self.assertRaisesRegex(preserve.PreserveError, 'non-formatting'):
                preserve.preserve_file(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_ambiguous_title_and_outline(self):
        doc = Document()
        doc.add_paragraph('普通样式的标题')
        custom = doc.styles.add_style('Custom outline', 1)
        outline = OxmlElement('w:outlineLvl'); outline.set(qn('w:val'), '1')
        custom._element.get_or_add_pPr().append(outline)
        doc.add_paragraph('自定义标题', style=custom)
        doc.add_paragraph('（一）可能是标题。正文')
        doc.add_paragraph('（二）标题。 正文', style='Heading 2')
        doc.save(self.source)
        result = preserve.preserve_file(self.source, self.output)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['formatted'], [{'paragraph': 2, 'kind': 'heading2'}])
        self.assertEqual([x['paragraph'] for x in result['skipped']], [1, 3, 4])

    def test_unsupported_heading_levels(self):
        doc = Document()
        doc.add_paragraph('标题', style='Title')
        doc.add_paragraph('四级标题', style='Heading 4')
        base = doc.styles.add_style('Fourth-level base', 1)
        outline = OxmlElement('w:outlineLvl'); outline.set(qn('w:val'), '3')
        base._element.get_or_add_pPr().append(outline)
        derived = doc.styles.add_style('Derived fourth-level', 1)
        derived.base_style = base
        doc.add_paragraph('继承四级', style=derived)
        p = doc.add_paragraph('直接四级')
        outline = OxmlElement('w:outlineLvl'); outline.set(qn('w:val'), '3')
        p._p.get_or_add_pPr().append(outline)
        p = doc.add_paragraph('正文大纲')
        outline = OxmlElement('w:outlineLvl'); outline.set(qn('w:val'), '9')
        p._p.get_or_add_pPr().append(outline)
        p = doc.add_paragraph('无效大纲')
        outline = OxmlElement('w:outlineLvl'); outline.set(qn('w:val'), '10')
        p._p.get_or_add_pPr().append(outline)
        doc.save(self.source)
        result = preserve.preserve_file(self.source, self.output)
        self.assertEqual([x['paragraph'] for x in result['formatted']], [1, 5])
        self.assertEqual([x['paragraph'] for x in result['skipped']], [2, 3, 4, 6])
        for item in result['skipped']:
            self.assertIn('heading level', item['reason'])

    def test_signature_is_rejected(self):
        doc = Document(); doc.add_paragraph('正文'); doc.save(self.source)
        with zipfile.ZipFile(self.source, 'a') as z:
            z.writestr('_xmlsignatures/sig1.xml', b'signature')
        with self.assertRaisesRegex(preserve.PreserveError, 'signed'):
            preserve.preserve_file(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_cli_protocol(self):
        doc = Document(); doc.add_paragraph('正文'); doc.save(self.source)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = preserve.main(['--input', str(self.source), '--output', str(self.output), '--config-json', '{"line_spacing_pt":30}'])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())['ok'])
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            code = preserve.main(['--input', str(self.source), '--output', str(self.output)])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(error.getvalue())['ok'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
