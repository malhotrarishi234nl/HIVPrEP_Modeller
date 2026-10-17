"""Small, dependency-free HTML summary for a workbench run."""

from __future__ import annotations

import csv
from html import escape
from pathlib import Path
from typing import Any, Mapping

from .manifest import load_manifest


def _table(rows: list[tuple[str, str]]) -> str:
    return "\n".join(
        f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>"
        for label, value in rows
    )


def _csv_snapshot(path: Path) -> str:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            first = next(reader, None)
            last = first
            count = 0
            if first is not None:
                count = 1
                for row in reader:
                    last = row
                    count += 1
    except (OSError, csv.Error) as exc:
        return f"<p>Could not preview output: {escape(str(exc))}</p>"
    if first is None:
        return "<p>The output CSV is empty.</p>"
    columns = list(first)[:12]
    head = "".join(f"<th>{escape(column)}</th>" for column in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(row.get(column, '')))}</td>" for column in columns) + "</tr>"
        for row in (first, last) if row is not None
    )
    return (
        f"<p>{count} rows. Showing first and last row; at most 12 columns.</p>"
        f"<div class='scroll'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def build_html_report(run_root: Path | str) -> Path:
    root = Path(run_root).resolve()
    manifest = load_manifest(root)
    rows = [
        ("Recipe", str(manifest.get("recipe_title", manifest.get("recipe_id", "")))),
        ("Action", str(manifest.get("action", ""))),
        ("Status", str(manifest.get("status", ""))),
        ("Created", str(manifest.get("created_utc", ""))),
        ("Elapsed seconds", str(manifest.get("elapsed_seconds", ""))),
        ("Completed units", str(manifest.get("completed_work_units", ""))),
    ]
    sections: list[str] = []
    for step in manifest.get("steps", []):
        if not isinstance(step, Mapping):
            continue
        output_dir = step.get("output_dir")
        if not isinstance(output_dir, str):
            continue
        csv_path = Path(output_dir) / "research_not_production_eligible_run_001.csv"
        relative = csv_path.relative_to(root) if csv_path.is_relative_to(root) else csv_path
        link = relative.as_posix()
        sections.append(
            f"<section><h2>{escape(str(step.get('id', 'Model output')))}</h2>"
            f"<p><a href='../{escape(link)}'>Open raw CSV</a></p>"
            f"{_csv_snapshot(csv_path)}</section>"
        )
    failure = manifest.get("failure")
    failure_html = (
        f"<section class='failure'><h2>Failure</h2><pre>{escape(str(failure))}</pre></section>"
        if failure else ""
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{escape(str(manifest.get('recipe_title', 'Model workbench report')))}</title>
<style>
body{{font:16px system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#18212b}}
table{{border-collapse:collapse;width:100%}}th,td{{padding:.55rem;border:1px solid #c8d0d8;text-align:left}}
th{{background:#eef2f5}}.scroll{{overflow:auto}}section{{margin:2rem 0}}.failure{{border-left:5px solid #b42318;padding-left:1rem}}
</style></head><body><h1>{escape(str(manifest.get('recipe_title', 'Model workbench report')))}</h1>
<p>This is a local research-run summary. It does not confer production eligibility.</p>
<table>{_table(rows)}</table>{failure_html}{''.join(sections)}</body></html>"""
    report_path = root / "report" / "index.html"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(".tmp")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(report_path)
    return report_path
