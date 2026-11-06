#!/usr/bin/env python3
"""Tests for controlled inline headings and explicit configuration overrides."""
import contextlib
import io
import json
import unittest
from pathlib import Path
from uuid import uuid4

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt
import format_document as formatter


class MixedConfigTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).parent / ("mixed-test-" + uuid4().hex + ".docx")

    def tearDown(self):
        if self.path.exists():
            self.path.unlink()
        source = self.path.with_suffix('.json')
        if source.exists():
            source.unlink()

    def test_cli_generation_and_source_check(self):
        source = self.path.with_suffix('.json')
        source.write_text(json.dumps({'title': '标题', 'blocks': [{'type': 'heading2', 'heading': '（一）标题。', 'body': '原文。'}]}, ensure_ascii=False), encoding='utf-8')
        config = json.dumps({'line_spacing_pt': 30})
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(formatter.main(['--input', str(source), '--output', str(self.path), '--config-json', config]), 0)
            self.assertEqual(formatter.main(['--check', str(self.path), '--input', str(source), '--config-json', config]), 0)
            source.write_text(json.dumps({'title': '标题', 'blocks': [{'type': 'body', 'text': '改动。'}]}, ensure_ascii=False), encoding='utf-8')
            self.assertEqual(formatter.main(['--check', str(self.path), '--input', str(source), '--config-json', config]), 2)

    def test_inline_boundary_and_quotes(self):
        data = {"title": "标题", "blocks": [{"type": "heading2", "heading": "（一）“标题", "body": "跨界”正文ABC。"}]}
        title, blocks = formatter.parse_json(json.dumps(data, ensure_ascii=False))
        formatter.build_document(title, blocks).save(self.path)
        self.assertTrue(formatter.check_document(self.path)["ok"])
        paragraph = Document(self.path).paragraphs[3]
        self.assertEqual(paragraph.text, "（一）“标题跨界”正文ABC。")
        self.assertEqual(paragraph.style.name, "Official Heading 2")
        runs = paragraph.runs
        self.assertTrue(any(r.style.name == formatter.INLINE_BODY_STYLE for r in runs))
        for run in runs:
            if run.style.name == formatter.INLINE_BODY_STYLE:
                self.assertEqual(run._r.rPr.rFonts.get(qn("w:eastAsia")), "方正仿宋_GBK")
                self.assertFalse(run.bold)
        runs[-1].bold = True
        paragraph.part.document.save(self.path)
        with self.assertRaises(formatter.FormatError):
            formatter.check_document(self.path)

    def test_explicit_config_and_check(self):
        config = {"line_spacing_pt": 30, "left_margin_mm": 30, "latin_font": "Arial", "styles": {"body": {"font": "宋体", "size_pt": 15, "bold": True}, "heading2": {"size_pt": 17.5}}}
        blocks = [("heading2", {"heading": "（一）标题。", "body": "正文“引用ABC”。"})]
        formatter.build_document("标题", blocks, config).save(self.path)
        self.assertTrue(formatter.check_document(self.path, config)["ok"])
        doc = Document(self.path)
        self.assertEqual(doc.paragraphs[3].paragraph_format.line_spacing, Pt(30))
        self.assertEqual(doc.paragraphs[3].paragraph_format.first_line_indent, Pt(35))
        with self.assertRaises(formatter.FormatError):
            formatter.check_document(self.path)
        self.assertEqual(formatter.resolve_config()["line_spacing_pt"], 28)

    def test_reject_invalid_config_and_mixed_blocks(self):
        for config in ({"unknown": 1}, {"line_spacing_pt": 1.1}, {"left_margin_mm": -1}, {"styles": {"body": {"size_pt": 1.1}}}, {"styles": {"body": {"bold": 1}}}):
            with self.subTest(config=config), self.assertRaises(formatter.FormatError):
                formatter.resolve_config(config)
        for block in ({"type": "body", "heading": "标题", "body": "正文"}, {"type": "heading2", "heading": "", "body": "正文"}):
            with self.subTest(block=block), self.assertRaises(formatter.FormatError):
                formatter.parse_json(json.dumps({"title": "标题", "blocks": [block]}, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
