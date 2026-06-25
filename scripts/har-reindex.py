#!/usr/bin/env python3
"""
har-reindex.py — Rebuild action registry and aggregates from all contributor entries.

This script is designed to run as a GitHub Action after a PR merges.
It reads ALL JSONL files under entries/ (across all contributors),
then regenerates:

    actions/{slug}.md          — One file per action with aggregate stats
    aggregates/latest.json     — Global stats + per-action + per-location breakdowns
    aggregates/index.json      — Quick summary
    aggregates/action-registry-index.json  — Lightweight agent lookup

Usage:
    python3 scripts/har-reindex.py                    # Rebuild everything in-place
    python3 scripts/har-reindex.py --data-dir PATH    # Custom public-actions directory
    python3 scripts/har-reindex.py --dry-run          # Show stats without writing
"""

from __future__ import annotations

import json
import os
import sys
import argparse
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_ACTIONS_DIR = REPO_ROOT / "public-actions"


def _load_all_entries(public_actions_dir: Path) -> list[dict[str, Any]]:
    """Read all JSONL files from entries/{contributor}/... and return flattened list."""
    entries: list[dict[str, Any]] = []
    entries_dir = public_actions_dir / "entries"
    if not entries_dir.exists():
        return entries

    for jsonl_file in sorted(entries_dir.rglob("*.jsonl")):
        try:
            with open(jsonl_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        entries.append(obj)
                    except json.JSONDecodeError:
                        print(f"  Warning: skipping malformed line in {jsonl_file}", file=sys.stderr)
        except (OSError, IOError) as e:
            print(f"  Warning: could not read {jsonl_file}: {e}", file=sys.stderr)

    return entries


def _contributor_list(public_actions_dir: Path) -> list[str]:
    """Return list of unique contributor IDs with entries."""
    entries_dir = public_actions_dir / "entries"
    if not entries_dir.exists():
        return []
    return sorted(
        d.name for d in entries_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def _compute_action_aggregates(
    public_actions_dir: Path,
    action_id: str,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute aggregate stats for one action from its entries."""
    total_entries = len(entries)
    total_duration = sum(e.get("duration", 0) for e in entries)
    unique_dates = sorted({e["date"] for e in entries if e.get("date")})
    total_participants = len(unique_dates)

    # Per-location breakdown
    by_location: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total_entries": 0, "total_duration": 0, "unique_dates": set()}
    )
    for e in entries:
        loc = e.get("location", "__unknown__")
        by_location[loc]["total_entries"] += 1
        by_location[loc]["total_duration"] += e.get("duration", 0)
        if e.get("date"):
            by_location[loc]["unique_dates"].add(e["date"])

    per_location = {}
    for loc, data in by_location.items():
        key = loc if loc != "__unknown__" else None
        per_location[key] = {
            "total_entries": data["total_entries"],
            "total_duration": data["total_duration"],
            "unique_dates": sorted(data["unique_dates"]),
        }

    # Collect exercise names from existing action file if present
    exercises: list[str] = []
    action_path = public_actions_dir / "actions" / f"{action_id}.md"
    if action_path.exists():
        existing_exercises = _read_action_exercises(action_path)
        if existing_exercises:
            exercises = existing_exercises

    result: dict[str, Any] = {
        "action": action_id.replace("-", " ").title(),
        "total_participants": total_participants,
        "total_entries": total_entries,
        "total_duration_minutes": total_duration,
        "per_location": per_location,
        "slug": action_id,
    }

    if exercises:
        result["exercises"] = exercises

    return result


def _read_action_exercises(path: Path) -> list[str]:
    """Read exercises list from an existing action file frontmatter."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return []
    parts = text.split("---", 2)
    if len(parts) < 3:
        return []

    fm_text = parts[1]
    # Simple parser for exercises: list items
    in_exercises = False
    exercises: list[str] = []
    for line in fm_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("exercises:"):
            in_exercises = True
        elif in_exercises:
            if stripped.startswith("- "):
                item = stripped[2:].strip().strip('"').strip("'")
                if item:
                    exercises.append(item)
            elif ":" in stripped:
                in_exercises = False
    return exercises


def _write_yaml_frontmatter(path: Path, frontmatter: dict[str, Any], body: str = "") -> None:
    """Write frontmatter + body to a markdown file, handling nested dicts/lists."""
    def _yaml_val(v: Any, indent: int = 0) -> list[str]:
        pad = "  " * indent
        lines: list[str] = []
        if isinstance(v, dict):
            if not v:
                lines.append(f"{pad}{{}}")
            else:
                for k, val in v.items():
                    if isinstance(val, (dict, list)):
                        lines.append(f"{pad}{k}:")
                        lines.extend(_yaml_val(val, indent + 1))
                    elif isinstance(val, bool):
                        lines.append(f"{pad}{k}: {str(val).lower()}")
                    elif isinstance(val, (int, float)):
                        lines.append(f"{pad}{k}: {val}")
                    elif val is None:
                        lines.append(f"{pad}{k}: null")
                    else:
                        lines.append(f'{pad}{k}: "{val}"')
        elif isinstance(v, list):
            if not v:
                lines.append(f"{pad}[]")
            else:
                for item in v:
                    if isinstance(item, (dict, list)):
                        lines.append(f"{pad}- ")
                        lines.extend(_yaml_val(item, indent + 1))
                    elif isinstance(item, bool):
                        lines.append(f"{pad}- {str(item).lower()}")
                    elif isinstance(item, (int, float)):
                        lines.append(f"{pad}- {item}")
                    else:
                        lines.append(f'{pad}- "{item}"')
        return lines

    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, (dict, list)):
            lines.append(f"{key}:")
            lines.extend(_yaml_val(value, 1))
        elif isinstance(value, bool):
            lines.append(f"{key}: {str(value).lower()}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        elif value is None:
            lines.append(f"{key}: null")
        else:
            lines.append(f'{key}: "{value}"')
    lines.append("---")
    if body:
        lines.append("")
        lines.append(body.strip())
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def rebuild_action_registry(public_actions_dir: Path, all_entries: list[dict[str, Any]]) -> list[str]:
    """Rebuild all action files from scratch.

    Returns list of action slugs that were created/updated.
    """
    actions_dir = public_actions_dir / "actions"
    actions_dir.mkdir(parents=True, exist_ok=True)

    # Group entries by action_id
    by_action: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in all_entries:
        aid = e.get("action_id", "__unknown__")
        if aid == "__unknown__":
            continue
        slug = aid.lower().replace(" ", "-").replace("/", "-")
        by_action[slug].append(e)

    # Read existing frontmatter to preserve first_reporter
    first_reporters: dict[str, str] = {}
    for md_file in actions_dir.glob("*.md"):
        text = md_file.read_text(encoding="utf-8")
        if "first_reporter:" in text:
            for line in text.splitlines():
                if "first_reporter:" in line:
                    name = line.split(":", 1)[1].strip().strip('"').strip("'")
                    if name:
                        first_reporters[md_file.stem] = name

    updated_slugs: list[str] = []
    for slug in sorted(by_action):
        entries = by_action[slug]
        agg = _compute_action_aggregates(public_actions_dir, slug, entries)

        # Preserve first_reporter
        if slug in first_reporters:
            agg["first_reporter"] = first_reporters[slug]

        # Build body with contributor info
        contributors = _contributor_list(public_actions_dir)
        body_parts = ["## About", ""]
        if slug in first_reporters:
            body_parts.append(f"First reported by: {first_reporters[slug]}")
        if len(contributors) > 1:
            num_contributors = len({e["action_id"] for e in entries if e.get("action_id") == slug})
            body_parts.append(f"")
            body_parts.append(f"This action is contributed through HAR's opt-in public record system.")
        body = "\n".join(body_parts)

        action_path = actions_dir / f"{slug}.md"
        _write_yaml_frontmatter(action_path, agg, body)
        updated_slugs.append(slug)

    # Remove stale action files (slugs that no longer have entries)
    for md_file in list(actions_dir.glob("*.md")):
        if md_file.stem not in by_action:
            md_file.unlink()
            print(f"  Removed stale action: {md_file.stem}")

    return updated_slugs


def rebuild_aggregates(public_actions_dir: Path, all_entries: list[dict[str, Any]]) -> None:
    """Rebuild aggregates/ directory with global stats."""
    aggregates_dir = public_actions_dir / "aggregates"
    aggregates_dir.mkdir(parents=True, exist_ok=True)

    total_entries = len(all_entries)
    total_duration = sum(e.get("duration", 0) for e in all_entries)
    unique_dates = sorted({e["date"] for e in all_entries if e.get("date")})
    total_participants = len(unique_dates)

    # Per-action breakdown
    by_action: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in all_entries:
        aid = e.get("action_id", "__unknown__")
        by_action[aid].append(e)

    action_breakdown: list[dict[str, Any]] = []
    for action_id in sorted(by_action):
        action_entries = by_action[action_id]
        action_total_dur = sum(e.get("duration", 0) for e in action_entries)
        action_unique_dates = {e["date"] for e in action_entries if e.get("date")}
        action_breakdown.append({
            "action_id": action_id,
            "total_entries": len(action_entries),
            "total_duration_minutes": action_total_dur,
            "total_participants": len(action_unique_dates),
        })

    # Per-location breakdown
    by_location: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total_entries": 0, "total_duration": 0, "unique_dates": set()}
    )
    for e in all_entries:
        loc = e.get("location", "__unknown__")
        by_location[loc]["total_entries"] += 1
        by_location[loc]["total_duration"] += e.get("duration", 0)
        if e.get("date"):
            by_location[loc]["unique_dates"].add(e["date"])

    location_breakdown: list[dict[str, Any]] = []
    for loc in sorted(by_location):
        data = by_location[loc]
        location_breakdown.append({
            "location": loc if loc != "__unknown__" else None,
            "total_entries": data["total_entries"],
            "total_duration_minutes": data["total_duration"],
            "total_participants": len(data["unique_dates"]),
        })

    # Write latest.json
    latest: dict[str, Any] = {
        "generated": datetime.now().isoformat(),
        "total_entries": total_entries,
        "total_duration_minutes": total_duration,
        "total_participants": total_participants,
        "contributors": _contributor_list(public_actions_dir),
        "date_range": {
            "start": unique_dates[0] if unique_dates else "",
            "end": unique_dates[-1] if unique_dates else "",
        },
        "by_action": action_breakdown,
        "by_location": location_breakdown,
    }
    (aggregates_dir / "latest.json").write_text(
        json.dumps(latest, indent=2) + "\n", encoding="utf-8"
    )

    # Write index.json (quick summary)
    index: dict[str, Any] = {
        "generated": datetime.now().isoformat(),
        "summary": f"{total_entries} entries · {total_duration} minutes · {len(unique_dates)} contributing days · {len(_contributor_list(public_actions_dir))} contributors",
        "total_entries": total_entries,
        "total_duration_minutes": total_duration,
        "total_participants": total_participants,
        "contributors": _contributor_list(public_actions_dir),
    }
    (aggregates_dir / "index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )


def rebuild_registry_index(public_actions_dir: Path) -> None:
    """Rebuild action-registry-index.json from action files."""
    aggregates_dir = public_actions_dir / "aggregates"
    aggregates_dir.mkdir(parents=True, exist_ok=True)

    actions_dir = public_actions_dir / "actions"
    if not actions_dir.exists():
        (aggregates_dir / "action-registry-index.json").write_text(
            "[]\n", encoding="utf-8"
        )
        return

    registry_list: list[dict[str, Any]] = []
    for md_file in sorted(actions_dir.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue

        # Simple frontmatter parsing
        fm: dict[str, Any] = {}
        for line in parts[1].splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if ": " in stripped:
                key, val_str = stripped.split(": ", 1)
                key = key.strip()
                val_str = val_str.strip().strip('"').strip("'")
                if val_str.lower() == "true":
                    val_str = True
                elif val_str.lower() == "false":
                    val_str = False
                try:
                    if "." in val_str:
                        val_str = float(val_str)
                    else:
                        val_str = int(val_str)
                except (ValueError, TypeError):
                    pass
                fm[key] = val_str

        slug = fm.get("slug", md_file.stem)
        entry: dict[str, Any] = {
            "slug": slug,
            "title": fm.get("action", slug.replace("-", " ").title()),
            "total_entries": fm.get("total_entries", 0),
            "total_participants": fm.get("total_participants", 0),
        }

        reporter = fm.get("first_reporter", "")
        if reporter:
            entry["first_reporter"] = reporter

        registry_list.append(entry)

    (aggregates_dir / "action-registry-index.json").write_text(
        json.dumps(registry_list, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild action registry and aggregates from all contributor entries."
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        metavar="DIR",
        help=f"Path to public-actions directory (default: {DEFAULT_PUBLIC_ACTIONS_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show stats without writing anything",
    )
    args = parser.parse_args()

    public_actions_dir = (
        Path(args.data_dir).expanduser().resolve()
        if args.data_dir
        else DEFAULT_PUBLIC_ACTIONS_DIR.resolve()
    )

    if not public_actions_dir.exists():
        print(f"Error: {public_actions_dir} not found.", file=sys.stderr)
        return 1

    print(f"Loading entries from: {public_actions_dir / 'entries'}")
    all_entries = _load_all_entries(public_actions_dir)
    contributors = _contributor_list(public_actions_dir)

    print(f"  Found {len(all_entries)} entries from {len(contributors)} contributor(s): {', '.join(contributors)}")

    if not all_entries:
        print("  No entries to index.")
        return 0

    total_duration = sum(e.get("duration", 0) for e in all_entries)
    unique_dates = sorted({e["date"] for e in all_entries if e.get("date")})
    print(f"  {total_duration} total minutes across {len(unique_dates)} days")

    if args.dry_run:
        print("\n=== DRY RUN ===")
        print(f"Would rebuild {len(contributors)} contributor entries into actions/ and aggregates/")
        by_action: dict[str, int] = defaultdict(int)
        for e in all_entries:
            aid = e.get("action_id", "__unknown__")
            by_action[aid] += 1
        print(f"  Action files to update: {len(by_action)}")
        for aid, cnt in sorted(by_action.items()):
            print(f"    {aid}: {cnt} entries")
        print("\n(Dry run — no files were written)")
        return 0

    # Rebuild action registry
    print("\nRebuilding action registry...")
    updated = rebuild_action_registry(public_actions_dir, all_entries)
    print(f"  Updated {len(updated)} action files: {', '.join(updated[:5])}{'...' if len(updated) > 5 else ''}")

    # Rebuild aggregates
    print("Rebuilding aggregates...")
    rebuild_aggregates(public_actions_dir, all_entries)
    print("  Updated aggregates.")

    # Rebuild registry index
    print("Rebuilding action registry index...")
    rebuild_registry_index(public_actions_dir)
    print("  Updated action-registry-index.json.")

    print(f"\nDone. {len(all_entries)} entries indexed from {len(contributors)} contributor(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
