#!/usr/bin/env python3
"""
har-contribute.py — Anonymization & Contribution Pipeline for HAR Public Actions

Reads HAR action entries, anonymizes them, and writes them to the public-actions
repo for opt-in public contribution.

Usage:
    har-contribute.py                  # Dry-run by default
    har-contribute.py --push           # Commit and push to GitHub
    har-contribute.py --since 2026-06-01 --push
    har-contribute.py --no-match       # Skip action matching, send raw entries
    har-contribute.py --dry-run        # Show what would happen without writing

Environment variables:
    HAR_PUBLIC_CONTRIBUTE=true         # Must be set to do anything
    HAR_PUBLIC_LOCATION="Cedar City, UT"  # Optional location string
    HAR_PUBLIC_DISPLAY_NAME="HappyBrain"  # Optional pseudonym for first-reporter credit
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HAR_ROOT = Path.home() / "HAR"
DERIVED_DIR = HAR_ROOT / "_derived"
PUBLIC_ACTIONS_DIR_ENV_VAR = "HAR_PUBLIC_ACTIONS_DIR"  # optional override
DEFAULT_PUBLIC_ACTIONS_DIR = HAR_ROOT / "public-actions"


def _public_actions_dir() -> Path:
    """Return the configured public-actions repo directory."""
    env_path = os.environ.get(PUBLIC_ACTIONS_DIR_ENV_VAR)
    if env_path:
        return Path(env_path).expanduser().resolve()
    return DEFAULT_PUBLIC_ACTIONS_DIR.resolve()


# ---------------------------------------------------------------------------
# Entry loading
# ---------------------------------------------------------------------------

def _load_derived_entries() -> list[dict[str, Any]]:
    """Load flattened action entries from _derived/har-data.json.

    Returns a list of dicts with fields:
        date, time, activity, category, stem, duration, custom_fields,
        notes, weekday (string), public_action_id (if set), time_bucket
    """
    data_path = DERIVED_DIR / "har-data.json"
    if not data_path.exists():
        sys.exit(
            f"Error: {data_path} not found. "
            "Run `build-har-derived.py` first to generate the derived data."
        )

    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)

    entries: list[dict[str, Any]] = []
    day_detail = data.get("day_detail", {})

    for ds, detail in sorted(day_detail.items()):
        weekday_str = detail.get("weekday", "")
        for actual in detail.get("actuals", []):
            entry = {
                "date": ds,
                "time": actual.get("time", ""),
                "activity": actual.get("activity", ""),
                "category": actual.get("category", ""),
                "stem": actual.get("stem", ""),
                "duration": actual.get("duration") or 0,
                "custom_fields": actual.get("custom_fields", {}) or {},
                "notes": actual.get("notes_body", "") or "",
                "weekday": weekday_str,
                "public_action_id": actual.get("public_action_id", ""),
            }
            entry["time_bucket"] = _compute_time_bucket(entry["time"])
            entries.append(entry)

    return entries


def _compute_time_bucket(time_str: str) -> str:
    """Convert an HH:MM time string into a time-of-day bucket.

    Buckets: dawn (4-7), morning (7-12), afternoon (12-17),
             evening (17-21), night (21-4)
    """
    if not time_str:
        return "unknown"

    try:
        parts = time_str.strip().split(":")
        hour = int(parts[0])
    except (ValueError, IndexError):
        return "unknown"

    if 4 <= hour < 7:
        return "dawn"
    elif 7 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 21:
        return "evening"
    else:
        return "night"


def _weekday_to_int(weekday_str: str) -> int:
    """Convert weekday name to integer (0=Monday, 6=Sunday)."""
    mapping = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    return mapping.get(weekday_str.strip().lower(), -1)


# ---------------------------------------------------------------------------
# Anonymization
# ---------------------------------------------------------------------------

def _extract_exercise_tags(entry: dict[str, Any]) -> list[str]:
    """Extract exercise names from an entry's custom_fields.

    Looks for common stat-tracking patterns in custom_fields:
    - exercises: [{name: "Pike Pushups", ...}, ...]
    - Any key ending in _exercises or _movements
    - Free-form stat names that look like exercise names

    Returns a sorted, deduplicated list of exercise name slugs.
    """
    exercises: set[str] = set()
    cf = entry.get("custom_fields", {})
    if not isinstance(cf, dict):
        return []

    # Check exercises field
    for field_key in ["exercises", "movements", "workout_exercises"]:
        field_val = cf.get(field_key, [])
        if isinstance(field_val, list):
            for item in field_val:
                if isinstance(item, dict):
                    name = item.get("name", "")
                    if name:
                        slug = name.lower().replace(" ", "-").replace("'", "")
                        exercises.add(slug)
                elif isinstance(item, str):
                    slug = item.lower().replace(" ", "-").replace("'", "")
                    exercises.add(slug)

    return sorted(exercises)


def _anonymize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Strip personal information from an entry, keeping only public-safe fields.

    Kept:
        action_id (from public_action_id)
        duration_minutes
        weekday (int)
        time_bucket
        date (YYYY-MM-DD)
        location (from env if set)
        exercises (extracted exercise names, for agent matching)

    Removed:
        notes_body, custom_fields, exact time, activity name (raw),
        stem, category (raw — we use action_id instead)
    """
    location = os.environ.get("HAR_PUBLIC_LOCATION", "").strip()

    anon: dict[str, Any] = {
        "action_id": entry.get("public_action_id", ""),
        "duration": entry.get("duration", 0),
        "weekday": _weekday_to_int(entry.get("weekday", "")),
        "time_bucket": entry.get("time_bucket", "unknown"),
        "date": entry.get("date", ""),
    }

    if location:
        anon["location"] = location

    # Extract exercise tags for richer action registry
    exercises = _extract_exercise_tags(entry)
    if exercises:
        anon["exercises"] = exercises

    return anon


# ---------------------------------------------------------------------------
# Action Registry (public-actions repo)
# ---------------------------------------------------------------------------

def _action_file_path(slug: str, public_actions_dir: Path | None = None) -> Path:
    """Return the path to an action registry file given its slug.

    Args:
        slug: Action slug (e.g. "morning-rituals").
        public_actions_dir: Override directory (from --data-dir or env var).
                           Falls back to _public_actions_dir() if None.
    """
    base = public_actions_dir or _public_actions_dir()
    return base / "actions" / f"{slug}.md"


def _action_slug(action_id: str) -> str:
    """Convert an action_id to a filesystem-safe slug."""
    return (
        action_id.lower()
        .replace(" ", "-")
        .replace("/", "-")
        .replace("_", "-")
        .strip("-")
    )


def _load_action_registry(public_actions_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Read the current action registry from the public-actions repo.

    Args:
        public_actions_dir: Override directory (from --data-dir or env var).
                           Falls back to _public_actions_dir() if None.

    Returns a dict mapping slug -> {
        "slug": str,
        "title": str,
        "path": Path,
        "frontmatter": dict or None
    }
    """
    base = public_actions_dir or _public_actions_dir()
    actions_dir = base / "actions"
    registry: dict[str, dict[str, Any]] = {}

    if not actions_dir.exists():
        return registry

    for md_file in sorted(actions_dir.glob("*.md")):
        slug = md_file.stem
        frontmatter = _read_frontmatter(md_file)
        registry[slug] = {
            "slug": slug,
            "title": frontmatter.get("action", slug.replace("-", " ").title()),
            "path": md_file,
            "frontmatter": frontmatter,
        }

    return registry


def _read_frontmatter(path: Path) -> dict[str, Any]:
    """Read YAML-like frontmatter from a markdown file.

    Uses simple parsing (stdlib only — no yaml dependency).
    Supports: key: value, list items with - prefix.
    """
    frontmatter: dict[str, Any] = {}
    text = path.read_text(encoding="utf-8")

    if not text.startswith("---"):
        return frontmatter

    parts = text.split("---", 2)
    if len(parts) < 3:
        return frontmatter

    fm_text = parts[1].strip()
    current_key: str | None = None
    current_list: list[str] | None = None

    for line in fm_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # List item continuation
        if stripped.startswith("- ") and current_key is not None:
            item = stripped[2:].strip().strip('"')
            if current_list is not None:
                current_list.append(item)
            continue

        # Key: value
        if ": " in stripped:
            key, val_str = stripped.split(": ", 1)
            current_key = key.strip()
            val_str = val_str.strip()

            # Lists
            if val_str == "[]":
                frontmatter[current_key] = []
                current_list = []
                continue
            if val_str == "[":
                current_list = []
                frontmatter[current_key] = current_list
                continue

            # Remove quotes
            if val_str.startswith('"') and val_str.endswith('"'):
                val_str = val_str[1:-1]
            elif val_str.startswith("'") and val_str.endswith("'"):
                val_str = val_str[1:-1]

            # Parse booleans
            if val_str.lower() == "true":
                val_str = True
            elif val_str.lower() == "false":
                val_str = False

            # Parse numbers
            try:
                if "." in val_str:
                    val_str = float(val_str)
                else:
                    val_str = int(val_str)
            except (ValueError, TypeError):
                pass

            frontmatter[current_key] = val_str
            current_list = None

    return frontmatter


def _write_frontmatter(path: Path, frontmatter: dict[str, Any],
                       body: str = "") -> None:
    """Write frontmatter + body to a markdown file.

    Handles strings, ints, floats, bools, lists, and nested dicts.
    """
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
                    elif isinstance(val, str):
                        lines.append(f"{pad}{k}: \"{val}\"")
                    else:
                        lines.append(f"{pad}{k}: {val}")
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
        else:
            lines.append(f'{key}: "{value}"')
    lines.append("---")
    if body:
        lines.append("")
        lines.append(body.strip())
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_action_file_template(action_id: str) -> str:
    """Build the default body for a new action file."""
    display_name = os.environ.get("HAR_PUBLIC_DISPLAY_NAME", "").strip()
    first_reporter = f"first-reporter: {display_name}" if display_name else ""
    return f"""## About

This action is contributed through HAR's opt-in public record system.

{first_reporter}
"""


def _location_entries_summary(
    entries: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Compute per-location aggregates from a list of entry dicts."""
    by_location: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total_entries": 0, "total_duration": 0, "unique_dates": set()}
    )
    for entry in entries:
        loc = entry.get("location", "__unknown__")
        by_location[loc]["total_entries"] += 1
        by_location[loc]["total_duration"] += entry.get("duration", 0)
        if entry.get("date"):
            by_location[loc]["unique_dates"].add(entry["date"])
    return {
        loc: {
            "total_entries": data["total_entries"],
            "total_duration": data["total_duration"],
            "unique_dates": sorted(data["unique_dates"]),
        }
        for loc, data in by_location.items()
    }


def _compute_action_aggregates(
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recompute aggregate stats for an action from its entry list."""
    total_entries = len(entries)
    total_duration = sum(e.get("duration", 0) for e in entries)
    unique_dates = sorted({e["date"] for e in entries if e.get("date")})
    total_participants = len(unique_dates)
    location_entries = _location_entries_summary(entries)

    # Collect all exercise tags across entries for this action
    all_exercises: set[str] = set()
    for e in entries:
        ex_tags = e.get("exercises", [])
        if isinstance(ex_tags, list):
            all_exercises.update(ex_tags)

    result: dict[str, Any] = {
        "action": str(entries[0].get("action_id", "")),
        "total_participants": total_participants,
        "total_entries": total_entries,
        "total_duration_minutes": total_duration,
        "per_location": location_entries,
    }

    if all_exercises:
        result["exercises"] = sorted(all_exercises)

    return result


def _update_action_file(
    slug: str,
    title: str,
    new_entries: list[dict[str, Any]],
    existing_frontmatter: dict[str, Any] | None,
    registry_entries: list[dict[str, Any]],
    public_actions_dir: Path | None = None,
) -> Path:
    """Create or update an action file with new entry aggregates.

    Args:
        public_actions_dir: Override directory (from --data-dir or env var).
                           Passed through to _action_file_path.
    """
    action_path = _action_file_path(slug, public_actions_dir)
    action_dir = action_path.parent
    action_dir.mkdir(parents=True, exist_ok=True)

    # Combine existing registry entries with new ones for recalculation
    all_entries = registry_entries + new_entries

    # Compute aggregates
    agg = _compute_action_aggregates(all_entries)

    body = ""
    if existing_frontmatter:
        # Preserve existing body (everything after frontmatter)
        text = action_path.read_text(encoding="utf-8") if action_path.exists() else ""
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                body = parts[2].strip()
    else:
        body = _build_action_file_template(title)

    if not body:
        body = _build_action_file_template(title)

    # Build frontmatter
    fm: dict[str, Any] = agg.copy()
    fm["slug"] = slug

    # If first creation, add first-reporter credit
    if not existing_frontmatter:
        display_name = os.environ.get("HAR_PUBLIC_DISPLAY_NAME", "").strip()
        if display_name:
            fm["first_reporter"] = display_name

    _write_frontmatter(action_path, fm, body)
    return action_path


def _write_entry_line(
    public_actions_dir: Path,
    entry: dict[str, Any],
    contributor_id: str = "",
) -> Path:
    """Write one anonymized entry line to the per-contributor JSONL file.

    Contributors each have their own directory under entries/ so that
    fork-and-PR contributions never cause merge conflicts. The structure is:

        entries/{contributor_id}/YYYY/MM/YYYY-MM-DD.jsonl

    If no contributor_id is given, uses "local" as a fallback.

    Returns the path to the written file.
    """
    date_str = entry.get("date", "")
    if not date_str:
        return None

    try:
        dt = date.fromisoformat(date_str)
    except ValueError:
        return None

    cid = contributor_id.strip() if contributor_id else "local"
    # Sanitize contributor ID to be filesystem-safe
    cid = re.sub(r"[^a-zA-Z0-9_\-]", "-", cid).strip("-")
    if not cid:
        cid = "anonymous"

    entry_dir = public_actions_dir / "entries" / cid / str(dt.year) / f"{dt.month:02d}"
    entry_dir.mkdir(parents=True, exist_ok=True)

    entry_file = entry_dir / f"{date_str}.jsonl"

    # Build the line to write
    line_data: dict[str, Any] = {
        "action_id": entry.get("action_id", ""),
        "duration": entry.get("duration", 0),
        "weekday": entry.get("weekday", -1),
        "time_bucket": entry.get("time_bucket", "unknown"),
        "date": date_str,
    }
    # Add location if present
    if entry.get("location"):
        line_data["location"] = entry["location"]

    with open(entry_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(line_data, sort_keys=True) + "\n")

    return entry_file


# ---------------------------------------------------------------------------
# Global Aggregates
# ---------------------------------------------------------------------------

def _update_global_aggregates(
    public_actions_dir: Path,
    all_entries: list[dict[str, Any]],
) -> None:
    """Update aggregates/ directory with latest global stats."""
    aggregates_dir = public_actions_dir / "aggregates"
    aggregates_dir.mkdir(parents=True, exist_ok=True)

    # Global stats
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
        action_entries_list = by_action[action_id]
        action_total_dur = sum(e.get("duration", 0) for e in action_entries_list)
        action_unique_dates = {e["date"] for e in action_entries_list if e.get("date")}
        action_breakdown.append({
            "action_id": action_id,
            "total_entries": len(action_entries_list),
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

    # Update index.json (simpler summary for quick reads)
    index: dict[str, Any] = {
        "generated": datetime.now().isoformat(),
        "summary": f"{total_entries} entries · {total_duration} minutes · {len(unique_dates)} contributing days",
        "total_entries": total_entries,
        "total_duration_minutes": total_duration,
        "total_participants": total_participants,
    }
    (aggregates_dir / "index.json").write_text(
        json.dumps(index, indent=2) + "\n", encoding="utf-8"
    )

    # Update action-registry-index.json (lightweight lookup for agents)
    # Contains only slug, title, entry count, exercise tags, and locations.
    # Agents fetch this file once to match activities without reading every action file.
    registry_list: list[dict[str, Any]] = []
    actions_dir = public_actions_dir / "actions"
    if actions_dir.exists():
        for md_file in sorted(actions_dir.glob("*.md")):
            fm = _read_frontmatter(md_file)
            if not fm:
                continue
            slug = fm.get("slug", md_file.stem)
            entry = {
                "slug": slug,
                "title": fm.get("action", slug.replace("-", " ").title()),
                "total_entries": fm.get("total_entries", 0),
                "total_participants": fm.get("total_participants", 0),
            }
            # Extract locations and exercise tags from per_location
            per_loc = fm.get("per_location", {})
            if isinstance(per_loc, dict):
                locations = [loc for loc in per_loc if loc != "__unknown__"]
                if locations:
                    entry["locations"] = sorted(locations)
            # Add exercises if present
            exercises = fm.get("exercises", [])
            if isinstance(exercises, list) and exercises:
                entry["exercises"] = exercises
            registry_list.append(entry)

    (aggregates_dir / "action-registry-index.json").write_text(
        json.dumps(registry_list, indent=2) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------

def _git_commit_and_push(
    public_actions_dir: Path,
    entry_count: int,
    date_str: str,
) -> None:
    """Commit and push changes to the public-actions repo."""
    repo_dir = str(public_actions_dir)

    # Check if it's a git repo
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        print("  Warning: public-actions directory is not a git repository.")
        print("  Skipping commit/push. To use --push, make it a git repo first:")
        print(f"    cd {repo_dir} && git init && git add . && git commit -m 'init'")
        return

    # Add all changes
    subprocess.run(
        ["git", "add", "-A"],
        cwd=repo_dir,
        capture_output=True,
        timeout=15,
    )

    # Check if there's anything to commit
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if not result.stdout.strip():
        print("  No changes to commit.")
        return

    # Commit
    commit_msg = f"HAR contribution: {entry_count} entries ({date_str})"
    result = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        print(f"  Git commit failed: {result.stderr.strip()}")
        return

    print(f"  Committed: {commit_msg}")

    # Push
    result = subprocess.run(
        ["git", "push"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        print(f"  Git push failed: {result.stderr.strip()}")
        print("  You may need to set up remote tracking or push manually.")
    else:
        print("  Pushed to remote.")


# ---------------------------------------------------------------------------
# Main contribution flow
# ---------------------------------------------------------------------------

def _repo_check(public_actions_dir: Path) -> None:
    """Check that the public-actions repo exists and is usable."""
    if not public_actions_dir.exists():
        print(
            f"Error: Public actions repo not found at {public_actions_dir}.\n"
            "\n"
            "To set it up:\n"
            f"  git clone <your-public-actions-repo-url> {public_actions_dir}\n"
            "\n"
            "Or create a new repo:\n"
            f"  mkdir -p {public_actions_dir}\n"
            f"  cd {public_actions_dir}\n"
            "  git init\n"
            "  mkdir -p actions entries aggregates\n"
            "  git add .\n"
            "  git commit -m 'init'\n"
        )
        sys.exit(1)

    # Ensure basic directory structure exists
    (public_actions_dir / "actions").mkdir(parents=True, exist_ok=True)
    (public_actions_dir / "entries").mkdir(parents=True, exist_ok=True)
    (public_actions_dir / "aggregates").mkdir(parents=True, exist_ok=True)


def _filter_by_since(entries: list[dict[str, Any]], since: str | None) -> list[dict[str, Any]]:
    """Filter entries to only those on or after the given date."""
    if not since:
        return entries

    try:
        since_date = date.fromisoformat(since)
    except ValueError:
        sys.exit(f"Error: Invalid --since date format: {since!r}. Use YYYY-MM-DD.")

    filtered = []
    for entry in entries:
        entry_date_str = entry.get("date", "")
        if entry_date_str:
            try:
                entry_date = date.fromisoformat(entry_date_str)
                if entry_date >= since_date:
                    filtered.append(entry)
            except ValueError:
                filtered.append(entry)
        else:
            filtered.append(entry)

    return filtered


def _filter_matched_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter to only entries that have a public_action_id set."""
    return [e for e in entries if e.get("public_action_id")]


def _build_registry_index(public_actions_dir: Path) -> None:
    """Rebuild action-registry-index.json from action files.

    This produces a lightweight JSON file that agents can fetch once
    (instead of reading every action file) to match activities by name
    or exercise tags.
    """
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
        fm = _read_frontmatter(md_file)
        if not fm:
            continue
        slug = fm.get("slug", md_file.stem)
        entry: dict[str, Any] = {
            "slug": slug,
            "title": fm.get("action", slug.replace("-", " ").title()),
            "total_entries": fm.get("total_entries", 0),
            "total_participants": fm.get("total_participants", 0),
        }
        # Extract locations
        per_loc = fm.get("per_location", {})
        if isinstance(per_loc, dict):
            locations = sorted(
                loc for loc in per_loc if loc != "__unknown__"
            )
            if locations:
                entry["locations"] = locations
        # Extract exercise tags
        exercises = fm.get("exercises", [])
        if isinstance(exercises, list) and exercises:
            entry["exercises"] = exercises
        # Extract first reporter
        reporter = fm.get("first_reporter", "")
        if reporter:
            entry["first_reporter"] = reporter
        registry_list.append(entry)

    (aggregates_dir / "action-registry-index.json").write_text(
        json.dumps(registry_list, indent=2) + "\n", encoding="utf-8"
    )


def _remove_duplicate_entries(
    public_actions_dir: Path,
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove entries that already exist in the public-actions repo's JSONL files.

    Deduplicates by (date, action_id, duration, time_bucket) matching.
    """
    if not entries:
        return entries

    existing_signatures: set[tuple] = set()

    # Scan existing JSONL files
    entries_dir = public_actions_dir / "entries"
    if entries_dir.exists():
        for jsonl_file in sorted(entries_dir.rglob("*.jsonl")):
            try:
                with open(jsonl_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            sig = (
                                obj.get("date", ""),
                                obj.get("action_id", ""),
                                obj.get("duration", 0),
                                obj.get("time_bucket", ""),
                            )
                            existing_signatures.add(sig)
                        except json.JSONDecodeError:
                            continue
            except (OSError, IOError):
                continue

    # Filter out entries with matching signatures
    unique_entries: list[dict[str, Any]] = []
    for entry in entries:
        sig = (
            entry.get("date", ""),
            entry.get("action_id", ""),
            entry.get("duration", 0),
            entry.get("time_bucket", ""),
        )
        if sig not in existing_signatures:
            unique_entries.append(entry)

    return unique_entries


def main() -> int:
    """Entry point for the contribution pipeline."""
    parser = argparse.ArgumentParser(
        description="Anonymize and contribute HAR entries to the public action record."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be contributed without writing anything",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Commit and push changes to GitHub",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        metavar="DATE",
        help="Only process entries on or after DATE (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--no-match",
        action="store_true",
        help="Skip action matching — send raw entries for agent to match later",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        metavar="DIR",
        help=f"Path to public-actions repo clone (default: {DEFAULT_PUBLIC_ACTIONS_DIR})",
    )
    parser.add_argument(
        "--build-index",
        action="store_true",
        help="Only rebuild the action registry index (no contribution). "
             "Useful for agents to update the lightweight lookup file after "
             "pulling latest changes from the public repo.",
    )
    parser.add_argument(
        "--fetch-index",
        action="store_true",
        help="Print the action registry index as JSON to stdout (no writes). "
             "Agents use this for cheap activity matching. Requires --data-dir or default path.",
    )
    parser.add_argument(
        "--contributor",
        type=str,
        default="",
        metavar="ID",
        help="Contributor ID for per-contributor entry directories. "
             "Use your GitHub username. Defaults to 'local'.",
    )
    args = parser.parse_args()

    # ---- Determine paths ----
    public_actions_dir = (
        Path(args.data_dir).expanduser().resolve()
        if args.data_dir
        else _public_actions_dir()
    )

    # ---- Fetch-index mode (read-only, no contribution) ----
    if args.fetch_index:
        index_path = public_actions_dir / "aggregates" / "action-registry-index.json"
        if not index_path.exists():
            # Try to build it on-the-fly
            _build_registry_index(public_actions_dir)
        if index_path.exists():
            print(index_path.read_text(encoding="utf-8"))
        else:
            print("[]")
        return 0

    # ---- Build-index mode (just rebuild the index file) ----
    if args.build_index:
        _build_registry_index(public_actions_dir)
        print(f"Action registry index rebuilt at {public_actions_dir / 'aggregates' / 'action-registry-index.json'}")
        return 0

    # ---- Check opt-in ----
    if os.environ.get("HAR_PUBLIC_CONTRIBUTE", "").strip().lower() not in ("true", "1", "yes"):
        print("HAR contribution skipped: HAR_PUBLIC_CONTRIBUTE is not set to 'true'.")
        print("Set it in your environment to enable anonymous contribution:")
        print("  export HAR_PUBLIC_CONTRIBUTE=true")
        return 0

    # ---- Determine paths ----
    public_actions_dir = (
        Path(args.data_dir).expanduser().resolve()
        if args.data_dir
        else _public_actions_dir()
    )

    # ---- Determine paths ----
    public_actions_dir = (
        Path(args.data_dir).expanduser().resolve()
        if args.data_dir
        else _public_actions_dir()
    )

    if not args.dry_run:
        _repo_check(public_actions_dir)

    # ---- Step 1: Load entries ----
    print("Loading HAR entries...")
    all_entries = _load_derived_entries()
    print(f"  Found {len(all_entries)} total entries in HAR data.")

    # Filter by --since
    filtered_entries = _filter_by_since(all_entries, args.since)
    if args.since:
        print(f"  {len(filtered_entries)} entries on or after {args.since}.")

    # Filter to only entries with public_action_id (unless --no-match)
    if not args.no_match:
        matched_entries = _filter_matched_entries(filtered_entries)
        print(f"  {len(matched_entries)} entries with public_action_id set.")
        print(f"  {len(filtered_entries) - len(matched_entries)} entries skipped (no public_action_id).")
        filtered_entries = matched_entries
    else:
        print("  Skipping action matching (--no-match).")

    if not filtered_entries:
        print("No entries to contribute.")
        return 0

    # ---- Step 2: Anonymize ----
    print("Anonymizing entries...")
    anon_entries = [_anonymize_entry(e) for e in filtered_entries]
    print(f"  Anonymized {len(anon_entries)} entries.")

    # ---- Step 3: Deduplicate against existing ----
    if not args.dry_run:
        anon_entries = _remove_duplicate_entries(public_actions_dir, anon_entries)
        print(f"  {len(anon_entries)} entries after deduplication against existing data.")

    if not anon_entries:
        print("No new entries to contribute (all already exist in the public repo).")
        return 0

    # ---- Step 4: Match/Create actions ----
    print("Loading action registry...")
    registry = _load_action_registry(public_actions_dir) if not args.no_match else {}
    registry_slugs_for_entries = _load_action_registry(public_actions_dir) if args.no_match else registry

    # Collect entries per action slug
    entries_by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in anon_entries:
        action_id = entry.get("action_id", "__unknown__")
        slug = _action_slug(action_id)
        entries_by_slug[slug].append(entry)

    matched_action_count = len(
        {s for s in entries_by_slug if s in registry}
    )
    new_action_count = len(
        {s for s in entries_by_slug if s not in registry}
    )

    if args.dry_run:
        total_duration = sum(e.get("duration", 0) for e in anon_entries)
        unique_dates = sorted({e["date"] for e in anon_entries if e.get("date")})
        print("\n=== DRY RUN ===")
        print(f"Entries to contribute: {len(anon_entries)}")
        print(f"  Total duration: {total_duration} minutes")
        print(f"  Date range: {unique_dates[0] if unique_dates else '?'} — {unique_dates[-1] if unique_dates else '?'}")
        print(f"Actions matched: {matched_action_count}")
        print(f"New actions to create: {new_action_count}")
        if args.push:
            print("Would commit and push to GitHub.")
        print("\nEntry breakdown by action:")
        for slug, slug_entries in sorted(entries_by_slug.items()):
            print(f"  {slug}: {len(slug_entries)} entries")
        print("\n(Dry run — no files were written)")
        return 0

    # ---- Step 5: Write entry lines ----
    print("\nWriting entry lines...")
    written_files: set[Path] = set()
    for entry in anon_entries:
        result_path = _write_entry_line(public_actions_dir, entry, args.contributor)
        if result_path:
            written_files.add(result_path)

    print(f"  {len(anon_entries)} entries written across {len(written_files)} date files.")

    # ---- Step 6: Update action files ----
    print("Updating action registry...")
    updated_actions: list[str] = []
    for slug, slug_entries in sorted(entries_by_slug.items()):
        action_id = slug_entries[0].get("action_id", slug.replace("-", " ").title())
        existing_fm = None if slug not in registry else registry[slug].get("frontmatter")

        _update_action_file(slug, action_id, slug_entries, existing_fm, [], public_actions_dir)
        updated_actions.append(slug)

    print(f"  Updated {len(updated_actions)} action files.")

    # ---- Step 7: Run reindex to rebuild actions/aggregates from all entries ----
    print("Rebuilding action registry and aggregates...")
    try:
        import subprocess
        reindex_path = HAR_ROOT / "scripts" / "har-reindex.py"
        result = subprocess.run(
            [sys.executable, str(reindex_path), "--data-dir", str(public_actions_dir)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                print(f"  {line}")
        else:
            print(f"  Reindex failed: {result.stderr[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"  Warning: could not run reindex: {e}", file=sys.stderr)

    # ---- Step 8: Git operations ----
    if args.push:
        print("Committing and pushing to GitHub...")
        date_str = anon_entries[0].get("date", "unknown") if anon_entries else "unknown"
        _git_commit_and_push(public_actions_dir, len(anon_entries), date_str)
    else:
        print("\nChanges are local. Use --push to commit and push to GitHub.")
        print(f"Review changes in: {public_actions_dir}")
        print("Run the reindex script to rebuild aggregates:")
        print(f"  python3 scripts/har-reindex.py")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
