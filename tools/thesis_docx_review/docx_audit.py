#!/usr/bin/env python3
"""Extract structure and table details from the thesis DOCX."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph


def iter_blocks(document: Document):
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield "paragraph", Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield "table", Table(child, document)


def cell_text(cell) -> str:
    return " ".join(p.text.strip() for p in cell.paragraphs if p.text.strip())


def heading_level(style_name: str) -> int | None:
    if not style_name:
        return None
    if style_name == "Title":
        return 0
    if style_name.startswith("Heading "):
        try:
            return int(style_name.split()[-1])
        except ValueError:
            return None
    return None


def table_widths(table: Table) -> list[int]:
    grid = table._tbl.tblGrid
    if grid is None:
        return []
    return [int(col.w) for col in grid.gridCol_lst if col.w]


def extract(path: Path) -> dict:
    doc = Document(path)
    out = {"paragraphs": [], "tables": [], "blocks": []}
    current_heading = []
    para_i = 0
    table_i = 0

    for kind, block in iter_blocks(doc):
        if kind == "paragraph":
            text = block.text.strip()
            style = block.style.name if block.style else ""
            level = heading_level(style)
            if level is not None and text:
                current_heading = [
                    h for h in current_heading if h["level"] < level
                ] + [{"level": level, "text": text}]
            if text:
                record = {
                    "index": para_i,
                    "style": style,
                    "heading_path": [h["text"] for h in current_heading],
                    "text": text,
                }
                out["paragraphs"].append(record)
                out["blocks"].append({"type": "paragraph", **record})
            para_i += 1
        else:
            rows = []
            for row in block.rows:
                rows.append([cell_text(cell) for cell in row.cells])
            max_cols = max((len(row) for row in rows), default=0)
            record = {
                "index": table_i,
                "heading_path": [h["text"] for h in current_heading],
                "rows": len(rows),
                "cols": max_cols,
                "widths": table_widths(block),
                "header": rows[0] if rows else [],
                "sample_rows": rows[:4],
            }
            out["tables"].append(record)
            out["blocks"].append({"type": "table", **record})
            table_i += 1
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("docx", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--text-out", type=Path)
    args = parser.parse_args()

    data = extract(args.docx)
    if args.json_out:
        args.json_out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if args.text_out:
        lines = []
        lines.append("# Headings and Paragraphs")
        for para in data["paragraphs"]:
            if para["style"].startswith("Heading") or para["style"] == "Title":
                lines.append(
                    f"P{para['index']:04d} [{para['style']}] "
                    + " > ".join(para["heading_path"])
                    + f" :: {para['text']}"
                )
            else:
                lines.append(f"P{para['index']:04d} [{para['style']}] {para['text']}")
        lines.append("\n# Tables")
        for tbl in data["tables"]:
            lines.append(
                f"T{tbl['index']:03d} heading={' > '.join(tbl['heading_path'])} "
                f"size={tbl['rows']}x{tbl['cols']} widths={tbl['widths']}"
            )
            lines.append(f"  header: {tbl['header']}")
            for row in tbl["sample_rows"][1:]:
                lines.append(f"  row: {row}")
        args.text_out.write_text("\n".join(lines), encoding="utf-8")
    if not args.json_out and not args.text_out:
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
