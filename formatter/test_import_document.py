"""Regression tests for conservative document import."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docx import Document
from docx.oxml import OxmlElement
from docx.shared import Inches
import import_document as importer


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def docx(self):
        path = self.root / 'input.docx'
        doc = Document()
        doc.add_paragraph('Title')
        doc.add_paragraph('一、原文 123', style='Heading 1')
        doc.add_paragraph('正文 ABC。')
        doc.save(path)
        return path

    def test_docx_and_source_unchanged(self):
        path = self.docx()
        before = path.read_bytes()
        title, blocks = importer.import_source(path)
        self.assertEqual(title, 'Title')
        self.assertEqual(blocks, [('heading1', '一、原文 123'), ('body', '正文 ABC。')])
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(importer.compare_content(title, blocks, path)['paragraphs'], 3)
        with self.assertRaisesRegex(importer.ImportError, 'paragraph 3'):
            importer.compare_content(title, blocks[:-1] + [('body', 'changed')], path)
        self.assertTrue(importer.compare_content(title, [blocks[0], ('heading2', {'heading': '正文 ', 'body': 'ABC。'})], path)['ok'])

    def test_unsupported_docx_structures(self):
        def table(doc): doc.add_table(rows=1, cols=1)
        def field(doc): doc.paragraphs[1].runs[0]._r.append(OxmlElement('w:fldChar'))
        def revision(doc): doc.paragraphs[1]._p.append(OxmlElement('w:ins'))
        def bookmark(doc): doc.paragraphs[1]._p.append(OxmlElement('w:bookmarkStart'))
        def numbering(doc): doc.paragraphs[1]._p.get_or_add_pPr().append(OxmlElement('w:numPr'))
        def footer(doc): doc.sections[0].footer.paragraphs[0].text = 'footer'
        def empty(doc): doc.add_paragraph('')
        def hidden_run(doc): doc.paragraphs[1].runs[0].font.hidden = True
        def hidden_style(doc): doc.styles['Normal'].font.hidden = True
        for mutate in (table, field, revision, bookmark, numbering, footer, empty, hidden_run, hidden_style):
            with self.subTest(mutate=mutate.__name__):
                path = self.docx()
                doc = Document(path)
                mutate(doc)
                doc.save(path)
                with self.assertRaises(importer.ImportError):
                    importer.import_source(path)

    def test_txt_and_markdown(self):
        path = self.root / 'test.txt'
        path.write_text('标题\n正文123\n', encoding='utf-8')
        self.assertEqual(importer.import_source(path), ('标题', [('body', '正文123')]))
        path.write_text('标题\n\n正文', encoding='utf-8')
        with self.assertRaisesRegex(importer.ImportError, 'nonempty'):
            importer.import_source(path)
        path = self.root / 'test.md'
        path.write_text('# 标题\n\n## 一、标题\n\n正文', encoding='utf-8')
        self.assertEqual(importer.import_source(path), ('标题', [('heading1', '一、标题'), ('body', '正文')]))

    def test_legacy_failure_is_explicit_and_no_source_overwrite(self):
        path = self.root / 'test.wps'
        path.write_bytes(b'legacy original')
        def conversion(args, **kwargs):
            self.assertNotIn('capture_output', kwargs)
            self.assertTrue(hasattr(kwargs['stdout'], 'fileno'))
            kwargs['stdout'].write(json.dumps({'ok': False, 'error': 'Revisions unsupported'}).encode('utf-8'))
            return mock.Mock(returncode=2)
        with mock.patch.object(importer.os, 'name', 'nt'), mock.patch.object(importer.shutil, 'which', return_value='pwsh'), mock.patch.object(importer.subprocess, 'run', side_effect=conversion):
            with self.assertRaisesRegex(importer.ImportError, 'Revisions unsupported'):
                importer._read_legacy(path)
        self.assertEqual(path.read_bytes(), b'legacy original')
        with mock.patch.object(importer.subprocess, 'run') as run:
            with self.assertRaisesRegex(importer.ImportError, 'disabled.*save a copy as DOCX'):
                importer.import_source(path)
            run.assert_not_called()


    def test_legacy_mismatch_rejected(self):
        source = self.root / 'test.doc'
        source.write_bytes(b'legacy original')
        docx = self.docx()
        def conversion(args, **kwargs):
            Path(args[-1]).write_bytes(docx.read_bytes())
            kwargs['stdout'].write(json.dumps({'ok': True, 'paragraphs': ['Title', 'different']}).encode('utf-8'))
            return mock.Mock(returncode=0)
        with mock.patch.object(importer.os, 'name', 'nt'), mock.patch.object(importer.shutil, 'which', return_value='pwsh'), mock.patch.object(importer.subprocess, 'run', side_effect=conversion):
            with self.assertRaisesRegex(importer.ImportError, 'changed paragraph content'):
                importer._read_legacy(source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
