#!/usr/bin/env python3
"""Run with Python: test_quote_styles.py <skill-directory>."""
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(sys.argv.pop(1)).resolve()
sys.path.insert(0, str(SKILL / 'scripts'))
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt
import format_document as fmt
import preserve_docx as preserve


class QuoteStyles(unittest.TestCase):
    def assert_run(self, run, chinese, size, bold, quote=False, latin='Times New Roman'):
        fonts = run._r.rPr.rFonts
        self.assertEqual(fonts.get(qn('w:eastAsia')), chinese)
        for attr in ('ascii', 'hAnsi', 'cs'):
            self.assertEqual(fonts.get(qn('w:' + attr)), chinese if quote else latin)
        self.assertEqual(run.font.size, Pt(size))
        self.assertEqual(run.font.bold, bold)

    def test_generated_styles_checker_and_boundary(self):
        blocks = [('heading1', '一级“重点ABC”'),
                  ('heading2', '二级"引文123"'),
                  ('heading3', {'heading': '三级“跨界', 'body': '正文XYZ”结束'}),
                  ('body', '正文“示例ABC”')]
        config = {'latin_font': 'Arial', 'styles': {'heading2': {'font': '自定义字体', 'size_pt': 18}}}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'result.docx'
            doc = fmt.build_document('标题“内容ABC”', blocks, config)
            doc.save(path)
            self.assertTrue(fmt.check_document(path, config)['ok'])
            doc = Document(path)
            for paragraph, chinese, size, bold in zip([doc.paragraphs[1], *doc.paragraphs[3:]],
                    ['方正小标宋_GBK', '方正黑体_GBK', '自定义字体', '方正仿宋_GBK', '方正仿宋_GBK'],
                    [20, 16, 18, 16, 16], [False, False, True, True, False]):
                for run in paragraph.runs:
                    inline = run.style.name == fmt.INLINE_BODY_STYLE
                    self.assert_run(run, '方正仿宋_GBK' if inline else chinese, 16 if inline else size,
                                    False if inline else bold, run.text in ('“', '”', '"'), 'Arial')
            # The independent checker must reject the former wrong title quote font.
            run = next(r for r in doc.paragraphs[1].runs if r.text == '“')
            fmt.set_font(run, '方正仿宋_GBK', 16, False, '方正仿宋_GBK')
            doc.save(path)
            with self.assertRaises(fmt.FormatError):
                fmt.check_document(path, config)
        with self.assertRaises(fmt.FormatError):
            fmt.build_document('未闭合“标题', [])

    def test_preserve_safe_runs_and_skip_mixed_run(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / 'in.docx', Path(temp) / 'out.docx'
            doc = Document()
            doc.add_paragraph('标题', style='Title')
            p = doc.add_paragraph(style='Heading 2')
            for text in ('标题', '“', '中文', 'ABC', '”'):
                p.add_run(text)
            doc.add_paragraph('正文“混合”')
            doc.save(source)
            report = preserve.preserve_file(source, output)
            self.assertEqual(report['status'], 'partial')
            self.assertTrue(any('cannot safely split' in row['reason'] for row in report['skipped']))
            p = Document(output).paragraphs[1]
            self.assertEqual(p.text, '标题“中文ABC”')
            for run in p.runs:
                self.assert_run(run, '方正楷体_GBK', 16, True, run.text in ('“', '”'))
            self.assertTrue(report['verification']['run_content_preserved'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
