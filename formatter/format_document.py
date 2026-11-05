#!/usr/bin/env python3
"""Strict UTF-8 Markdown/JSON to official-layout DOCX with python-docx.

CLI: --input source --output result.docx [--overwrite] [--config-json JSON]
     --check result.docx [--input source] [--config-json JSON]
Success: JSON stdout, exit 0. Failure: JSON stderr, exit 2.
Blank lines separate Markdown paragraphs; single newlines remain line breaks.
No numbering, punctuation, dates, names, or other facts are rewritten.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt

LATIN = "Times New Roman"
INDENT_KINDS = {"heading1", "heading2", "body"}
SPECS = {
    "title": ("Official Title", "方正小标宋_GBK", 20, False, WD_ALIGN_PARAGRAPH.CENTER),
    "heading1": ("Official Heading 1", "方正黑体_GBK", 16, False, WD_ALIGN_PARAGRAPH.LEFT),
    "heading2": ("Official Heading 2", "方正楷体_GBK", 16, True, WD_ALIGN_PARAGRAPH.LEFT),
    "heading3": ("Official Heading 3", "方正仿宋_GBK", 16, True, WD_ALIGN_PARAGRAPH.LEFT),
    "body": ("Official Body", "方正仿宋_GBK", 16, False, WD_ALIGN_PARAGRAPH.JUSTIFY),
    "recipient": ("Official Recipient", "方正仿宋_GBK", 16, False, WD_ALIGN_PARAGRAPH.LEFT),
    "signature": ("Official Signature", "方正仿宋_GBK", 16, False, WD_ALIGN_PARAGRAPH.RIGHT),
    "date": ("Official Date", "方正仿宋_GBK", 16, False, WD_ALIGN_PARAGRAPH.RIGHT),
}
FOOTER_STYLE = "Official Footer"
INLINE_BODY_STYLE = "Official Inline Body"

class FormatError(ValueError):
    """Unsupported input or invalid document structure."""


def resolve_config(overrides=None):
    """Validate explicit per-operation overrides without mutating defaults."""
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise FormatError("Config must be an object")
    defaults = {"line_spacing_pt": 28, "top_margin_mm": 25, "bottom_margin_mm": 25,
                "left_margin_mm": 25, "right_margin_mm": 25, "latin_font": LATIN,
                "styles": {kind: {"font": spec[1], "size_pt": spec[2], "bold": spec[3]}
                           for kind, spec in SPECS.items()}}
    if set(overrides) - set(defaults):
        raise FormatError("Unknown config fields: " + ", ".join(sorted(set(overrides) - set(defaults))))
    def number(value, label, lower, upper, half=False):
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(value) or not lower <= value <= upper or
                (half and value * 2 != int(value * 2))):
            raise FormatError(f"Invalid {label}")
        return value
    for key, value in overrides.items():
        if key == "styles":
            if not isinstance(value, dict) or set(value) - set(SPECS):
                raise FormatError("Invalid config styles")
            for kind, settings in value.items():
                if not isinstance(settings, dict) or set(settings) - {"font", "size_pt", "bold"}:
                    raise FormatError(f"Invalid config style {kind}")
                for field, setting in settings.items():
                    if field == "font":
                        if not isinstance(setting, str) or not setting.strip():
                            raise FormatError(f"Invalid {kind} font")
                    elif field == "size_pt":
                        number(setting, f"{kind} size", 1, 200, half=True)
                    elif not isinstance(setting, bool):
                        raise FormatError(f"Invalid {kind} bold")
                    defaults["styles"][kind][field] = setting
        elif key == "latin_font":
            if not isinstance(value, str) or not value.strip():
                raise FormatError("Invalid latin_font")
            defaults[key] = value
        else:
            defaults[key] = number(value, key, 1 if key == "line_spacing_pt" else 0,
                                   200 if key == "line_spacing_pt" else 100,
                                   half=key == "line_spacing_pt")
    return defaults


def validate_text(text, label, multiline=True):
    if not isinstance(text, str) or not text.strip():
        raise FormatError(f"{label}: expected nonempty text")
    if any(ord(c) < 32 and c != "\n" for c in text) or "\r" in text:
        raise FormatError(f"{label}: unsupported control character")
    if not multiline and "\n" in text:
        raise FormatError(f"{label}: expected a single line")
    return text


def parse_json(text):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FormatError(f"Invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"title", "blocks"}:
        raise FormatError("JSON requires exactly title and blocks")
    title = validate_text(data["title"], "title", False)
    if not isinstance(data["blocks"], list):
        raise FormatError("blocks must be an array")
    blocks = []
    for index, block in enumerate(data["blocks"]):
        if (isinstance(block, dict) and set(block) == {"type", "heading", "body"}
                and block["type"] in ("heading1", "heading2", "heading3")):
            heading = validate_text(block["heading"], f"blocks[{index}].heading", False)
            body = validate_text(block["body"], f"blocks[{index}].body")
            blocks.append((block["type"], {"heading": heading, "body": body}))
            continue
        if not isinstance(block, dict) or set(block) != {"type", "text"}:
            raise FormatError(f"blocks[{index}] requires exactly type and text")
        kind = block["type"]
        if not isinstance(kind, str) or kind not in SPECS or kind == "title":
            raise FormatError(f"Unsupported block type at blocks[{index}]")
        blocks.append((kind, validate_text(block["text"], f"blocks[{index}].text")))
    return title, blocks


def parse_markdown(text):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    title = None
    blocks = []
    pending = []
    def flush():
        if pending:
            blocks.append(("body", validate_text("\n".join(pending), "paragraph")))
            pending.clear()
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            flush()
            continue
        validate_text(line, f"line {line_no}")
        if (line.startswith(("    ", "\t")) or re.match(r"\s*(?:`{3,}|~{3,}|>|[-+*]\s)", line)
                or "|" in line or re.search(r"!?\[[^\]]*\](?:\(|\[|:)", line)
                or re.search(r"<[^>]+>|`|\*|(?<!\w)_(?:[^_]+)_(?!\w)|~~|\\[!#*`\[\]<>]", line)
                or re.match(r"\s*(?:-{3,}|_{3,}|\*{3,})\s*$", line)
                or re.match(r"\s*\[[^\]]+\]:", line)):
            raise FormatError(f"line {line_no}: unsupported Markdown structure")
        heading = re.match(r"^(#{1,4}) (.+)$", line)
        if line.lstrip().startswith("#") and not heading:
            raise FormatError(f"line {line_no}: unsupported heading syntax")
        if heading:
            flush()
            level, content = len(heading.group(1)), heading.group(2)
            if re.search(r"\s#+$", content):
                raise FormatError(f"line {line_no}: closing heading markers unsupported")
            if level == 1:
                if title is not None or blocks:
                    raise FormatError("Exactly one initial # title is required")
                title = validate_text(content, "title", False)
            else:
                if title is None:
                    raise FormatError("The first content must be # title")
                blocks.append((f"heading{level - 1}", content))
        else:
            if title is None:
                raise FormatError("The first content must be # title")
            pending.append(line)
    flush()
    if title is None:
        raise FormatError("Exactly one initial # title is required")
    return title, blocks


def load_source(path):
    path = Path(path)
    if path.suffix.lower() not in {".json", ".md", ".markdown"}:
        from import_document import ImportError as DocumentImportError, import_source
        try:
            return import_source(path)
        except DocumentImportError as exc:
            raise FormatError(str(exc)) from exc
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise FormatError(f"Cannot read UTF-8 input: {exc}") from exc
    if path.suffix.lower() == ".json":
        return parse_json(text)
    if path.suffix.lower() in {".md", ".markdown"}:
        return parse_markdown(text)
    raise FormatError("Input extension must be .md, .markdown or .json")


def set_font(owner, chinese, size, bold, latin=None):
    latin = latin or ("宋体" if chinese == "宋体" else LATIN)
    owner.font.name = latin
    owner.font.size = Pt(size)
    owner.font.bold = bold
    rpr = owner._element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    for attribute, value in {"ascii": latin, "hAnsi": latin, "eastAsia": chinese, "cs": latin}.items():
        fonts.set(qn("w:" + attribute), value)
    for attr in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        fonts.attrib.pop(qn("w:" + attr), None)
    size_cs = rpr.find(qn("w:szCs"))
    if size_cs is None:
        size_cs = OxmlElement("w:szCs")
        rpr.append(size_cs)
    size_cs.set(qn("w:val"), str(int(size * 2)))


def set_paragraph_format(owner, kind, config=None):
    config = resolve_config(config)
    fmt = owner.paragraph_format
    fmt.alignment = SPECS[kind][4] if kind in SPECS else WD_ALIGN_PARAGRAPH.CENTER
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(0)
    fmt.line_spacing = Pt(config["line_spacing_pt"])
    fmt.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    fmt.widow_control = False
    pPr = owner._element.get_or_add_pPr()
    widow = pPr.find(qn("w:widowControl"))
    if widow is None:
        widow = OxmlElement("w:widowControl")
        pPr.append(widow)
    widow.set(qn("w:val"), "0")
    if kind in INDENT_KINDS:
        fmt.first_line_indent = Pt(config["styles"][kind]["size_pt"] * 2)
        ind = pPr.find(qn("w:ind"))
        if ind is None:
            ind = OxmlElement("w:ind")
            pPr.append(ind)
        ind.set(qn("w:firstLineChars"), "200")
    else:
        fmt.first_line_indent = Pt(0)
        ind = pPr.find(qn("w:ind"))
        if ind is not None:
            ind.attrib.pop(qn("w:firstLineChars"), None)
    fmt.left_indent = Pt(0)
    fmt.right_indent = Pt(0)


def segment_text(text):
    """Yield runs grouped by quoted range and character class without editing text."""
    stack = []
    tokens = []
    for char in text:
        if char == '“':
            stack.append('”')
            quoted = True
        elif char == '”':
            if not stack or stack[-1] != '”':
                raise FormatError('Unmatched Chinese double quote')
            quoted = True
            stack.pop()
        elif char == '"':
            quoted = True
            if stack and stack[-1] == '"':
                stack.pop()
            else:
                stack.append('"')
        else:
            quoted = bool(stack)
        quote_mark = char in {'“', '”', '"'} and quoted
        ascii_char = ord(char) < 128
        key = (quoted, quote_mark, ascii_char)
        if tokens and tokens[-1][1] == key:
            tokens[-1] = (tokens[-1][0] + char, key)
        else:
            tokens.append((char, key))
    if stack:
        raise FormatError('Unmatched double quote; quoted text must close within each paragraph')
    return [(text, quoted, quote_mark) for text, (quoted, quote_mark, _) in tokens]


def styled_segments(text, boundary=None):
    """Segment a whole paragraph first, then split the explicit body boundary."""
    offset = 0
    for segment, quoted, mark in segment_text(text):
        end = offset + len(segment)
        if boundary is not None and offset < boundary < end:
            yield segment[:boundary - offset], quoted, mark, False
            yield segment[boundary - offset:], quoted, mark, True
        else:
            yield segment, quoted, mark, boundary is not None and offset >= boundary
        offset = end


def build_document(title, blocks, config=None):
    config = resolve_config(config)
    document = Document()
    document.core_properties.title = title
    document.core_properties.subject = "受控文本排版"
    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin = Mm(config["top_margin_mm"])
    section.bottom_margin = Mm(config["bottom_margin_mm"])
    section.left_margin = Mm(config["left_margin_mm"])
    section.right_margin = Mm(config["right_margin_mm"])
    section.different_first_page_header_footer = False
    document.settings.odd_and_even_pages_header_footer = False
    document.styles.add_style(INLINE_BODY_STYLE, WD_STYLE_TYPE.CHARACTER)
    for kind, (name, _, _, _, _) in SPECS.items():
        style = document.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = document.styles["Normal"]
        font = config["styles"][kind]
        set_font(style, font["font"], font["size_pt"], font["bold"], config["latin_font"])
        set_paragraph_format(style, kind, config)
        if kind.startswith("heading"):
            outline = OxmlElement("w:outlineLvl")
            outline.set(qn("w:val"), str(int(kind[-1]) - 1))
            style._element.get_or_add_pPr().append(outline)
    for kind, content in [("title", title), *blocks]:
        paragraph = document.add_paragraph(style=SPECS[kind][0])
        set_paragraph_format(paragraph, kind, config)
        if isinstance(content, dict):
            boundary = len(content["heading"])
            text = content["heading"] + content["body"]
        else:
            boundary, text = None, content
        for segment, quoted, quote_mark, inline_body in styled_segments(text, boundary):
            font = config["styles"]["body" if quoted or inline_body else kind]
            run = paragraph.add_run(segment)
            if inline_body:
                run.style = INLINE_BODY_STYLE
            set_font(run, font["font"], font["size_pt"], False if quoted else font["bold"],
                     latin=font["font"] if quote_mark else config["latin_font"])
    style = document.styles.add_style(FOOTER_STYLE, WD_STYLE_TYPE.PARAGRAPH)
    set_font(style, "宋体", 14, False)
    set_paragraph_format(style, "footer", config)
    paragraph = section.footer.paragraphs[0]
    paragraph.style = style
    set_paragraph_format(paragraph, "footer", config)
    for text in ("— ", None, " —"):
        run = paragraph.add_run(text or "")
        set_font(run, "宋体", 14, False)
        if text is None:
            for tag, value in (("begin", None), ("instruction", " PAGE "), ("separate", None), ("text", "1"), ("end", None)):
                element = OxmlElement("w:instrText" if tag == "instruction" else "w:t" if tag == "text" else "w:fldChar")
                if tag in {"instruction", "text"}:
                    element.text = value
                    element.set(qn("xml:space"), "preserve")
                else:
                    element.set(qn("w:fldCharType"), tag)
                run._r.append(element)
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "true")
    document.settings._element.append(update)
    return document


def check_document(path, config=None):
    """Validate the strict generated document contract; raise FormatError on drift."""
    config = resolve_config(config)
    try:
        document = Document(str(path))
    except Exception as exc:
        raise FormatError(f"Cannot open DOCX: {exc}") from exc
    errors = []
    def require(condition, message):
        if not condition:
            errors.append(message)
    def check_format(owner, kind, label):
        fmt = owner.paragraph_format
        require(fmt.line_spacing_rule == WD_LINE_SPACING.EXACTLY and fmt.line_spacing == Pt(config["line_spacing_pt"]), f"{label}: fixed line spacing")
        require(fmt.space_before == Pt(0) and fmt.space_after == Pt(0), f"{label}: zero paragraph spacing")
        require(fmt.widow_control is False, f"{label}: widow control disabled")
        widow = owner._element.xpath("./w:pPr/w:widowControl")
        require(len(widow) == 1 and widow[0].get(qn("w:val")) == "0", f"{label}: xml widow control disabled")
        alignment = SPECS[kind][4] if kind in SPECS else WD_ALIGN_PARAGRAPH.CENTER
        require(fmt.alignment == alignment, f"{label}: alignment")
        require(fmt.left_indent == Pt(0) and fmt.right_indent == Pt(0), f"{label}: side indent")
        require(fmt.first_line_indent == (Pt(config["styles"][kind]["size_pt"] * 2) if kind in INDENT_KINDS else Pt(0)), f"{label}: first line indent")
        ind = owner._element.get_or_add_pPr().find(qn("w:ind"))
        require(ind is not None and (ind.get(qn("w:firstLineChars")) == "200" if kind in INDENT_KINDS else not ind.get(qn("w:firstLineChars"))), f"{label}: character indent")
    def check_font(owner, chinese, size, bold, label, latin=None):
        rpr = owner._element.rPr
        fonts = None if rpr is None else rpr.find(qn("w:rFonts"))
        latin = latin or ("宋体" if chinese == "宋体" else LATIN)
        require(fonts is not None and all(fonts.get(qn("w:" + attr)) == value for attr, value in {"ascii": latin, "hAnsi": latin, "eastAsia": chinese, "cs": latin}.items()), f"{label}: fonts")
        require(owner.font.size == Pt(size) and owner.font.bold == bold, f"{label}: size/bold")
        size_cs = None if rpr is None else rpr.find(qn("w:szCs"))
        require(size_cs is not None and size_cs.get(qn("w:val")) == str(int(size * 2)), f"{label}: complex-script size")
    require(len(document.sections) == 1, "exactly one section required")
    require(not document.tables, "tables unsupported")
    require(not document.inline_shapes and not document._element.xpath(".//w:drawing | .//w:pict | .//w:object"), "images/objects unsupported")
    require(all(element.tag in {qn("w:p"), qn("w:sectPr")} for element in document._element.body), "unsupported body structure")
    name_to_kind = {spec[0]: kind for kind, spec in SPECS.items()}
    paragraphs = document.paragraphs
    require(bool(paragraphs), "document is empty")
    title_count = 0
    for index, paragraph in enumerate(paragraphs):
        label = f"paragraph {index + 1}"
        kind = name_to_kind.get(paragraph.style.name)
        if kind is None:
            errors.append(f"{label}: unsupported paragraph style")
            continue
        if kind == "title":
            title_count += 1
        require(index != 0 or kind == "title", "first paragraph must be title")
        require(bool(paragraph.text.strip()), f"{label}: empty paragraph")
        require(all(child.tag in {qn("w:pPr"), qn("w:r")} for child in paragraph._p), f"{label}: unsupported inline structure")
        check_format(paragraph, kind, label)
        check_format(paragraph.style, kind, f"style {paragraph.style.name}")
        font = config["styles"][kind]
        check_font(paragraph.style, font["font"], font["size_pt"], font["bold"],
                   f"style {paragraph.style.name}", config["latin_font"])
        if kind.startswith("heading"):
            outlines = paragraph.style._element.xpath("./w:pPr/w:outlineLvl")
            require(len(outlines) == 1 and outlines[0].get(qn("w:val")) == str(int(kind[-1]) - 1), f"{label}: outline level")
        flags = [run.style.name == INLINE_BODY_STYLE for run in paragraph.runs]
        boundary = None
        if any(flags):
            first = flags.index(True)
            require(kind.startswith("heading") and first > 0 and all(flags[first:]), f"{label}: invalid inline body boundary")
            boundary = sum(len(run.text) for run in paragraph.runs[:first])
        expected = list(styled_segments(paragraph.text, boundary))
        require(len(paragraph.runs) == len(expected), f"{label}: text run segmentation")
        for run, (segment, quoted, quote_mark, inline_body) in zip(paragraph.runs, expected):
            require(run.text == segment, f"{label}: text run content")
            require((run.style.name == INLINE_BODY_STYLE) == inline_body, f"{label}: inline body style")
            run_font = config["styles"]["body" if quoted or inline_body else kind]
            check_font(run, run_font["font"], run_font["size_pt"], False if quoted else run_font["bold"],
                       f"{label}: run", run_font["font"] if quote_mark else config["latin_font"])
            require(all(child.tag in {qn("w:rPr"), qn("w:t"), qn("w:br")} for child in run._r), f"{label}: unsupported run content")
    require(title_count == 1, "exactly one title required")
    for section in document.sections:
        require(abs(section.page_width - Mm(210)) < 700 and abs(section.page_height - Mm(297)) < 700, "A4 page required")
        require(all(abs(value - Mm(config[key])) < 700 for value, key in ((section.top_margin, "top_margin_mm"), (section.bottom_margin, "bottom_margin_mm"), (section.left_margin, "left_margin_mm"), (section.right_margin, "right_margin_mm"))), "configured margins required")
        require(not section.different_first_page_header_footer and not document.settings.odd_and_even_pages_header_footer, "uniform footer required")
        require(len(section.footer.paragraphs) == 1 and not section.footer.tables, "one footer paragraph required")
        footer = section.footer.paragraphs[0]
        require(footer.style.name == FOOTER_STYLE, "footer style")
        check_format(footer, "footer", "footer")
        check_format(footer.style, "footer", "footer style")
        check_font(footer.style, "宋体", 14, False, "footer style")
        require(footer.text == "— 1 —", "footer must be — PAGE —")
        require(len(footer.runs) == 3 and [r.text for r in footer.runs] == ["— ", "1", " —"], "footer run structure")
        for run in footer.runs:
            check_font(run, "宋体", 14, False, "footer")
        if len(footer.runs) == 3:
            nodes = [child for child in footer.runs[1]._r if child.tag != qn("w:rPr")]
            require([node.tag for node in nodes] == [qn("w:fldChar"), qn("w:instrText"), qn("w:fldChar"), qn("w:t"), qn("w:fldChar")], "PAGE field sequence")
            require([node.get(qn("w:fldCharType")) for node in nodes if node.tag == qn("w:fldChar")] == ["begin", "separate", "end"], "PAGE field markers")
            require([node.text.strip() for node in nodes if node.tag == qn("w:instrText")] == ["PAGE"], "automatic PAGE field required")
    if errors:
        raise FormatError("DOCX check failed: " + "; ".join(dict.fromkeys(errors)))
    return {"ok": True, "paragraphs": len(paragraphs), "sections": len(document.sections), "page_field": True}


def format_file(source, output, overwrite=False, config=None):
    config = resolve_config(config)
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve() or (source.exists() and output.exists() and os.path.samefile(source, output)):
        raise FormatError("Input and output paths must differ")
    if output.suffix.lower() != ".docx":
        raise FormatError("Output extension must be .docx")
    if output.exists() and not overwrite:
        raise FormatError("Output already exists; use --overwrite explicitly")
    title, blocks = load_source(source)
    document = build_document(title, blocks, config)
    try:
        with output.open("wb" if overwrite else "xb") as stream:
            document.save(stream)
    except OSError as exc:
        raise FormatError(f"Cannot write output: {exc}") from exc
    result = check_document(output, config)
    from import_document import compare_content
    content_check = compare_content(title, blocks, output)
    return {**result, "content_check": content_check, "output": str(output.resolve()), "blocks": len(blocks)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--check", type=Path, metavar="DOCX")
    parser.add_argument("--config-json", help="Explicit per-operation JSON configuration overrides")
    args = parser.parse_args(argv)
    try:
        config = resolve_config(json.loads(args.config_json) if args.config_json is not None else None)
        if args.check:
            if args.output or args.overwrite:
                raise FormatError("--check cannot be combined with --output or --overwrite")
            result = {**check_document(args.check, config), "input": str(args.check.resolve())}
            if args.input:
                from import_document import compare_content
                title, blocks = load_source(args.input)
                result["content_check"] = compare_content(title, blocks, args.check)
                result["source"] = str(args.input.resolve())
        else:
            if not args.input or not args.output:
                raise FormatError("Generation requires --input and --output")
            result = format_file(args.input, args.output, args.overwrite, config)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (FormatError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == "__main__":
    sys.exit(main())
