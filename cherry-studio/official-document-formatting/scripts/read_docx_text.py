#!/usr/bin/env python3
"""Extract plain text and tables from any .docx file for reading/reference."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from docx import Document


def extract_docx(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"File not found: {path}")
    if path.suffix.lower() != ".docx":
        raise ValueError(f"Expected a .docx file, got: {path.suffix} (if .doc/.wps, save as .docx first)")
    doc = Document(str(path))
    paragraphs = []
    for idx, p in enumerate(doc.paragraphs, 1):
        text = p.text.strip()
        if text:
            style_name = p.style.name if p.style and p.style.name else "Normal"
            paragraphs.append({"index": idx, "style": style_name, "text": text})
    tables = []
    for t_idx, table in enumerate(doc.tables, 1):
        rows = []
        for row in table.rows:
            rows.append([cell.text.strip() for cell in row.cells])
        tables.append({"table_index": t_idx, "rows": rows})
    full_text = "\n\n".join(p["text"] for p in paragraphs)
    return {
        "ok": True,
        "file": str(path.resolve()),
        "paragraph_count": len(paragraphs),
        "table_count": len(tables),
        "text": full_text,
        "paragraphs": paragraphs,
        "tables": tables,
    }


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Path to the .docx file to read")
    parser.add_argument("--json", action="store_true", help="Output structured JSON instead of plain text")
    try:
        args = parser.parse_args(argv)
        data = extract_docx(args.input)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(data["text"])
            if data["tables"]:
                print("\n--- 附表内容 ---")
                for t in data["tables"]:
                    print(f"\n[表格 {t['table_index']}]")
                    for r in t["rows"]:
                        print(" | ".join(r))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
