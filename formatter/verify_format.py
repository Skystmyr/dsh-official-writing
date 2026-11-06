#!/usr/bin/env python3
"""Run: bundled-python official-writing/formatter/verify_format.py"""
import contextlib
import io
import json
import shutil
import uuid
import unittest
from pathlib import Path
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Mm, Pt
import format_document as formatter


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / ("official-format-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.source = self.root / "input.md"
        self.output = self.root / "output.docx"
        self.source.write_text("# 非正式排版测试 Test20%\n\n## 一、标题ABC1%\n\n### （一）标题ABC2%\n\n#### 1. 标题ABC3%\n\n2026年仅作原文数字测试；ABC 12.5%，不作事实陈述。\n保留第二行！\n\n1. 这是普通段落，不自动识别为标题。\n", encoding="utf-8")

    def tearDown(self):
        self.assertEqual(self.root.parent, Path(__file__).resolve().parent)
        self.assertTrue(self.root.name.startswith("official-format-"))
        shutil.rmtree(self.root)

    def generate(self):
        return formatter.format_file(self.source, self.output)

    def test_all_formats_and_mixed_runs(self):
        result = self.generate()
        self.assertTrue(result["ok"])
        document = Document(self.output)
        section = document.sections[0]
        for actual, expected in ((section.page_width, Mm(210)), (section.page_height, Mm(297)), *[(margin, Mm(25)) for margin in (section.top_margin, section.bottom_margin, section.left_margin, section.right_margin)]):
            self.assertLess(abs(actual - expected), 700)
        self.assertEqual([p.text for p in document.paragraphs[:3:2]], ["", ""])
        self.assertEqual(document.paragraphs[0].style.name, "Official Blank")
        kinds = ["title", "heading1", "heading2", "heading3", "body"]
        expected_fonts = ["方正小标宋_GBK", "方正黑体_GBK", "方正楷体_GBK", "方正仿宋_GBK", "方正仿宋_GBK"]
        content = [document.paragraphs[1]] + document.paragraphs[3:]
        for index, (paragraph, kind, chinese) in enumerate(zip(content, kinds, expected_fonts)):
            size = 20 if index == 0 else 16
            bold = index in (2, 3)
            self.assertEqual(paragraph.style.name, formatter.SPECS[kind][0])
            fmt = paragraph.paragraph_format
            self.assertEqual(fmt.line_spacing_rule, WD_LINE_SPACING.EXACTLY)
            self.assertEqual(fmt.line_spacing, Pt(28))
            self.assertEqual(fmt.space_before, Pt(0))
            self.assertEqual(fmt.space_after, Pt(0))
            self.assertFalse(fmt.widow_control)
            self.assertEqual(fmt.first_line_indent, Pt(0 if kind == 'title' else 32))
            if kind != 'title':
                self.assertEqual(paragraph._p.pPr.ind.get(qn("w:firstLineChars")), "200")
            self.assertEqual(fmt.alignment, WD_ALIGN_PARAGRAPH.CENTER if kind == "title" else WD_ALIGN_PARAGRAPH.JUSTIFY if kind in ("body", "heading3") else WD_ALIGN_PARAGRAPH.LEFT)
            for run in ([] if kind == "heading3" else paragraph.runs):
                self.assertEqual(run.font.size, Pt(size))
                self.assertEqual(run.font.bold, bold)
                fonts = run._r.rPr.rFonts
                self.assertEqual(fonts.get(qn("w:eastAsia")), chinese)
                for name in ("ascii", "hAnsi", "cs"):
                    self.assertEqual(fonts.get(qn("w:" + name)), "Times New Roman")
                chars = run.text.replace("\n", "")
                self.assertTrue(not chars or all(ord(c) < 128 for c in chars) or all(ord(c) >= 128 for c in chars))
        h3 = document.paragraphs[5]
        self.assertEqual(h3.text, "1. 标题ABC3%2026年仅作原文数字测试；ABC 12.5%，不作事实陈述。\n保留第二行！")
        self.assertEqual(h3.style.name, "Official Heading 3")
        self.assertEqual(h3.alignment, WD_ALIGN_PARAGRAPH.JUSTIFY)
        self.assertTrue(h3.runs[0].font.bold)
        self.assertFalse(h3.runs[-1].font.bold)
        self.assertEqual(document.paragraphs[-1].style.name, "Official Body")
        self.assertEqual(document.paragraphs[-1].text, "1. 这是普通段落，不自动识别为标题。")

    def test_quoted_ranges_preserve_text_and_use_body_format(self):
        title = '标题“示例ABC20%”后缀'
        blocks = [('heading1', '一、标题“重点ABC123%”后续'),
                  ('heading2', '（一）标题"引文XYZ5%"后续'),
                  ('heading3', '1. 三级“引文”后续'),
                  ('body', '正文“示例ABC%”尾部')]
        formatter.build_document(title, blocks).save(self.output)
        self.assertTrue(formatter.check_document(self.output)['ok'])
        document = Document(self.output)
        self.assertEqual([p.text for p in document.paragraphs], ["", title, ""] + [text for _, text in blocks])
        for paragraph in document.paragraphs:
            for run, (text, quoted, mark) in zip(paragraph.runs, formatter.segment_text(paragraph.text)):
                if quoted:
                    self.assertEqual(run.font.size, Pt(16))
                    self.assertFalse(run.font.bold)
                    self.assertEqual(run._r.rPr.rFonts.get(qn('w:eastAsia')), '方正仿宋_GBK')
                    self.assertEqual(run._r.rPr.rFonts.get(qn('w:ascii')), '方正仿宋_GBK' if mark else 'Times New Roman')
        with self.assertRaises(formatter.FormatError):
            formatter.build_document('未闭合“标题', [])
        with self.assertRaises(formatter.FormatError):
            formatter.build_document('标题', [('body', '不匹配”')])

    def test_page_field(self):
        self.generate()
        document = Document(self.output)
        footer = document.sections[0].footer.paragraphs[0]
        self.assertEqual(footer.text, "— 1 —")
        self.assertEqual(footer.alignment, WD_ALIGN_PARAGRAPH.CENTER)
        for run in footer.runs:
            self.assertEqual(run.font.size, Pt(14))
            for attr in ('ascii','hAnsi','eastAsia','cs'):
                self.assertEqual(run._r.rPr.rFonts.get(qn('w:'+attr)), '宋体')
        self.assertEqual(footer._p.xpath(".//w:instrText")[0].text.strip(), "PAGE")
        self.assertEqual([item.get(qn("w:fldCharType")) for item in footer._p.xpath(".//w:fldChar")], ["begin", "separate", "end"])
        self.assertEqual(document.settings._element.xpath("./w:updateFields")[0].get(qn("w:val")), "true")

    def test_json_block_types_and_literal_text(self):
        self.source = self.root / "input.json"
        types = ["recipient", "body", "heading1", "heading2", "heading3", "signature", "date"]
        texts = ["收文对象（非正式样例）：", "原样保留 | * [x](url) % ABC123；", "一级", "二级", "三级", "署名占位（非正式）", "日期占位（非正式）"]
        self.source.write_text(json.dumps({"title": "非正式JSON测试", "blocks": [{"type": kind, "text": text} for kind, text in zip(types, texts)]}, ensure_ascii=False), encoding="utf-8")
        self.generate()
        document = Document(self.output)
        self.assertEqual([p.text for p in document.paragraphs[3:]], texts)
        for p, kind in zip(document.paragraphs[3:], types):
            self.assertEqual(p.style.name, formatter.SPECS[kind][0])
        self.assertEqual(document.paragraphs[-1].alignment, WD_ALIGN_PARAGRAPH.RIGHT)

    def test_json_heading3_runs_into_following_body(self):
        self.source = self.root / "input.json"
        self.source.write_text(json.dumps({"title": "标题", "blocks": [
            {"type": "heading3", "text": "1. 小标题"},
            {"type": "body", "text": "后接正文。"},
        ]}, ensure_ascii=False), encoding="utf-8")
        self.generate()
        document = Document(self.output)
        paragraph = document.paragraphs[3]
        self.assertEqual(paragraph.text, "1. 小标题后接正文。")
        self.assertEqual(len(document.paragraphs), 4)
        self.assertEqual(paragraph.style.name, "Official Heading 3")
        self.assertTrue(paragraph.runs[0].bold)
        self.assertFalse(paragraph.runs[-1].bold)

    def test_reject_unsupported_markdown_without_output(self):
        cases = ["|a|b|\n|-|-|", "![图](x.png)", "![图][id]", "[链接](https://example.com)", "```python\nx=1\n```", "~~~\nx\n~~~", "    code", "- list", "> quote", "**bold**", "`code`", "<table>x</table>", "[id]: url", "---", "##### 太深", "# 第二个标题"]
        for text in cases:
            with self.subTest(text=text):
                self.source.write_text("# 非正式测试\n\n" + text, encoding="utf-8")
                with self.assertRaises(formatter.FormatError):
                    self.generate()
                self.assertFalse(self.output.exists())

    def test_invalid_json_and_no_silent_unknown_fields(self):
        cases = [{"title": "T", "blocks": [], "unknown": "keep me"}, {"title": "T", "blocks": [{"type": "table", "text": "x"}]}, {"title": "T", "blocks": [{"type": "body", "text": "x", "image": "x.png"}]}, {"title": "T", "blocks": [{"type": [], "text": "x"}]}, {"title": "T", "blocks": [{"type": "body", "text": "a\tb"}]}]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(formatter.FormatError):
                formatter.parse_json(json.dumps(data))

    def test_overwrite_requires_opt_in(self):
        self.output.write_bytes(b"existing-content")
        with self.assertRaisesRegex(formatter.FormatError, "overwrite"):
            self.generate()
        self.assertEqual(self.output.read_bytes(), b"existing-content")
        self.assertTrue(formatter.format_file(self.source, self.output, overwrite=True)["ok"])

    def test_same_input_output_always_rejected(self):
        for overwrite in (False, True):
            with self.assertRaisesRegex(formatter.FormatError, "must differ"):
                formatter.format_file(self.source, self.source, overwrite)
        self.assertTrue(self.source.read_text(encoding="utf-8").startswith("# 非正式"))

    def test_invalid_utf8_and_extension(self):
        self.source.write_bytes(b"\xff\xfe")
        with self.assertRaisesRegex(formatter.FormatError, "UTF-8"):
            self.generate()
        unknown = self.root / "unknown.xyz"
        unknown.write_text("# title", encoding="utf-8")
        with self.assertRaisesRegex(formatter.FormatError, "extension"):
            formatter.format_file(unknown, self.output)
        self.assertFalse(self.output.exists())

    def test_checker_detects_format_and_structure_drift(self):
        self.generate()
        def change_style(doc):
            doc.paragraphs[3].style = doc.styles["Normal"]
        def change_margin(doc):
            doc.sections[0].left_margin = Mm(20)
        def change_font(doc):
            doc.paragraphs[3].runs[0]._r.rPr.rFonts.set(qn("w:ascii"), "Arial")
        def change_size(doc):
            doc.paragraphs[1].runs[0].font.size = Pt(16)
        def change_bold(doc):
            doc.paragraphs[3].runs[0].font.bold = True
        def change_spacing(doc):
            doc.paragraphs[-1].paragraph_format.line_spacing = Pt(30)
        def change_indent(doc):
            doc.paragraphs[-1].paragraph_format.first_line_indent = Pt(0)
        def remove_field(doc):
            doc.sections[0].footer.paragraphs[0].text = "— 1 —"
        def wrong_field(doc):
            doc.sections[0].footer.paragraphs[0]._p.xpath(".//w:instrText")[0].text = "NUMPAGES"
        def add_table(doc):
            doc.add_table(rows=1, cols=1)
        original = self.output.read_bytes()
        for mutate in (change_style, change_margin, change_font, change_size, change_bold, change_spacing, change_indent, remove_field, wrong_field, add_table):
            with self.subTest(mutation=mutate.__name__):
                self.output.write_bytes(original)
                document = Document(self.output)
                mutate(document)
                document.save(self.output)
                with self.assertRaises(formatter.FormatError):
                    formatter.check_document(self.output)

    def test_cli_protocol(self):
        def invoke(args):
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = formatter.main(args)
            return code, stdout.getvalue(), stderr.getvalue()
        code, out, err = invoke(["--input", str(self.source), "--output", str(self.output)])
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(json.loads(out)["ok"])
        code, out, err = invoke(["--check", str(self.output)])
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(json.loads(out)["page_field"])
        code, out, err = invoke(["--input", str(self.source), "--output", str(self.output)])
        self.assertEqual((code, out), (2, ""))
        self.assertFalse(json.loads(err)["ok"])
        code, out, err = invoke(["--check", str(self.output), "--overwrite"])
        self.assertEqual((code, out), (2, ""))
        self.assertFalse(json.loads(err)["ok"])

if __name__ == "__main__":
    unittest.main(verbosity=2)
