"""Command-line front end for the local model workbench."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from .service import WorkbenchService


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _override(raw: str) -> tuple[str, Any]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("overrides must be NAME=JSON_VALUE")
    name, text = raw.split("=", 1)
    if not name:
        raise argparse.ArgumentTypeError("override name cannot be empty")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = text
    return name, value


def _overrides(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in values:
        if name in result:
            raise ValueError(f"duplicate override: {name}")
        result[name] = value
    return result


def _default_run_root(root: Path, recipe_id: str, action: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return root / "outputs" / "workbench" / f"{recipe_id}-{action}-{timestamp}"


def parser() -> argparse.ArgumentParser:
    out = argparse.ArgumentParser(
        prog="python -m model_workbench",
        description="Run allow-listed local model recipes without editing code.",
    )
    out.add_argument("--root", type=Path, default=_repo_root(), help=argparse.SUPPRESS)
    commands = out.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="list available recipes")
    listing.add_argument("--json", action="store_true")
    check = commands.add_parser("check", help="check setup and validate a recipe")
    check.add_argument("recipe")
    check.add_argument("--set", action="append", type=_override, default=[])
    for name in ("benchmark", "run"):
        command = commands.add_parser(name, help=f"{name} a recipe")
        command.add_argument("recipe")
        command.add_argument("--set", action="append", type=_override, default=[])
        command.add_argument("--output", type=Path)
    status = commands.add_parser("status", help="show a prior run manifest")
    status.add_argument("run_directory", type=Path)
    report = commands.add_parser("report", help="rebuild a prior run's HTML report")
    report.add_argument("run_directory", type=Path)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    service = WorkbenchService(args.root)
    try:
        if args.command == "list":
            recipes = service.list_recipes()
            if args.json:
                print(json.dumps([
                    {"id": item.id, "title": item.title, "category": item.category,
                     "description": item.description}
                    for item in recipes
                ], indent=2))
            else:
                for item in recipes:
                    print(f"{item.id}\t{item.title}\t{item.description}")
            return 0
        if args.command == "check":
            checks = service.check(args.recipe, _overrides(args.set))
            for check in checks:
                print(f"{check.level:5}  {check.id}: {check.message}")
            return 2 if any(item.level == "BLOCK" for item in checks) else 0
        if args.command in {"run", "benchmark"}:
            run_root = args.output or _default_run_root(service.root, args.recipe, args.command)
            values = _overrides(args.set)
            plan = service.resolve(args.recipe, args.command, values, run_root)
            manifest = service.execute(plan)
            print(f"{manifest['status']}: {run_root.resolve()}")
            print(f"Report: {(run_root.resolve() / 'report' / 'index.html')}")
            if args.command == "benchmark":
                target = service.resolve(args.recipe, "run", values, run_root.parent / "estimate-only")
                estimate = service.estimate(target, manifest)
                print(json.dumps(estimate.to_dict(), indent=2))
            return 0
        if args.command == "status":
            print(json.dumps(service.status(args.run_directory), indent=2))
            return 0
        if args.command == "report":
            print(service.build_report(args.run_directory))
            return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2
