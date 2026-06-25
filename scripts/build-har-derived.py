#!/usr/bin/env python3
"""Build derived HAR review artifacts from action entries.

Three primary jobs:
1. Time & Stats windows with aggregate stats per activity across the range
2. Activity journal pages (one per activity family, chronological notes)
3. Chart SVGs for time and notes

Maintains the clean, stripped-down approach from the Apr 27 cleanup.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import html
import json
import math
from pathlib import Path
import re
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CALENDAR_ROOT = REPO_ROOT / "calendar"
DERIVED_ROOT = REPO_ROOT / "_derived"
GRAPH_ROOT = DERIVED_ROOT / "har-graphs"
JOURNAL_ROOT = DERIVED_ROOT / "har-activity-journals"
CATEGORY_CONFIG_PATH = REPO_ROOT / "maps" / "action-categories.yaml"
PLANS_ROOT = REPO_ROOT / "plans" / "daily"

# Fields to skip in aggregate stat display (too granular for card surface)
SKIP_AGGREGATE_FIELDS = {"exercises"}

# Fields that are list-type and should be deduplicated
LIST_TYPE_FIELDS = {"played_games"}

# The live weekly shell needs a tighter scan rhythm than the deeper proof
# surfaces. Keep the page-top strip to the top categories, but let each
# visible category carry three explicit activity rows before overflow so the
# signature surface can still read category-first at the activity level.
TOP_SHELL_VISIBLE_CATEGORY_COUNT = 2
TOP_SHELL_VISIBLE_ACTIVITY_COUNT = 2
TOP_SHELL_OVERFLOW_PREVIEW_COUNT = 3
TOP_PROOF_VISIBLE_ACTIVITY_COUNT = 3

DISPLAY_BADGE_STATE_LABELS = {
    "capture mix",
    "movement data",
    "notes-derived movements",
    "stats reported",
}


def read_entry(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    end_index = lines[1:].index("---") + 1
    fm = yaml.safe_load("\n".join(lines[1:end_index])) or {}
    body = "\n".join(lines[end_index + 1:]).strip()
    frontmatter_notes = str(fm.get("notes", "")).strip()
    if body and frontmatter_notes and frontmatter_notes not in body:
        notes = f"{body}\n\n{frontmatter_notes}"
    else:
        notes = body or frontmatter_notes
    return {
        "path": path,
        "date": str(fm.get("date", "")),
        "time": str(fm.get("time", "")),
        "activity": str(fm.get("activity", "")),
        "category": str(fm.get("category", "")),
        "duration": fm.get("duration") if isinstance(fm.get("duration"), int) else None,
        "capture_mode": str(fm.get("capture_mode", "unknown-legacy")),
        "custom_fields": fm.get("custom_fields") if isinstance(fm.get("custom_fields"), dict) else {},
        "has_notes": bool(notes),
        "notes": notes,
        "stem": path.stem,
        "public_action_id": str(fm.get("public_action_id", "")).strip(),
    }


def action_entries() -> list[dict[str, Any]]:
    entries = []
    for path in sorted(CALENDAR_ROOT.rglob("*.md")):
        if path.is_file():
            entries.append(read_entry(path))
    return entries


def load_category_config() -> dict[str, Any]:
    if not CATEGORY_CONFIG_PATH.exists():
        return {}
    data = yaml.safe_load(CATEGORY_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    categories = data.get("categories")
    return categories if isinstance(categories, dict) else {}


def activity_family(name: str) -> str:
    m = re.match(r"^(.*?)(?:\s+Session\s+\d+)$", name, flags=re.IGNORECASE)
    return (m.group(1) if m else name).strip()


def ascii_bar(value: int, max_val: int, width: int = 20) -> str:
    if value <= 0 or max_val <= 0:
        return ""
    filled = max(1, round((value / max_val) * width))
    return "#" * min(width, filled)


def top_lines(items: list[tuple[str, int]], suffix: str = "") -> list[str]:
    if not items:
        return ["- none yet"]
    max_val = items[0][1]
    result = []
    for label, val in items:
        bar = ascii_bar(val, max_val)
        result.append(f"- {label}: {val}{suffix} {'| ' + bar if bar else ''}")
    return result


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        return (107, 114, 128)
    return tuple(int(value[i:i+2], 16) for i in (0, 2, 4))


def mix_hex(base: str, target: tuple[int, int, int], ratio: float) -> str:
    br, bg, bb = hex_to_rgb(base)
    tr, tg, tb = target
    r = max(0, min(255, round((1 - ratio) * br + ratio * tr)))
    g = max(0, min(255, round((1 - ratio) * bg + ratio * tg)))
    b = max(0, min(255, round((1 - ratio) * bb + ratio * tb)))
    return f"#{r:02x}{g:02x}{b:02x}"


def window_entries(entries: list[dict], days: int) -> list[dict]:
    dated = [e for e in entries if e["date"]]
    if not dated:
        return []
    latest = max(date.fromisoformat(e["date"]) for e in dated)
    earliest = latest - timedelta(days=days - 1)
    return sorted(
        [e for e in dated if earliest <= date.fromisoformat(e["date"]) <= latest],
        key=lambda e: (e["date"], e["time"], e["stem"]),
        reverse=True,
    )


def aggregate_stats(entries: list[dict]) -> dict[str, Any]:
    """Aggregate structured_numeric fields across a list of entries.
    
    Returns a dict mapping field name -> dict with:
      - 'value': aggregated value (sum for numerics, unique list for list fields)
      - 'type': 'numeric' or 'list'
    """
    agg: dict[str, Any] = {}
    list_values: dict[str, set] = defaultdict(set)
    
    for e in entries:
        cf = e.get("custom_fields", {})
        if not cf:
            continue
        for k, v in cf.items():
            if k in SKIP_AGGREGATE_FIELDS:
                continue
            if k in LIST_TYPE_FIELDS:
                if isinstance(v, list):
                    for item in v:
                        list_values[k].add(str(item))
                elif isinstance(v, str):
                    # Could be comma-separated
                    for item in v.split(","):
                        list_values[k].add(item.strip())
            elif isinstance(v, (int, float)):
                agg[k] = agg.get(k, {"value": 0.0, "type": "numeric"})
                agg[k]["value"] += float(v)
            elif isinstance(v, bool):
                agg[k] = agg.get(k, {"value": 0, "type": "bool_count"})
                agg[k]["value"] += 1 if v else 0
    
    for k, vals in list_values.items():
        agg[k] = {"value": sorted(vals), "type": "list"}
    
    return agg


def format_aggregated_stats(agg: dict) -> str:
    """Format aggregated stats into a clean inline string."""
    if not agg:
        return ""
    parts = []
    for k, info in sorted(agg.items()):
        label = k.replace("_", " ")
        v = info["value"]
        t = info.get("type", "numeric")
        if t == "numeric":
            # Round to int if whole number
            if v == int(v):
                val_str = str(int(v))
            else:
                val_str = f"{v:.1f}"
            parts.append(f"{label}: {val_str}")
        elif t == "list":
            val_str = ", ".join(str(x) for x in v)
            parts.append(f"{label}: {val_str}")
        elif t == "bool_count":
            parts.append(f"{label}: {int(v)}")
    return " · ".join(parts)


def format_custom_fields(fields: dict) -> str:
    if not fields:
        return ""
    parts = []
    for k, v in fields.items():
        label = k.replace("_", " ")
        if k == "exercises":
            continue
        if isinstance(v, dict):
            sub = ', '.join(f'{sk}: {sv}' for sk, sv in v.items() if sk != 'exercises')
            if sub:
                parts.append(f"{label}: {sub}")
        elif isinstance(v, list):
            parts.append(f"{label}: {'; '.join(str(x) for x in v)}")
        else:
            parts.append(f"{label}: {v}")
    return " | ".join(parts)


def stats_summary(entry: dict) -> str:
    notes_lower = entry["notes"].lower()
    if "no stats to report" in notes_lower:
        return ""
    return format_custom_fields(entry["custom_fields"])


def _render_callout(callout_type: str, title: str, body_lines: list[str]) -> list[str]:
    lines = [f"> [!{callout_type}] {title}"]
    for line in body_lines:
        if line:
            lines.append(f"> {line}")
        else:
            lines.append(">")
    return lines


def _format_stat_label(label: str) -> str:
    return label.replace("_", " ").title()


def _count_phrase(count: int, singular: str, plural: str | None = None) -> str:
    if count == 1:
        return f"1 {singular}"
    return f"{count} {plural or singular + 's'}"


def _visible_of_total_activities_text(total_count: int, visible_count: int) -> str:
    visible = max(0, min(total_count, visible_count))
    row_label = "row" if visible == 1 else "rows"
    if total_count <= 0:
        return "0 rows · 0 grouped"
    if visible >= total_count:
        return f"{visible} {row_label}"
    grouped = total_count - visible
    return f"{visible} {row_label} · {grouped} grouped"


def _top_surface_visibility_meta_text(total_count: int) -> str:
    return _visible_of_total_activities_text(
        max(total_count, 0),
        TOP_SHELL_VISIBLE_ACTIVITY_COUNT,
    )


def _compact_category_meta_items(
    activity_count: int,
    log_count: int,
    *,
    visible_count: int,
    include_activity_count: bool = False,
) -> list[str]:
    items = [_count_phrase(log_count, "log")]
    if include_activity_count:
        items.append(_count_phrase(activity_count, "activity", "activities"))
    items.append(_visible_of_total_activities_text(activity_count, visible_count))
    return items


def _category_visibility_meta_text(
    activity_count: int,
    log_count: int,
    *,
    visible_count: int,
    include_activity_count: bool = False,
) -> str:
    return " · ".join(
        _compact_category_meta_items(
            activity_count,
            log_count,
            visible_count=visible_count,
            include_activity_count=include_activity_count,
        )
    )


def _more_reads_suffix(count: int) -> str:
    return f"+{count} more read" if count == 1 else f"+{count} more reads"


def _human_duration(total_minutes: int) -> str:
    minutes = max(0, int(total_minutes))
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours}h {remainder}m"
    if hours:
        return f"{hours}h 0m"
    return f"{remainder}m"


def _duration_summary_text(total_minutes: int, share: int | None = None) -> str:
    text = f"{_human_duration(total_minutes)} ({total_minutes} min"
    if isinstance(share, int):
        text += f", {share}%"
    return text + ")"


def _duration_pair_text(total_minutes: int) -> str:
    if total_minutes < 60:
        return f"{total_minutes} min"
    return f"{_human_duration(total_minutes)} ({total_minutes} min)"


def _human_activity_duration(total_minutes: int) -> str:
    return _human_duration(total_minutes)


def _top_activity_label(count: int) -> str:
    return f"top {count} {'activity' if count == 1 else 'activities'}"


def _more_group_title(
    count: int,
    *,
    singular: str = "activity",
    plural: str | None = None,
) -> str:
    noun = singular if count == 1 else (plural or singular + "s")
    return f"+{count} {noun}"


def _compact_name_preview(
    names: list[str],
    *,
    max_names: int = 2,
    max_chars: int = 42,
    overflow_singular: str = "name",
    overflow_plural: str | None = None,
) -> str:
    cleaned: list[str] = []
    seen: set[str] = set()
    for name in names:
        normalized = re.sub(r"\s+", " ", str(name)).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)

    if not cleaned:
        return ""

    def _build_preview(candidate_names: list[str], visible_count: int) -> str:
        visible = candidate_names[:visible_count]
        if not visible:
            return ""
        preview = " + ".join(visible)
        if len(candidate_names) > visible_count:
            remainder = len(candidate_names) - visible_count
            preview += f" +{remainder} more {overflow_plural or overflow_singular + 's'}"
        return preview

    variant_sets: list[list[str]] = [cleaned]
    trimmed = [_trim_parenthetical_suffix(name) or name for name in cleaned]
    if trimmed != cleaned:
        variant_sets.append(trimmed)

    for variant_names in variant_sets:
        max_visible = min(max_names, len(variant_names))
        for visible_count in range(max_visible, 0, -1):
            preview = _build_preview(variant_names, visible_count)
            if preview and len(preview) <= max_chars:
                return preview
    return ""


def _leader_names(acts: list[tuple[str, int, list[dict]]]) -> tuple[list[str], int]:
    if not acts:
        return [], 0
    lead_minutes = acts[0][1]
    leaders = [act_name for act_name, act_min, _ in acts if act_min == lead_minutes]
    return leaders, lead_minutes


def _format_lead_summary(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
    prefix: str = "lead",
    max_names: int = 2,
) -> str:
    leaders, lead_minutes = _leader_names(acts)
    if not leaders or lead_minutes <= 0 or total <= 0:
        return ""

    visible_names = leaders[:max_names]
    names_text = " + ".join(visible_names)
    if len(leaders) > max_names:
        names_text += f" +{len(leaders) - max_names} more"

    share = round((lead_minutes / total) * 100)
    human_duration = _human_duration(lead_minutes)
    if len(leaders) == 1:
        return f"{prefix}: {names_text} {human_duration} ({lead_minutes} min, {share}%)"
    return f"{prefix} tie: {names_text} {human_duration} ({lead_minutes} min each, {share}%)"


def _top_mix_summary(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
    max_items: int = 2,
    prefix: str = "top mix",
) -> str:
    if total <= 0 or len(acts) <= 1:
        return ""

    parts = []
    for act_name, act_min, _ in acts[:max_items]:
        share = round((act_min / total) * 100)
        parts.append(f"{act_name} {share}%")

    if not parts:
        return ""
    return f"{prefix}: " + " · ".join(parts)


def _format_date_span(start: date, end: date) -> str:
    return f"**{start.isoformat()}** to **{end.isoformat()}**"


def _plain_date_span(start: date, end: date) -> str:
    if start == end:
        return start.isoformat()
    return f"{start.isoformat()} to {end.isoformat()}"


def _format_logged_dates_line(date_values: list[str]) -> str:
    if not date_values:
        return "- logged dates in range: none yet"
    start = min(date_values)
    end = max(date_values)
    if start == end:
        return f"- logged date in range: **{start}** only"
    return f"- logged dates in range: **{start}** to **{end}**"


def _range_coverage_line(*, active_days: int, selected_days: int) -> str:
    if selected_days <= 0:
        return "- range coverage: no selected days"
    coverage = round((active_days / selected_days) * 100) if active_days else 0
    return (
        f"- range coverage: **{active_days} of {selected_days} days** with logs"
        f" ({coverage}%)"
    )


def _sparse_week_handoff_line(*, days: int, active_days: int) -> str:
    if days != 7 or active_days > 1:
        return ""
    return (
        "- sparse week handoff: **1 active day** logged, so keep this weekly read for honesty "
        "and open **30-Day Pattern Extension** next if you need a denser pattern read"
    )


def _window_bounds(entries: list[dict], days: int) -> tuple[date, date] | None:
    dated = [e for e in entries if e["date"]]
    if not dated:
        return None
    latest = max(date.fromisoformat(e["date"]) for e in dated)
    if days >= 36500:
        earliest = min(date.fromisoformat(e["date"]) for e in dated)
    else:
        earliest = latest - timedelta(days=days - 1)
    return earliest, latest


def _strip_markdown_emphasis(text: str) -> str:
    return text.replace("**", "")


def _truncate_text(text: str, limit: int = 110) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    first_sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0].strip()
    if first_sentence and len(first_sentence) >= 28 and len(first_sentence) < len(cleaned):
        cleaned = first_sentence
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip(" ,;:-") + "…"


def _normalize_note_clue(line: str) -> str:
    def strip_trailing_clock_phrase(text: str) -> str:
        text = re.sub(
            r"(?i)\s+from\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:to|-)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?\.?$",
            "",
            text,
        ).strip()
        text = re.sub(
            r"(?i)\s+at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\.?$",
            "",
            text,
        ).strip()
        return re.sub(r"\s+\.\s*$", ".", text)

    original = line.strip()
    normalized = re.sub(r"(?i)^today\s+from\s+[^.]+?\bi\s+", "", original).strip()
    normalized = re.sub(r"(?i)^from\s+[^.]+?\bi\s+", "", normalized).strip()
    normalized = re.sub(r"(?i)^today\b[\s,:-]*", "", normalized).strip()
    normalized = re.sub(
        r"(?i)^from\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:to|-)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s+i\s+",
        "",
        normalized,
    ).strip()
    normalized = re.sub(
        r"(?i)^at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s+i\s+",
        "",
        normalized,
    ).strip()
    stripped_time_preamble = normalized != original
    normalized = strip_trailing_clock_phrase(normalized)
    normalized = re.sub(
        r"(?i)\s+from\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:to|-)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?(?=\s+(?:after|before|while|then|and|because|when)\b)",
        "",
        normalized,
    ).strip()
    normalized = re.sub(r"\s{2,}", " ", normalized).strip()
    normalized = re.sub(r"(?i)\.\s+i\s+(?:had|ate|drank|was|did|went)\b.*$", "", normalized).strip()
    normalized = strip_trailing_clock_phrase(normalized)
    stripped_time_context = normalized != original
    if normalized and normalized[:1].islower():
        normalized = normalized[:1].upper() + normalized[1:]
    if normalized and (len(normalized) < 14 or len(normalized.split()) < 2):
        if stripped_time_preamble or stripped_time_context:
            return normalized
        return original
    return normalized or original


def _note_clue_key(line: str) -> str:
    key = line.lower()
    key = re.sub(r"\b(?:more|again)\b", "", key)
    key = re.sub(r"\b\d{1,2}:\d{2}\b", "", key)
    key = re.sub(r"\b\d+\b", "", key)
    key = re.sub(r"[^a-z\s]", " ", key)
    key = re.sub(r"\s+", " ", key).strip()
    return key


def _join_note_clues(clues: list[str]) -> str:
    return " / ".join(clues)


def _summary_note_clue(clues: list[str], *, limit: int = 54) -> str:
    if not clues:
        return ""
    text = _join_note_clues(clues[:1])
    return _truncate_text(text, limit=limit)


def _visible_note_clue_read(
    clues: list[str],
    *,
    max_chars: int = 108,
) -> str:
    if not clues:
        return ""
    clue_limit = max(42, min(52, max_chars // 2 + 4))
    clue_parts = [_truncate_text(clue, limit=clue_limit) for clue in clues if clue]
    summary, hidden_count = _bounded_preview_join(
        clue_parts,
        max_chars=max_chars,
    )
    if hidden_count > 0:
        overflow = _overflow_count_label(hidden_count, "note clue")
        summary = f"{summary} / {overflow}" if summary else overflow
    return summary or _truncate_text(_join_note_clues(clues), limit=max_chars)


def _surface_note_clue_read(
    clues: list[str],
    *,
    max_chars: int = 84,
) -> str:
    if not clues:
        return ""

    primary = _truncate_text(clues[0], limit=max(36, min(62, max_chars - 18)))
    remaining = max(0, len(clues) - 1)
    if not remaining:
        return primary

    overflow = _overflow_count_label(remaining, "note read")
    joined = f"{primary} / {overflow}" if primary else overflow
    if len(joined) <= max_chars:
        return joined
    primary = _truncate_text(primary, limit=max(24, max_chars - len(overflow) - 3))
    return f"{primary} / {overflow}" if primary else overflow


def _compact_preview_detail(text: str, *, limit: int = 92) -> str:
    if not text:
        return ""
    return _truncate_text(text, limit=limit)


def _compact_segmented_preview(
    text: str,
    *,
    limit: int = 92,
    overflow_singular: str = "detail",
    overflow_plural: str | None = None,
    min_keep: int = 1,
) -> str:
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if not cleaned:
        return ""
    if len(cleaned) <= limit or " · " not in cleaned:
        return _compact_preview_detail(cleaned, limit=limit)

    parts = [part.strip() for part in cleaned.split(" · ") if part.strip()]
    if len(parts) <= 1:
        return _compact_preview_detail(cleaned, limit=limit)

    visible = parts[:]
    hidden = 0
    min_keep = max(1, min(min_keep, len(parts)))
    while len(visible) > min_keep and len(" · ".join(visible)) > limit:
        visible.pop()
        hidden += 1

    if hidden > 0:
        overflow = _overflow_count_label(hidden, overflow_singular, overflow_plural)
        candidate = " · ".join(visible + [overflow])
        while len(visible) > min_keep and len(candidate) > limit:
            visible.pop()
            hidden += 1
            overflow = _overflow_count_label(hidden, overflow_singular, overflow_plural)
            candidate = " · ".join(visible + [overflow])
        if len(candidate) <= limit:
            return candidate

    candidate = " · ".join(visible)
    if len(candidate) <= limit:
        return candidate
    return _compact_preview_detail(candidate, limit=limit)


def _compact_preview_parts(
    parts: list[str],
    *,
    limit: int,
    overflow_singular: str | None = None,
    overflow_plural: str | None = None,
) -> str:
    cleaned = [re.sub(r"\s+", " ", str(part)).strip() for part in parts if str(part).strip()]
    if not cleaned:
        return ""

    summary, hidden_count = _bounded_preview_join(
        cleaned,
        max_chars=limit,
    )
    if hidden_count > 0 and overflow_singular:
        overflow = _overflow_count_label(
            hidden_count,
            overflow_singular,
            overflow_plural,
        )
        summary = f"{summary} · {overflow}" if summary else overflow
    return summary


def _compact_session_metric_text(text: str) -> str:
    cleaned = re.sub(r"\b(\d+)\s+structured\s+sessions?\b", r"\1 structured", str(text), flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(\d+)\s+notes-only\s+sessions?\b", r"\1 notes-only", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _strip_session_mix_segment(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if not cleaned:
        return ""
    cleaned = re.sub(
        r"\s*·\s*\d+\s+structured(?:\s*\+\s*\d+\s+notes-only)?\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s*·\s*\d+\s+notes-only\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s{2,}", " ", cleaned).strip(" ·")


def _strip_workout_load_segment(text: str) -> str:
    cleaned = re.sub(
        r"\s*·\s*loads?\s+[^·]+$",
        "",
        str(text),
        flags=re.IGNORECASE,
    ).strip()
    return re.sub(r"\s{2,}", " ", cleaned)


def _strip_low_priority_surface_suffixes(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if not cleaned:
        return ""

    patterns = [
        r"\s*·\s*\+\d+\s+more\s+reads?\s+below$",
        r"\s*·\s*\+\d+\s+more\s+note\s+reads?$",
        r"\s*·\s*\+\d+\s+more\s+details?$",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def _compact_workout_surface_text(text: str, *, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if not cleaned:
        return ""
    variants = [cleaned]
    compact_totals = re.sub(r"\btotal sets\b", "sets", cleaned)
    compact_totals = re.sub(r"\btotal reps\b", "reps", compact_totals)
    if compact_totals != cleaned:
        variants.append(compact_totals)
    compact_more = re.sub(r"(\+\d+)\s+more\s+movements\b", r"\1 more", compact_totals)
    if compact_more not in variants:
        variants.append(compact_more)
    compact_sessions = _compact_session_metric_text(compact_more)
    if compact_sessions not in variants:
        variants.append(compact_sessions)
    compact_sessions_without_loads = _strip_workout_load_segment(compact_sessions)
    if compact_sessions_without_loads not in variants:
        variants.append(compact_sessions_without_loads)
    segments = [segment.strip() for segment in compact_more.split(" · ") if segment.strip()]
    if segments and segments[0] in {"movement data", "notes-derived movements", "capture mix"}:
        detail_parts = segments[1:]
        if detail_parts and re.search(r"\bmovements?\b", detail_parts[0], flags=re.IGNORECASE):
            name_part = ""
            tail_start = 1
            if len(detail_parts) > 1 and not re.search(
                r"\b(?:structured|notes-only)\s+sessions?\b|\bsets\b|\breps\b|\bloads?\b|\bNo stats to report\b",
                detail_parts[1],
                flags=re.IGNORECASE,
            ):
                name_part = detail_parts[1]
                tail_start = 2
            tail_parts = detail_parts[tail_start:]
            tail_text = " · ".join(tail_parts)
            shortened_name = name_part
            if name_part:
                shortened_name = re.sub(
                    r"^(.+?)\s+\+\s+.+?(\s+\+\d+\s+more(?:\s+movements?)?)$",
                    r"\1\2",
                    name_part,
                    flags=re.IGNORECASE,
                )
                if shortened_name == name_part:
                    shortened_name = re.sub(
                        r"^(.+?)\s+\+\s+.+$",
                        r"\1",
                        name_part,
                        flags=re.IGNORECASE,
                    )
            movement_count = detail_parts[0]
            if shortened_name and tail_text:
                variants.append(
                    f"{segments[0]} · {movement_count} · {shortened_name} · {tail_text}"
                )
            if tail_text:
                variants.append(f"{segments[0]} · {movement_count} · {tail_text}")
            if shortened_name:
                variants.append(f"{segments[0]} · {movement_count} · {shortened_name}")
            compact_tail_text = _compact_session_metric_text(tail_text)
            if shortened_name and compact_tail_text:
                variants.append(
                    f"{segments[0]} · {movement_count} · {shortened_name} · {compact_tail_text}"
                )
            if compact_tail_text:
                variants.append(f"{segments[0]} · {movement_count} · {compact_tail_text}")
                compact_tail_without_loads = _strip_workout_load_segment(compact_tail_text)
                if compact_tail_without_loads != compact_tail_text:
                    if shortened_name:
                        variants.append(
                            f"{segments[0]} · {movement_count} · {shortened_name} · {compact_tail_without_loads}"
                        )
                    variants.append(
                        f"{segments[0]} · {movement_count} · {compact_tail_without_loads}"
                    )
    stripped_variants = [
        _strip_low_priority_surface_suffixes(variant)
        for variant in variants
    ]
    preferred_variants = [
        variant
        for variant in stripped_variants + variants
        if variant
    ]
    ordered_variants = _unique_in_order(preferred_variants)
    for allow_ellipsis in (False, True):
        fitting = [
            variant
            for variant in ordered_variants
            if len(variant) <= limit and (allow_ellipsis or "…" not in variant)
        ]
        if fitting:
            return max(fitting, key=len)
    fallback = stripped_variants[-1] if stripped_variants else ordered_variants[-1]
    return _compact_segmented_preview(fallback, limit=limit, min_keep=3)


def _meaning_preview_limit(state_label: str) -> int:
    if state_label.startswith("notes-only"):
        return 64
    if state_label == "capture mix":
        return 78
    return 88


def _bounded_preview_join(
    parts: list[str],
    *,
    max_chars: int,
    overflow_text: str | None = None,
) -> tuple[str, int]:
    cleaned = [re.sub(r"\s+", " ", part).strip() for part in parts if str(part).strip()]
    if not cleaned:
        return "", 0

    visible: list[str] = []
    hidden = 0
    for index, part in enumerate(cleaned):
        candidate = " / ".join(visible + [part])
        remaining = len(cleaned) - (index + 1)
        suffix = ""
        if remaining > 0 and overflow_text:
            suffix = f" / {overflow_text.format(count=remaining)}"
        if len(candidate + suffix) <= max_chars:
            visible.append(part)
            continue

        if not visible:
            visible.append(_compact_preview_detail(part, limit=max_chars))
            hidden = remaining
        else:
            hidden = len(cleaned) - index
        break

    if not visible:
        return "", hidden

    return " / ".join(visible), hidden


def _unique_in_order(parts: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        cleaned = re.sub(r"\s+", " ", str(part)).strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    return unique


def _chunk_detail_lines(
    *,
    label: str,
    items: list[str],
    chunk_size: int = 2,
    continuation_label: str | None = None,
    max_items: int | None = None,
    overflow_label: str | None = None,
    overflow_preview_titles: list[str] | None = None,
) -> list[str]:
    if not items:
        return []
    lines: list[str] = []
    visible_items = items[:max_items] if isinstance(max_items, int) and max_items > 0 else items
    if visible_items is not items and len(items) - len(visible_items) == 1:
        visible_items = items
    next_label = continuation_label or f"more {label}"
    for index in range(0, len(visible_items), chunk_size):
        chunk = visible_items[index:index + chunk_size]
        current_label = label if index == 0 else next_label
        lines.append(f"  - {current_label}: {' / '.join(chunk)}")
    if visible_items is not items:
        hidden_count = len(items) - len(visible_items)
        if hidden_count > 0:
            overflow_text = overflow_label or f"+{hidden_count} more {label}"
            if overflow_preview_titles:
                preview_text = _compact_name_preview(
                    overflow_preview_titles[:hidden_count],
                    max_names=2,
                    max_chars=72,
                    overflow_singular="movement",
                )
                if preview_text:
                    overflow_text = preview_text
            lines.append(f"  - {next_label}: {overflow_text}")
    return lines


def _session_coverage_text(reported_sessions: int | None, total_sessions: int | None) -> str:
    if (
        not isinstance(reported_sessions, int)
        or not isinstance(total_sessions, int)
        or reported_sessions <= 0
        or total_sessions <= 0
        or reported_sessions >= total_sessions
    ):
        return ""
    return f" in {reported_sessions}/{total_sessions} sessions"


def _overflow_count_label(count: int, singular: str, plural: str | None = None) -> str:
    if count <= 0:
        return ""
    return f"+{count} more {plural or singular + 's'}" if count != 1 else f"+1 more {singular}"


def _session_phrase(count: int, adjective: str | None = None) -> str:
    singular = "session" if not adjective else f"{adjective} session"
    plural = "sessions" if not adjective else f"{adjective} sessions"
    return _count_phrase(count, singular, plural)


def _append_detail_suffix(detail: str, suffix: str) -> str:
    if not detail:
        return suffix
    if not suffix:
        return detail
    return f"{detail} · {suffix}"


def _capture_mix_detail_suffix(structured_sessions: int, notes_only_count: int) -> str:
    parts: list[str] = []
    if structured_sessions > 0:
        parts.append(_session_phrase(structured_sessions, "structured"))
    if notes_only_count > 0:
        parts.append(_session_phrase(notes_only_count, "notes-only"))
    return " + ".join(parts)


def _stat_read_line(
    stat_items: list[dict[str, str]],
    *,
    prefer_detail: bool = False,
) -> str:
    if not stat_items:
        return ""
    parts: list[str] = []
    for item in stat_items:
        if prefer_detail:
            detail = str(item.get("detail", "")).strip()
            if detail:
                parts.append(detail)
                continue
        compact = str(item.get("compact", "")).strip()
        if compact:
            parts.append(compact)
    return " / ".join(parts) if prefer_detail else " · ".join(parts)


def _surface_stat_read(
    stat_items: list[dict[str, str]],
    *,
    max_chars: int = 112,
) -> str:
    if not stat_items:
        return ""

    parts = [str(item.get("compact", "")).strip() for item in stat_items if str(item.get("compact", "")).strip()]
    summary, hidden_count = _bounded_preview_join(
        parts,
        max_chars=max_chars,
    )
    if hidden_count > 0:
        overflow = _overflow_count_label(hidden_count, "stat", "stats")
        summary = f"{summary} / {overflow}" if summary else overflow
    return summary


def _detail_stat_lines(stat_items: list[dict[str, str]]) -> list[str]:
    details: list[str] = []
    for item in stat_items:
        compact = str(item.get("compact", "")).strip()
        detail = str(item.get("detail", "")).strip()
        if not detail:
            continue
        if compact and detail == compact:
            continue
        details.append(detail)
    return details


def _trim_parenthetical_suffix(label: str) -> str:
    return re.sub(r"\s*\([^)]*\)\s*$", "", label).strip()


def _normalize_exercise_name(name: str) -> str:
    cleaned = re.sub(r"(?i)^\s*\d+\s+sets?\s+", "", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if cleaned and cleaned == cleaned.lower():
        cleaned = cleaned.title()
    return cleaned


def _parse_exercise_descriptor(raw_name: str) -> dict[str, Any]:
    name = re.sub(r"\s+", " ", str(raw_name).strip())
    if not name:
        return {"name": "", "sets": 0, "weight": None, "unit": ""}

    explicit_sets = 0
    set_match = re.match(r"(?i)^\s*(\d+)\s+sets?\s+(.+)$", name)
    if set_match:
        explicit_sets = int(set_match.group(1))
        name = set_match.group(2).strip()

    weight = None
    unit = ""
    weight_match = re.search(r"\((\d+(?:\.\d+)?)\s*(lb|lbs|kg)?\)\s*$", name, flags=re.IGNORECASE)
    if weight_match:
        raw_weight = float(weight_match.group(1))
        weight = int(raw_weight) if raw_weight.is_integer() else raw_weight
        unit = (weight_match.group(2) or "").lower()
        if unit == "lb":
            unit = "lbs"
        name = name[: weight_match.start()].strip()

    return {
        "name": _normalize_exercise_name(name),
        "sets": explicit_sets,
        "weight": weight,
        "unit": unit,
    }


def _extract_rep_values(rep_text: Any) -> list[int]:
    if not isinstance(rep_text, str):
        return []
    return [int(value) for value in re.findall(r"\d+", rep_text)]


def _name_list_line(
    *,
    label: str,
    names: list[str],
    max_items: int = 4,
    overflow_singular: str = "name",
    overflow_plural: str | None = None,
    max_chars: int = 92,
) -> str:
    cleaned = []
    seen: set[str] = set()
    for name in names:
        normalized = re.sub(r"\s+", " ", name).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)

    if not cleaned:
        return ""

    visible = cleaned[:max_items]
    remainder = len(cleaned) - len(visible)
    text = " / ".join(visible)
    if remainder > 0:
        text += f" / {_overflow_count_label(remainder, overflow_singular, overflow_plural)}"
    if len(text) > max_chars:
        compact_preview = _compact_name_preview(
            cleaned,
            max_names=1,
            max_chars=max(32, max_chars - 18),
            overflow_singular=overflow_singular,
            overflow_plural=overflow_plural,
        )
        text = compact_preview or _count_phrase(
            len(cleaned),
            overflow_singular,
            overflow_plural,
        )
    return f"  - {label}: {text}"


def _movement_name_preview(
    names: list[str],
    *,
    max_names: int = 2,
    max_chars: int = 60,
    include_overflow_count: bool = True,
) -> str:
    if include_overflow_count:
        return _compact_name_preview(
            names,
            max_names=max_names,
            max_chars=max_chars,
            overflow_singular="movement",
        )

    cleaned: list[str] = []
    seen: set[str] = set()
    for name in names:
        normalized = re.sub(r"\s+", " ", str(name)).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)

    if not cleaned:
        return ""

    preview = " + ".join(cleaned[:max_names])
    if len(preview) <= max_chars:
        return preview

    trimmed = [_trim_parenthetical_suffix(name) or name for name in cleaned]
    preview = " + ".join(trimmed[:max_names])
    if len(preview) <= max_chars:
        return preview
    return _truncate_text(preview, limit=max_chars)


def _leading_name_preview(
    names: list[str],
    *,
    max_names: int = 2,
    max_chars: int = 44,
) -> str:
    cleaned: list[str] = []
    seen: set[str] = set()
    for name in names:
        normalized = re.sub(r"\s+", " ", str(name)).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)

    if not cleaned:
        return ""

    preview = " + ".join(cleaned[:max_names])
    if len(preview) <= max_chars:
        return preview
    return _truncate_text(preview, limit=max_chars)


def _surface_movement_name_read(names: list[str], *, max_chars: int = 60) -> str:
    unique_names: list[str] = []
    seen: set[str] = set()
    for name in names:
        normalized = re.sub(r"\s+", " ", str(name)).strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_names.append(normalized)

    if not unique_names:
        return ""
    count_text = _count_phrase(len(unique_names), "movement")
    lead_names = _movement_name_preview(
        unique_names,
        max_names=2,
        max_chars=max(18, max_chars - len(count_text) - 3),
    )
    if lead_names:
        return f"{count_text} · {lead_names}"
    return count_text


def _surface_movement_detail_text(
    names: list[str],
    *,
    max_chars: int = 84,
    volume_summary: str = "",
) -> str:
    unique_names = _unique_in_order(names)
    if not unique_names:
        base = _append_detail_suffix("", volume_summary) if volume_summary else ""
        return _compact_segmented_preview(base, limit=max_chars, min_keep=2)

    count_text = _count_phrase(len(unique_names), "movement")

    def _attach_volume(text: str) -> str:
        return _append_detail_suffix(text, volume_summary) if volume_summary else text

    def _compact_overflow_name_preview(max_names: int) -> str:
        visible = unique_names[:max_names]
        if not visible:
            return ""
        preview = " + ".join(visible)
        remainder = len(unique_names) - len(visible)
        if remainder > 0:
            preview += f" +{remainder} more"
        return preview

    candidates: list[str] = []
    volume_budget = len(volume_summary) + 3 if volume_summary else 0
    compact_name_budget = max(
        14,
        max_chars - len(count_text) - 3 - volume_budget,
    )

    for max_names in (2, 1):
        short_preview = _compact_overflow_name_preview(max_names)
        if short_preview:
            candidates.append(_attach_volume(f"{count_text} · {short_preview}"))
        for include_overflow_count in (True, False):
            preview = _movement_name_preview(
                unique_names,
                max_names=max_names,
                max_chars=compact_name_budget,
                include_overflow_count=include_overflow_count,
            )
            if preview:
                candidates.append(_attach_volume(f"{count_text} · {preview}"))

    for max_names in (2, 1):
        short_preview = _compact_overflow_name_preview(max_names)
        if short_preview:
            candidates.append(f"{count_text} · {short_preview}")
    candidates.append(_attach_volume(count_text))

    unique_candidates = _unique_in_order(candidates)
    for allow_ellipsis in (False, True):
        for candidate in unique_candidates:
            if not candidate or len(candidate) > max_chars:
                continue
            if not allow_ellipsis and "…" in candidate:
                continue
            return candidate

    if not volume_summary:
        return _compact_segmented_preview(
            f"{count_text} · {_movement_name_preview(unique_names, max_names=1, max_chars=max_chars, include_overflow_count=False) or count_text}",
            limit=max_chars,
            min_keep=2,
        )

    fallback_detail = _attach_volume(count_text)
    return _compact_segmented_preview(fallback_detail, limit=max_chars, min_keep=2)


def _movement_load_summary(exercise_lines: list[str]) -> str:
    if not exercise_lines:
        return ""

    loads = _unique_in_order(
        [
            f"{match.group(1)} {match.group(2)}".strip()
            for line in exercise_lines
            for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(lbs|kg)\b", line, flags=re.IGNORECASE)
        ]
    )
    if not loads:
        return ""
    if len(loads) == 1:
        return f"load {loads[0]}"
    return "loads " + " + ".join(loads)


def _exercise_weight_parts(weight: Any, unit: Any = "") -> tuple[float | int | None, str]:
    if isinstance(weight, (int, float)):
        normalized_unit = str(unit or "").strip().lower()
        if normalized_unit == "lb":
            normalized_unit = "lbs"
        return weight, normalized_unit
    if isinstance(weight, str):
        match = re.search(r"(\d+(?:\.\d+)?)\s*(lb|lbs|kg)\b", weight, flags=re.IGNORECASE)
        if match:
            value = float(match.group(1))
            if value.is_integer():
                value = int(value)
            normalized_unit = match.group(2).lower()
            if normalized_unit == "lb":
                normalized_unit = "lbs"
            return value, normalized_unit
    return None, ""


def _exercise_rep_values(value: Any) -> list[int]:
    if isinstance(value, list):
        return [int(rep) for rep in value if isinstance(rep, (int, float))]
    if isinstance(value, (int, float)):
        return [int(value)]
    if isinstance(value, str):
        return [int(rep) for rep in _extract_rep_values(value)]
    return []


def _exercise_dict_rollup(exercise: dict[str, Any]) -> dict[str, Any]:
    name = _normalize_exercise_name(str(exercise.get("name", "")))
    if not name:
        return {}

    sets_value = exercise.get("sets", 0)
    reps: list[int] = []
    set_count = 0
    weight = None
    unit = ""

    if isinstance(sets_value, list):
        set_count = len(sets_value)
        for set_item in sets_value:
            if isinstance(set_item, dict):
                reps.extend(_exercise_rep_values(set_item.get("reps")))
                if weight is None:
                    weight, unit = _exercise_weight_parts(
                        set_item.get("weight"),
                        set_item.get("unit", ""),
                    )
            else:
                reps.extend(_exercise_rep_values(set_item))
    else:
        try:
            set_count = int(sets_value or 0)
        except (TypeError, ValueError):
            set_count = 0
        for ex_k, ex_v in exercise.items():
            if ex_k.startswith("reps"):
                reps.extend(_exercise_rep_values(ex_v))

    if not reps:
        reps.extend(_exercise_rep_values(exercise.get("reps")))

    top_level_weight, top_level_unit = _exercise_weight_parts(
        exercise.get("weight"),
        exercise.get("unit", ""),
    )
    if top_level_weight is not None:
        weight, unit = top_level_weight, top_level_unit

    return {
        "name": name,
        "sets": set_count,
        "reps": reps,
        "weight": weight,
        "unit": unit,
    }


def _surface_movement_volume_summary(
    exercise_lines: list[str],
    *,
    session_count: int = 0,
    session_adjective: str | None = None,
) -> str:
    if not exercise_lines:
        return ""

    total_sets = 0
    total_reps = 0
    for line in exercise_lines:
        sets_match = re.search(r"(\d+)\s+sets", line)
        reps_match = re.search(r"(\d+)\s+reps", line)
        if sets_match:
            total_sets += int(sets_match.group(1))
        if reps_match:
            total_reps += int(reps_match.group(1))

    parts: list[str] = []
    if session_count > 1:
        parts.append(_session_phrase(session_count, session_adjective))
    if total_sets:
        parts.append(f"{total_sets} sets")
    if total_reps:
        parts.append(f"{total_reps} reps")
    load_summary = _movement_load_summary(exercise_lines)
    if load_summary:
        parts.append(load_summary)
    return " · ".join(parts)


def _collect_exercise_rollups(raw_cfs: list[dict]) -> list[dict[str, Any]]:
    rollups: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "sessions": 0,
            "sets": 0,
            "reps": 0,
            "weight": None,
            "unit": "",
            "first_seen": math.inf,
        }
    )
    order_index = 0

    for cf in raw_cfs:
        exercises = cf.get("exercises")
        if not isinstance(exercises, list):
            continue
        seen_in_session: set[str] = set()
        for ex in exercises:
            if isinstance(ex, str):
                desc, _, rep_text = ex.partition(":")
                parsed_desc = _parse_exercise_descriptor(desc)
                name = parsed_desc["name"]
                if not name:
                    continue
                if rollups[name]["first_seen"] == math.inf:
                    rollups[name]["first_seen"] = order_index
                    order_index += 1
                rollups[name]["sets"] += int(parsed_desc["sets"] or 0)
                rollups[name]["reps"] += sum(_extract_rep_values(rep_text))
                if parsed_desc["weight"] is not None and rollups[name]["weight"] is None:
                    rollups[name]["weight"] = parsed_desc["weight"]
                    rollups[name]["unit"] = parsed_desc["unit"]
            elif isinstance(ex, dict) and "name" in ex:
                parsed_ex = _exercise_dict_rollup(ex)
                name = parsed_ex.get("name", "")
                if not name:
                    continue
                if rollups[name]["first_seen"] == math.inf:
                    rollups[name]["first_seen"] = order_index
                    order_index += 1
                rollups[name]["sets"] += int(parsed_ex.get("sets", 0) or 0)
                rollups[name]["reps"] += sum(parsed_ex.get("reps", []))
                if parsed_ex.get("weight") is not None and rollups[name]["weight"] is None:
                    rollups[name]["weight"] = parsed_ex["weight"]
                    rollups[name]["unit"] = str(parsed_ex.get("unit", ""))
            elif isinstance(ex, dict):
                for ex_desc, ex_rep_text in ex.items():
                    if not isinstance(ex_rep_text, str):
                        continue
                    parsed_desc = _parse_exercise_descriptor(str(ex_desc))
                    name = parsed_desc["name"]
                    if not name:
                        continue
                    if rollups[name]["first_seen"] == math.inf:
                        rollups[name]["first_seen"] = order_index
                        order_index += 1
                    rollups[name]["sets"] += int(parsed_desc["sets"] or 0)
                    rollups[name]["reps"] += sum(_extract_rep_values(ex_rep_text))
                    if parsed_desc["weight"] is not None and rollups[name]["weight"] is None:
                        rollups[name]["weight"] = parsed_desc["weight"]
                        rollups[name]["unit"] = parsed_desc["unit"]
            else:
                continue

            if name not in seen_in_session:
                rollups[name]["sessions"] += 1
                seen_in_session.add(name)

    return [
        {"name": name, **data}
        for name, data in sorted(
            rollups.items(),
            key=lambda item: (item[1]["first_seen"], item[0]),
        )
    ]


def _exercise_rollup_lines(raw_cfs: list[dict]) -> list[str]:
    lines = []
    for rollup in _collect_exercise_rollups(raw_cfs):
        parts = []
        if rollup["sessions"] > 1:
            parts.append(_count_phrase(rollup["sessions"], "session"))
        if rollup["sets"]:
            parts.append(f"{rollup['sets']} sets")
        if rollup["reps"]:
            parts.append(f"{rollup['reps']} reps")
        if rollup["weight"] is not None:
            weight = f"{rollup['weight']} {rollup['unit']}".strip()
            if weight:
                parts.append(weight)
        if parts:
            lines.append(f"{rollup['name']} — {' · '.join(parts)}")
    return lines


def _exercise_rollup_summary(exercise_lines: list[str], structured_sessions: int) -> str:
    if not exercise_lines:
        return ""

    total_sets = 0
    total_reps = 0
    for line in exercise_lines:
        sets_match = re.search(r"(\d+)\s+sets", line)
        reps_match = re.search(r"(\d+)\s+reps", line)
        if sets_match:
            total_sets += int(sets_match.group(1))
        if reps_match:
            total_reps += int(reps_match.group(1))

    movement_count = len(exercise_lines)
    movement_intro = (
        f"{movement_count} movement across {_count_phrase(structured_sessions, 'structured session')}"
        if movement_count == 1
        else f"{movement_count} movements across {_count_phrase(structured_sessions, 'structured session')}"
    )
    parts = [movement_intro]
    if total_sets:
        parts.append(f"{total_sets} total sets")
    if total_reps:
        parts.append(f"{total_reps} total reps")
    load_summary = _movement_load_summary(exercise_lines)
    if load_summary:
        parts.append(load_summary)
    return " · ".join(parts)


def _non_exercise_aggregate_items(entries: list[dict]) -> list[dict[str, str]]:
    agg = aggregate_stats(entries)
    items = []
    for key, info in sorted(agg.items()):
        label = _format_stat_label(key)
        value = info.get("value")
        stat_type = info.get("type")
        if stat_type == "list":
            joined = ", ".join(str(item) for item in value)
            items.append(
                {
                    "name": label,
                    "compact": f"{label}: {joined}",
                    "detail": f"{label}: {joined}",
                }
            )
        elif stat_type == "bool_count":
            count = int(value)
            items.append(
                {
                    "name": label,
                    "compact": f"{label}: {count}",
                    "detail": f"{label}: {count}",
                }
            )
    return items


def _computed_stat_items(computed_stats: list[dict], *, total_sessions: int | None = None) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for stat in computed_stats:
        if stat.get("type") == "exercise":
            continue
        name = str(stat.get("name", "")).strip()
        trimmed_name = _trim_parenthetical_suffix(name)
        value = str(stat.get("value", "")).strip()
        stat_type = str(stat.get("type", "simple"))
        sessions = stat.get("sessions")
        compact = f"{trimmed_name}: {value}"
        detail = f"{trimmed_name}: {value}"
        if stat_type == "average":
            compact = f"{trimmed_name}: avg {value}"
            if isinstance(sessions, int) and sessions > 1:
                detail = f"{trimmed_name}: avg {value} across {sessions} sessions"
        elif stat_type == "total":
            compact = f"{trimmed_name}: total {value}"
            if isinstance(sessions, int) and sessions > 1:
                detail = f"{trimmed_name}: total {value} across {sessions} sessions"
        coverage_text = _session_coverage_text(sessions, total_sessions)
        if coverage_text:
            compact += coverage_text
            if stat_type == "average":
                detail = f"{trimmed_name}: avg {value}{coverage_text}"
            elif stat_type == "total":
                detail = f"{trimmed_name}: total {value}{coverage_text}"
            elif stat_type == "simple":
                detail = f"{trimmed_name}: {value}{coverage_text}"
        items.append(
            {
                "name": trimmed_name,
                "compact": compact,
                "detail": detail,
            }
        )
    return items


def _header_already_carries_names(header_state_text: str, names: list[str], *, overflow_singular: str) -> bool:
    cleaned = [str(name).strip() for name in names if str(name).strip()]
    if not cleaned:
        return False
    if overflow_singular == "movement":
        preview = _movement_name_preview(cleaned)
    else:
        preview = _compact_name_preview(cleaned, overflow_singular=overflow_singular)
    return bool(preview and preview in header_state_text)


def _extract_note_clues(
    entries: list[dict],
    limit: int = 2,
    *,
    skip_exercise_bullets: bool = False,
) -> list[str]:
    clues: list[str] = []
    key_to_index: dict[str, int] = {}
    for entry in entries:
        for raw_line in entry.get("notes", "").splitlines():
            if skip_exercise_bullets and _parse_note_exercise_bullet(raw_line):
                continue
            line = raw_line.strip()
            if not line:
                continue
            if line.lower() == "no stats to report.":
                continue
            line = re.sub(r"^[>\-\*\d\.\)\s]+", "", line).strip()
            if not line:
                continue
            for stale_phrase in (
                r"(?i)\bno stats or general notes to report this time\b\.?",
                r"(?i)\bno stats to report this time\b\.?",
                r"(?i)\bno stats or general notes this time\b\.?",
                r"(?i)\bno stats or general notes to report\b\.?",
                r"(?i)\bno stats to report\b\.?",
                r"(?i)\bno stats or general notes\b\.?",
                r"(?i)\bno stats this time\b\.?",
                r"(?i)\bno stats\b\.?",
            ):
                line = re.sub(stale_phrase, "", line)
            line = re.sub(r"(?i)[\s\.,;:-]*this time\.?$", "", line)
            line = re.sub(r"\s{2,}", " ", line).strip(" .,-;:")
            line = _normalize_note_clue(line)
            line = _truncate_text(line, limit=84)
            line = _normalize_note_clue(line)
            key = _note_clue_key(line)
            if not line or not key:
                continue
            existing_index = key_to_index.get(key)
            if existing_index is not None:
                if len(line) > len(clues[existing_index]):
                    clues[existing_index] = line
                continue
            key_to_index[key] = len(clues)
            clues.append(line)
    return clues[:limit]


def _parse_note_exercise_bullet(raw_line: str) -> dict[str, Any] | None:
    if not re.match(r"^\s*[-*]\s+", raw_line):
        return None

    cleaned = re.sub(r"^\s*[-*]\s+", "", raw_line).strip().rstrip(".")
    if ":" not in cleaned:
        return None

    left, right = cleaned.split(":", 1)
    name = left.strip()
    reps_text = right.strip()
    trailing_context = ""

    context_match = re.search(r"\(([^)]+)\)\s*$", reps_text)
    if context_match:
        trailing_context = context_match.group(1).strip()
        reps_text = reps_text[: context_match.start()].strip()

    set_match = re.match(r"(?i)(\d+)\s+sets?\s+(.+)$", name)
    explicit_sets = None
    if set_match:
        explicit_sets = int(set_match.group(1))
        name = set_match.group(2).strip()

    reps = [int(value) for value in re.findall(r"\d+", reps_text)]
    if not reps:
        return None

    weight = None
    unit = ""
    for candidate in (name, trailing_context):
        if not candidate:
            continue
        weight_match = re.search(r"(\d+(?:\.\d+)?)\s*(lb|lbs|kg)\b", candidate, flags=re.IGNORECASE)
        if weight_match:
            raw_weight = float(weight_match.group(1))
            weight = int(raw_weight) if raw_weight.is_integer() else raw_weight
            unit = weight_match.group(2).lower()
            if unit == "lb":
                unit = "lbs"
            break

    if trailing_context:
        trailing_context = re.sub(
            r"(?i)\b\d+(?:\.\d+)?\s*(?:lb|lbs|kg)\b",
            "",
            trailing_context,
        )
        trailing_context = re.sub(r"(?i)\brock\b", "", trailing_context)
        trailing_context = re.sub(r"\s{2,}", " ", trailing_context).strip(" ,;:-")

    return {
        "name": name,
        "sessions": 1,
        "sets": explicit_sets or len(reps),
        "reps": sum(reps),
        "detail": trailing_context,
        "weight": weight,
        "unit": unit,
    }


def _notes_derived_exercise_items(entries: list[dict]) -> list[dict[str, Any]]:
    aggregated: dict[str, dict[str, Any]] = {}
    order_index = 0
    for entry in entries:
        for raw_line in entry.get("notes", "").splitlines():
            parsed = _parse_note_exercise_bullet(raw_line)
            if not parsed:
                continue
            key = f"{parsed['name'].lower()}::{parsed['detail'].lower()}"
            item = aggregated.setdefault(
                key,
                {
                    "name": parsed["name"],
                    "sessions": 0,
                    "sets": 0,
                    "reps": 0,
                    "detail": parsed["detail"],
                    "weight": None,
                    "unit": "",
                    "first_seen": order_index,
                },
            )
            if item["sessions"] == 0:
                order_index += 1
            item["sessions"] += parsed["sessions"]
            item["sets"] += parsed["sets"]
            item["reps"] += parsed["reps"]
            if parsed.get("weight") is not None and item["weight"] is None:
                item["weight"] = parsed["weight"]
                item["unit"] = parsed["unit"]

    return sorted(
        aggregated.values(),
        key=lambda item: (item["first_seen"], item["name"].lower()),
    )


def _notes_derived_exercise_summary(items: list[dict[str, Any]], total_sessions: int) -> str:
    if not items:
        return ""
    total_sets = sum(int(item["sets"]) for item in items)
    total_reps = sum(int(item["reps"]) for item in items)
    load_summary = _movement_load_summary(_notes_derived_exercise_lines(items))
    summary = _count_phrase(len(items), "movement")
    summary = _append_detail_suffix(summary, f"{total_sets} total sets")
    summary = _append_detail_suffix(summary, f"{total_reps} total reps")
    if load_summary:
        summary = _append_detail_suffix(summary, load_summary)
    return _append_detail_suffix(
        summary,
        _count_phrase(total_sessions, "notes-only session"),
    )


def _notes_derived_exercise_lines(items: list[dict[str, Any]]) -> list[str]:
    lines = []
    for item in items:
        line = (
            f"{item['name']} — {_count_phrase(int(item['sessions']), 'session')} · "
            f"{int(item['sets'])} sets · {int(item['reps'])} reps"
        )
        if item.get("weight") is not None:
            weight_text = f"{item['weight']} {item.get('unit', '')}".strip()
            if weight_text:
                line += f" · {weight_text}"
        if item.get("detail"):
            line += f" · {item['detail']}"
        lines.append(line)
    return lines


def _activity_review_state(
    *,
    total_sessions: int,
    structured_sessions: int,
    exercise_lines: list[str],
    visible_stat_lines: list[str],
    note_clues: list[str],
    has_explicit_no_stats: bool,
) -> str:
    if exercise_lines:
        return f"movement data in {structured_sessions} of {total_sessions} sessions"
    if visible_stat_lines:
        return f"{_count_phrase(len(visible_stat_lines), 'stat name')} reported in {structured_sessions} of {total_sessions} sessions"
    if note_clues and has_explicit_no_stats:
        return f"notes-only + no-stats across {_count_phrase(total_sessions, 'session')}"
    if note_clues:
        return f"notes-only across {_count_phrase(total_sessions, 'session')}"
    if has_explicit_no_stats:
        return f"explicit no-stats in {_count_phrase(total_sessions, 'session')}"
    return f"no notes or stat names across {_count_phrase(total_sessions, 'session')}"


def _activity_header_state_label(
    *,
    total_sessions: int,
    structured_sessions: int,
    notes_only_count: int,
    exercise_lines: list[str],
    visible_stat_items: list[dict[str, str]],
    notes_derived_lines: list[str],
    note_derived_names: list[str],
    note_clues: list[str],
    has_explicit_no_stats: bool,
) -> str:
    stat_preview = _compact_name_preview(
        [str(item.get("name", "")).strip() for item in visible_stat_items],
        overflow_singular="stat name",
    )
    movement_preview = _compact_name_preview(
        [line.split(" — ", 1)[0] for line in exercise_lines],
        max_chars=60,
        overflow_singular="movement",
    )
    note_movement_preview = _movement_name_preview(
        note_derived_names,
    )
    if 0 < notes_only_count < total_sessions:
        mix_suffix = _capture_mix_detail_suffix(structured_sessions, notes_only_count)
        if exercise_lines:
            label = f"{_count_phrase(len(exercise_lines), 'movement')} + notes"
            detail = f"{movement_preview or _count_phrase(len(exercise_lines), 'movement')}"
            return _append_detail_suffix(label, _append_detail_suffix(detail, mix_suffix))
        if visible_stat_items:
            label = f"{_count_phrase(len(visible_stat_items), 'stat name')} + notes"
            detail = stat_preview or _count_phrase(len(visible_stat_items), "stat name")
            return _append_detail_suffix(label, _append_detail_suffix(detail, mix_suffix))
        return "capture mix"
    if exercise_lines:
        label = _count_phrase(len(exercise_lines), "movement")
        detail = movement_preview or label
        if structured_sessions > 1:
            detail = _append_detail_suffix(detail, _session_phrase(structured_sessions, "structured"))
        if detail == label:
            return label
        return _append_detail_suffix(label, detail)
    if visible_stat_items:
        detail = stat_preview or _count_phrase(len(visible_stat_items), "stat name")
        if structured_sessions > 1:
            detail = _append_detail_suffix(detail, _session_phrase(structured_sessions, "structured"))
        label = _count_phrase(len(visible_stat_items), "stat name")
        if detail.startswith(f"{label} · "):
            detail = detail[len(label) + 3 :].strip()
        elif detail == label:
            detail = ""
        return _append_detail_suffix(label, detail)
    if notes_derived_lines:
        label = _count_phrase(len(notes_derived_lines), "movement")
        detail = note_movement_preview or label
        if notes_only_count > 1:
            detail = _append_detail_suffix(detail, _session_phrase(notes_only_count, "notes-only"))
        if detail == label:
            return label
        return _append_detail_suffix(label, detail)
    if note_clues and has_explicit_no_stats:
        return (
            f"notes-only + no-stats · {_session_phrase(total_sessions)}"
            if total_sessions > 1
            else "notes-only + no-stats"
        )
    if note_clues:
        return f"notes-only · {_session_phrase(total_sessions)}" if total_sessions > 1 else "notes-only"
    if has_explicit_no_stats:
        return f"no-stats · {_session_phrase(total_sessions)}" if total_sessions > 1 else "no-stats"
    if structured_sessions:
        return "structured detail"
    return "no detail captured"


def _movement_volume_summary(exercise_lines: list[str]) -> str:
    if not exercise_lines:
        return ""

    total_sets = 0
    total_reps = 0
    for line in exercise_lines:
        sets_match = re.search(r"(\d+)\s+sets", line)
        reps_match = re.search(r"(\d+)\s+reps", line)
        if sets_match:
            total_sets += int(sets_match.group(1))
        if reps_match:
            total_reps += int(reps_match.group(1))

    parts: list[str] = []
    if total_sets:
        parts.append(f"{total_sets} total sets")
    if total_reps:
        parts.append(f"{total_reps} total reps")
    load_summary = _movement_load_summary(exercise_lines)
    if load_summary:
        parts.append(load_summary)
    return " · ".join(parts)


def _text_carries_name_preview(
    text: str,
    names: list[str],
    *,
    overflow_singular: str,
) -> bool:
    if overflow_singular == "movement":
        preview = _movement_name_preview(names)
    else:
        preview = _compact_name_preview(
            names,
            overflow_singular=overflow_singular,
        )
    return bool(preview and preview in text)


def _movement_read_summary(
    exercise_lines: list[str],
    *,
    structured_sessions: int,
) -> str:
    if not exercise_lines:
        return ""

    movement_names = [line.split(" — ", 1)[0] for line in exercise_lines]
    summary = _movement_name_preview(movement_names) or _count_phrase(len(exercise_lines), "movement")
    volume = _movement_volume_summary(exercise_lines)
    if volume:
        summary = _append_detail_suffix(summary, volume)
    if structured_sessions:
        summary = _append_detail_suffix(summary, _session_phrase(structured_sessions, "structured"))
    return summary


def _notes_derived_movement_read_summary(
    items: list[dict[str, Any]],
    *,
    notes_only_count: int,
) -> str:
    if not items:
        return ""

    movement_names = [str(item.get("name", "")).strip() for item in items]
    summary = _movement_name_preview(movement_names) or _count_phrase(len(items), "movement")

    total_sets = sum(int(item.get("sets", 0) or 0) for item in items)
    total_reps = sum(int(item.get("reps", 0) or 0) for item in items)
    volume_parts: list[str] = []
    if total_sets:
        volume_parts.append(f"{total_sets} total sets")
    if total_reps:
        volume_parts.append(f"{total_reps} total reps")
    load_summary = _movement_load_summary(_notes_derived_exercise_lines(items))
    if load_summary:
        volume_parts.append(load_summary)
    if volume_parts:
        summary = _append_detail_suffix(summary, " · ".join(volume_parts))
    if notes_only_count:
        summary = _append_detail_suffix(summary, _session_phrase(notes_only_count, "notes-only"))
    return summary


def _surface_movement_read_summary(exercise_lines: list[str]) -> str:
    if not exercise_lines:
        return ""

    movement_names = [line.split(" — ", 1)[0] for line in exercise_lines]
    volume = _movement_volume_summary(exercise_lines)
    return _surface_movement_detail_text(
        movement_names,
        max_chars=120,
        volume_summary=volume,
    )


def _surface_notes_derived_movement_read_summary(
    items: list[dict[str, Any]],
    *,
    notes_only_count: int = 0,
) -> str:
    if not items:
        return ""

    movement_names = [str(item.get("name", "")).strip() for item in items]

    total_sets = sum(int(item.get("sets", 0) or 0) for item in items)
    total_reps = sum(int(item.get("reps", 0) or 0) for item in items)
    volume_parts: list[str] = []
    if total_sets:
        volume_parts.append(f"{total_sets} total sets")
    if total_reps:
        volume_parts.append(f"{total_reps} total reps")
    load_summary = _movement_load_summary(_notes_derived_exercise_lines(items))
    if load_summary:
        volume_parts.append(load_summary)
    summary = _surface_movement_detail_text(
        movement_names,
        max_chars=120,
        volume_summary=" · ".join(volume_parts),
    )
    if notes_only_count:
        summary = _append_detail_suffix(summary, _session_phrase(notes_only_count, "notes-only"))
    return summary


def _session_stamp(entry: dict[str, Any]) -> str:
    date_text = str(entry.get("date", "")).strip()
    time_text = str(entry.get("time", "")).strip()
    if date_text and time_text:
        return f"{date_text} {time_text}"
    return date_text or time_text or "undated session"


def _single_entry_visible_stat_items(entry: dict[str, Any]) -> list[dict[str, str]]:
    raw_cfs = [entry.get("custom_fields", {}) or {}]
    computed_stats = _compute_structured_stats(raw_cfs)
    computed_items = _computed_stat_items(computed_stats, total_sessions=1)
    aggregate_items = _non_exercise_aggregate_items([entry])
    return computed_items + [item for item in aggregate_items if item not in computed_items]


def _single_entry_movement_summary(entry: dict[str, Any]) -> str:
    raw_cfs = [entry.get("custom_fields", {}) or {}]
    exercise_lines = _exercise_rollup_lines(raw_cfs)
    if not exercise_lines:
        return ""

    movement_names = [line.split(" — ", 1)[0] for line in exercise_lines]
    summary = _movement_name_preview(movement_names) or _count_phrase(len(exercise_lines), "movement")
    total_sets = 0
    total_reps = 0
    for line in exercise_lines:
        sets_match = re.search(r"(\d+)\s+sets", line)
        reps_match = re.search(r"(\d+)\s+reps", line)
        if sets_match:
            total_sets += int(sets_match.group(1))
        if reps_match:
            total_reps += int(reps_match.group(1))
    volume_parts: list[str] = []
    if total_sets:
        volume_parts.append(f"{total_sets} sets")
    if total_reps:
        volume_parts.append(f"{total_reps} reps")
    load_summary = _movement_load_summary(exercise_lines)
    if load_summary:
        volume_parts.append(load_summary)
    if volume_parts:
        summary = _append_detail_suffix(summary, " · ".join(volume_parts))
    return summary


def _single_entry_notes_derived_movement_summary(entry: dict[str, Any]) -> str:
    items = _notes_derived_exercise_items([entry])
    if not items:
        return ""

    movement_names = [str(item.get("name", "")).strip() for item in items]
    summary = _movement_name_preview(movement_names) or _count_phrase(len(items), "movement")
    total_sets = sum(int(item.get("sets", 0) or 0) for item in items)
    total_reps = sum(int(item.get("reps", 0) or 0) for item in items)
    volume_parts: list[str] = []
    if total_sets:
        volume_parts.append(f"{total_sets} sets")
    if total_reps:
        volume_parts.append(f"{total_reps} reps")
    load_summary = _movement_load_summary(_notes_derived_exercise_lines(items))
    if load_summary:
        volume_parts.append(load_summary)
    if volume_parts:
        summary = _append_detail_suffix(summary, " · ".join(volume_parts))
    return summary


def _activity_session_split_lines(
    act_entries: list[dict[str, Any]],
    *,
    compact_detail: bool = False,
) -> list[str]:
    if len(act_entries) <= 1:
        return []

    session_lines: list[str] = []
    for entry in sorted(
        act_entries,
        key=lambda item: (str(item.get("date", "")), str(item.get("time", "")), str(item.get("stem", ""))),
    ):
        raw_cf = entry.get("custom_fields", {}) or {}
        stamp = _session_stamp(entry)
        if isinstance(raw_cf.get("exercises"), list):
            movement_summary = _single_entry_movement_summary(entry)
            if movement_summary:
                session_lines.append(f"{stamp} · movement: {movement_summary}")
                continue

        stat_items = _single_entry_visible_stat_items(entry)
        if stat_items:
            stat_summary = _stat_read_line(stat_items, prefer_detail=True)
            if stat_summary:
                session_lines.append(f"{stamp} · stats: {stat_summary}")
                continue

        notes_derived_summary = _single_entry_notes_derived_movement_summary(entry)
        if notes_derived_summary:
            session_lines.append(f"{stamp} · notes-derived movement: {notes_derived_summary}")

    return _chunk_detail_lines(
        label="session splits",
        continuation_label="more session splits",
        items=session_lines,
        chunk_size=1,
        max_items=1 if compact_detail else 3,
        overflow_label=_overflow_count_label(
            max(0, len(session_lines) - (1 if compact_detail else 3)),
            "session split",
        ),
    )


def _capture_mix_surface_note_detail(
    *,
    note_clues: list[str],
    has_explicit_no_stats: bool,
    max_chars: int,
) -> str:
    parts: list[str] = []
    note_read = _surface_note_clue_read(
        note_clues,
        max_chars=max(30, max_chars - 10),
    )
    if note_read:
        parts.append(note_read)
    if has_explicit_no_stats:
        parts.append("No stats to report")
    if not parts:
        return ""
    return _compact_preview_parts(
        parts,
        limit=max_chars,
        overflow_singular="detail",
    )


def _compact_state_value_preview(
    values: list[str],
    *,
    limit: int = 78,
) -> str:
    cleaned = [_truncate_text(value, limit=54) for value in values if value]
    if not cleaned:
        return ""

    joined = " / ".join(cleaned[:2])
    if len(cleaned) > 2:
        joined += f" / +{len(cleaned) - 2} more"
    if len(joined) <= limit:
        return joined
    return _truncate_text(joined, limit=limit)


def _activity_state_detail_read(
    *,
    total_sessions: int,
    structured_sessions: int,
    notes_only_count: int,
    exercise_lines: list[str],
    visible_stat_items: list[dict[str, str]],
    notes_derived_items: list[dict[str, Any]],
    notes_derived_lines: list[str],
    note_clues: list[str],
    has_explicit_no_stats: bool,
    include_stat_values: bool = True,
) -> str:
    if 0 < notes_only_count < total_sessions:
        mix_suffix = _capture_mix_detail_suffix(structured_sessions, notes_only_count)
        if exercise_lines:
            movement_names = [line.split(" — ", 1)[0] for line in exercise_lines]
            names = (
                _movement_name_preview(movement_names)
                if include_stat_values
                else _surface_movement_name_read(movement_names)
            )
            volume = _movement_volume_summary(exercise_lines)
            detail = (names or _count_phrase(len(exercise_lines), "movement")) + " + notes"
            if include_stat_values and mix_suffix:
                detail = _append_detail_suffix(detail, mix_suffix)
            return _append_detail_suffix(detail, volume) if include_stat_values else detail
        if visible_stat_items:
            detail = _compact_name_preview(
                [str(item.get("name", "")).strip() for item in visible_stat_items],
                overflow_singular="stat name",
            ) or "reported stats"
            detail = detail + " + notes"
            if include_stat_values and mix_suffix:
                detail = _append_detail_suffix(detail, mix_suffix)
            if include_stat_values:
                stat_read = _compact_state_value_preview(
                    [str(item.get("compact", "")).strip() for item in visible_stat_items],
                )
                return _append_detail_suffix(detail, stat_read)
            return detail
        if note_clues:
            if include_stat_values and mix_suffix:
                return _append_detail_suffix(_summary_note_clue(note_clues), mix_suffix)
            return _summary_note_clue(note_clues)
        return mix_suffix

    if exercise_lines:
        movement_names = [line.split(" — ", 1)[0] for line in exercise_lines]
        names = (
            _movement_name_preview(movement_names)
            if include_stat_values
            else _surface_movement_name_read(movement_names)
        )
        volume = _movement_volume_summary(exercise_lines)
        detail = names or _count_phrase(len(exercise_lines), "movement")
        if include_stat_values and structured_sessions > 1:
            detail = _append_detail_suffix(detail, _session_phrase(structured_sessions, "structured"))
        return _append_detail_suffix(detail, volume) if include_stat_values else detail

    if visible_stat_items:
        stat_names = [str(item.get("name", "")).strip() for item in visible_stat_items]
        detail = _compact_name_preview(
            stat_names,
            overflow_singular="stat name",
        )
        if include_stat_values and structured_sessions > 1:
            detail = _append_detail_suffix(detail, _session_phrase(structured_sessions, "structured"))
        if include_stat_values:
            stat_read = _compact_state_value_preview(
                [str(item.get("compact", "")).strip() for item in visible_stat_items],
            )
            return _append_detail_suffix(detail, stat_read)
        return detail

    if notes_derived_lines:
        note_names = [str(item.get("name", "")).strip() for item in notes_derived_items]
        names = (
            _movement_name_preview(note_names)
            if include_stat_values
            else _surface_movement_name_read(note_names)
        )
        summary = _notes_derived_exercise_summary(notes_derived_items, notes_only_count)
        detail = names or _count_phrase(len(notes_derived_lines), "movement")
        if summary:
            summary = re.sub(r"^[^·]+ · ", "", summary, count=1)
        return _append_detail_suffix(detail, summary) if include_stat_values else detail

    if note_clues and has_explicit_no_stats:
        return _append_detail_suffix(_summary_note_clue(note_clues), "No stats to report")
    if note_clues:
        return _summary_note_clue(note_clues)
    if has_explicit_no_stats:
        return "No stats to report"
    return "No notes or stat names captured"


def _activity_state_summary_parts(
    act_entries: list[dict],
    *,
    include_stat_values: bool = True,
) -> tuple[str, str]:
    raw_cfs = [e.get("custom_fields", {}) or {} for e in act_entries]
    computed_stats = _compute_structured_stats(raw_cfs)
    exercise_lines = _exercise_rollup_lines(raw_cfs)
    computed_items = _computed_stat_items(computed_stats, total_sessions=len(act_entries))
    aggregate_items = _non_exercise_aggregate_items(act_entries)
    visible_stat_items = computed_items + [
        item for item in aggregate_items if item not in computed_items
    ]
    structured_sessions = sum(1 for cf in raw_cfs if cf)
    notes_only_entries = [entry for entry, cf in zip(act_entries, raw_cfs) if not cf]
    notes_derived_items = _notes_derived_exercise_items(notes_only_entries) if structured_sessions == 0 else []
    notes_derived_lines = _notes_derived_exercise_lines(notes_derived_items)
    note_clues = _extract_note_clues(
        act_entries,
        skip_exercise_bullets=bool(notes_derived_items),
    )
    notes_only_count = len(notes_only_entries)
    has_explicit_no_stats = any("no stats to report" in e.get("notes", "").lower() for e in act_entries)

    state_label = ""
    if 0 < notes_only_count < len(act_entries):
        state_label = "capture mix"
    elif exercise_lines:
        state_label = "movement data"
    elif visible_stat_items:
        state_label = "stats reported"
    elif notes_derived_lines:
        state_label = "notes-derived movements"
    elif note_clues and has_explicit_no_stats:
        state_label = "notes-only + no-stats"
    elif note_clues:
        state_label = "notes-only"
    elif has_explicit_no_stats:
        state_label = "no-stats"
    elif structured_sessions:
        state_label = "structured detail"
    else:
        state_label = "no detail captured"

    detail = _activity_state_detail_read(
        total_sessions=len(act_entries),
        structured_sessions=structured_sessions,
        notes_only_count=notes_only_count,
        exercise_lines=exercise_lines,
        visible_stat_items=visible_stat_items,
        notes_derived_items=notes_derived_items,
        notes_derived_lines=notes_derived_lines,
        note_clues=note_clues,
        has_explicit_no_stats=has_explicit_no_stats,
        include_stat_values=include_stat_values,
    )

    return state_label, detail


def _activity_summary_meaning_text(
    act_entries: list[dict],
    *,
    max_chars: int = 120,
) -> str:
    raw_cfs = [e.get("custom_fields", {}) or {} for e in act_entries]
    computed_stats = _compute_structured_stats(raw_cfs)
    exercise_lines = _exercise_rollup_lines(raw_cfs)
    computed_items = _computed_stat_items(computed_stats, total_sessions=len(act_entries))
    aggregate_items = _non_exercise_aggregate_items(act_entries)
    visible_stat_items = computed_items + [
        item for item in aggregate_items if item not in computed_items
    ]
    structured_sessions = sum(1 for cf in raw_cfs if cf)
    notes_only_entries = [entry for entry, cf in zip(act_entries, raw_cfs) if not cf]
    notes_derived_items = _notes_derived_exercise_items(notes_only_entries) if structured_sessions == 0 else []
    note_clues = _extract_note_clues(
        act_entries,
        skip_exercise_bullets=bool(notes_derived_items),
    )
    notes_only_count = len(notes_only_entries)
    has_explicit_no_stats = any("no stats to report" in e.get("notes", "").lower() for e in act_entries)
    session_suffix = _session_phrase(len(act_entries)) if len(act_entries) > 1 else ""
    notes_only_session_suffix = (
        _session_phrase(notes_only_count, "notes-only")
        if notes_only_count > 1
        else ""
    )

    def _session_first_note_read(limit: int) -> str:
        if not note_clues:
            return ""
        if len(act_entries) > 1:
            return _truncate_text(note_clues[0], limit=max(24, limit))
        return _surface_note_clue_read(note_clues, max_chars=max(24, limit))

    if 0 < notes_only_count < len(act_entries):
        mix_suffix = _capture_mix_detail_suffix(structured_sessions, notes_only_count)
        if exercise_lines:
            movement_names = [line.split(" — ", 1)[0] for line in exercise_lines]
            mix_context = _compact_session_metric_text(mix_suffix)
            movement_volume = _append_detail_suffix(
                mix_context,
                _surface_movement_volume_summary(exercise_lines),
            )
            note_detail = _capture_mix_surface_note_detail(
                note_clues=note_clues,
                has_explicit_no_stats=has_explicit_no_stats,
                max_chars=max(34, max_chars - 34),
            )
            movement_read = _surface_movement_detail_text(
                movement_names,
                max_chars=max(34, max_chars - 14),
                volume_summary=movement_volume,
            )
            detail = _compact_preview_parts(
                [movement_read, note_detail],
                limit=max(48, max_chars - 14),
                overflow_singular="read",
            )
            if (
                note_detail
                and re.search(r"\bloads?\b", movement_read, flags=re.IGNORECASE)
                and len(f"capture mix · {detail}") > max_chars
            ):
                detail = movement_read
            text = f"capture mix · {detail}" if detail else "capture mix"
            return _compact_workout_surface_text(text, limit=max_chars)
        if visible_stat_items:
            stat_read = _surface_stat_read(visible_stat_items, max_chars=max(72, max_chars - 14))
            mix_context = _compact_session_metric_text(mix_suffix)
            note_detail = _capture_mix_surface_note_detail(
                note_clues=note_clues,
                has_explicit_no_stats=has_explicit_no_stats,
                max_chars=max(34, max_chars - 34),
            )
            detail = _compact_preview_parts(
                [stat_read, mix_context, note_detail],
                limit=max(48, max_chars - 14),
                overflow_singular="read",
            )
            text = f"capture mix · {detail}" if detail else "capture mix"
            return _compact_preview_detail(text, limit=max_chars)
        if note_clues and has_explicit_no_stats:
            clue_read = _session_first_note_read(max(42, max_chars - 32))
            clue_read = _append_detail_suffix(
                clue_read,
                notes_only_session_suffix or session_suffix,
            )
            text = f"notes-only + no-stats · {clue_read}"
            return _compact_preview_detail(text, limit=max_chars)
        if note_clues:
            clue_read = _session_first_note_read(max(42, max_chars - 14))
            clue_read = _append_detail_suffix(
                clue_read,
                notes_only_session_suffix or session_suffix,
            )
            text = f"notes-only · {clue_read}"
            return _compact_preview_detail(text, limit=max_chars)
        return _compact_preview_detail("capture mix", limit=max_chars)

    if exercise_lines:
        movement_names = [line.split(" — ", 1)[0] for line in exercise_lines]
        movement_volume = _surface_movement_volume_summary(
            exercise_lines,
            session_count=structured_sessions,
            session_adjective="structured",
        )
        movement_detail = _surface_movement_detail_text(
            movement_names,
            max_chars=max(34, max_chars - 8),
            volume_summary=movement_volume,
        )
        text = f"movement data · {movement_detail}"
        return _compact_workout_surface_text(text, limit=max_chars)

    if visible_stat_items:
        stat_read = _surface_stat_read(visible_stat_items, max_chars=max(72, max_chars - 8))
        text = f"stats reported · {stat_read}" if stat_read else "stats reported"
        return _compact_preview_detail(text, limit=max_chars)

    if notes_derived_items:
        note_names = [str(item.get("name", "")).strip() for item in notes_derived_items]
        note_volume = _surface_movement_volume_summary(
            _notes_derived_exercise_lines(notes_derived_items),
            session_count=notes_only_count,
            session_adjective="notes-only",
        )
        note_detail = _surface_movement_detail_text(
            note_names,
            max_chars=max(52, max_chars - 18),
            volume_summary=note_volume,
        )
        text = "notes-derived movements · " + note_detail
        return _compact_workout_surface_text(text, limit=max_chars)

    if note_clues and has_explicit_no_stats:
        clue_read = _session_first_note_read(max(42, max_chars - 32))
        clue_read = _append_detail_suffix(
            clue_read,
            notes_only_session_suffix or session_suffix,
        )
        text = f"notes-only + no-stats · {_append_detail_suffix(clue_read, 'No stats to report')}"
        return _compact_preview_detail(text, limit=max_chars)
    if note_clues:
        clue_read = _session_first_note_read(max(42, max_chars - 14))
        clue_read = _append_detail_suffix(
            clue_read,
            notes_only_session_suffix or session_suffix,
        )
        text = f"notes-only · {clue_read}"
        return _compact_preview_detail(text, limit=max_chars)
    if has_explicit_no_stats:
        detail = "No stats to report"
        suffix = notes_only_session_suffix or session_suffix
        if suffix:
            detail = _append_detail_suffix(detail, suffix)
        return f"no-stats · {detail}"

    state_label, detail = _activity_state_summary_parts(
        act_entries,
        include_stat_values=False,
    )
    text = state_label
    if detail:
        text += f" · {detail}"
    return _compact_preview_detail(text, limit=max_chars)


def _meaning_text_parts(text: str) -> tuple[str, str]:
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if not cleaned:
        return "", ""
    if " · " not in cleaned:
        return "", cleaned
    state, detail = cleaned.split(" · ", 1)
    return state.strip(), detail.strip()


def _activity_meaning_row(
    act_name: str,
    act_entries: list[dict],
    *,
    max_chars: int = 148,
) -> dict[str, str]:
    # Keep a wider pre-compaction budget here than the final rendered row
    # gets. The compact surfaces split workout rows into primary/secondary
    # lines after this step, so giving the raw meaning text more room helps
    # preserve movement names plus rolled-up sets/reps/load before the final
    # compact pass trims it back down.
    raw_max_chars = min(260, max(max_chars + 80, 132))
    meaning_text = _activity_summary_meaning_text(
        act_entries,
        max_chars=raw_max_chars,
    )
    raw_cfs = [e.get("custom_fields", {}) or {} for e in act_entries]
    computed_stats = _compute_structured_stats(raw_cfs)
    computed_items = _computed_stat_items(computed_stats, total_sessions=len(act_entries))
    aggregate_items = _non_exercise_aggregate_items(act_entries)
    visible_stat_items = computed_items + [
        item for item in aggregate_items if item not in computed_items
    ]
    state, detail = _meaning_text_parts(meaning_text)
    state_badge = _activity_meaning_state_badge(
        act_entries,
        fallback_state=state,
    )
    raw_detail = detail or meaning_text
    should_split_for_row = state.startswith("notes-only") or state in {
        "capture mix",
        "movement data",
        "notes-derived movements",
    }
    if state == "stats reported" and len(visible_stat_items) > 1 and not act_name.startswith("+"):
        should_split_for_row = True

    if not should_split_for_row:
        detail_limit = min(132, max(44, max_chars))
        if state in {"movement data", "notes-derived movements", "capture mix"}:
            rendered_detail = _compact_workout_surface_text(raw_detail, limit=detail_limit) if raw_detail else raw_detail
        else:
            rendered_detail = _compact_preview_detail(raw_detail, limit=detail_limit) if raw_detail else raw_detail
        row = {"title": act_name, "detail": rendered_detail or raw_detail or meaning_text}
        if state:
            row["state"] = state
        if state_badge and state_badge != state:
            row["state_badge"] = state_badge
        return row

    primary, secondary = _split_meaning_row_detail(
        state=state,
        detail=raw_detail,
    )

    detail_limit = min(132, max(44, max_chars))
    secondary_limit = min(116, max(32, max_chars))

    if state == "capture mix" and secondary:
        secondary = _strip_low_priority_surface_suffixes(secondary)
        metric_segments: list[str] = []
        for segment in [part.strip() for part in secondary.split(" · ") if part.strip()]:
            if re.search(
                r"\b(?:structured|notes-only)\s+sessions?\b|\b\d+\s+sets\b|\b\d+\s+reps\b|\bloads?\b|No stats to report\b",
                segment,
                flags=re.IGNORECASE,
            ):
                metric_segments.append(segment)
                continue
            if metric_segments:
                break
        if metric_segments:
            secondary = " · ".join(metric_segments)

    if state in {"movement data", "notes-derived movements", "capture mix"}:
        primary = _compact_workout_surface_text(primary, limit=detail_limit) if primary else primary
        secondary = _compact_workout_surface_text(secondary, limit=secondary_limit) if secondary else secondary
    else:
        primary = _compact_preview_detail(primary, limit=detail_limit) if primary else primary
        secondary = _compact_preview_detail(secondary, limit=secondary_limit) if secondary else secondary

    row = {"title": act_name, "detail": primary or detail or meaning_text}
    if state:
        row["state"] = state
    if state_badge and state_badge != state:
        row["state_badge"] = state_badge
    if secondary and should_split_for_row:
        row["detail_secondary"] = secondary
    return row


def _activity_meaning_state_badge(
    act_entries: list[dict[str, Any]],
    *,
    fallback_state: str,
) -> str:
    raw_cfs = [e.get("custom_fields", {}) or {} for e in act_entries]
    computed_stats = _compute_structured_stats(raw_cfs)
    exercise_lines = _exercise_rollup_lines(raw_cfs)
    computed_items = _computed_stat_items(computed_stats, total_sessions=len(act_entries))
    aggregate_items = _non_exercise_aggregate_items(act_entries)
    visible_stat_items = computed_items + [
        item for item in aggregate_items if item not in computed_items
    ]
    structured_sessions = sum(1 for cf in raw_cfs if cf)
    notes_only_entries = [entry for entry, cf in zip(act_entries, raw_cfs) if not cf]
    notes_derived_items = _notes_derived_exercise_items(notes_only_entries) if structured_sessions == 0 else []
    note_clues = _extract_note_clues(
        act_entries,
        skip_exercise_bullets=bool(notes_derived_items),
    )
    notes_only_count = len(notes_only_entries)
    has_explicit_no_stats = any("no stats to report" in e.get("notes", "").lower() for e in act_entries)

    if 0 < notes_only_count < len(act_entries):
        if exercise_lines:
            return f"{_count_phrase(len(exercise_lines), 'movement')} + notes"
        if visible_stat_items:
            return f"{_count_phrase(len(visible_stat_items), 'stat name')} + notes"
        return "capture mix"

    if exercise_lines:
        return _count_phrase(len(exercise_lines), "movement")

    if visible_stat_items:
        return _count_phrase(len(visible_stat_items), "stat name")

    if notes_derived_items:
        return _count_phrase(len(notes_derived_items), "movement")

    if note_clues and has_explicit_no_stats:
        return "notes-only + no-stats"
    if note_clues:
        return "notes-only"
    if has_explicit_no_stats:
        return "no-stats"
    if structured_sessions:
        return "structured"
    return fallback_state


def _split_meaning_row_detail(
    *,
    state: str,
    detail: str,
) -> tuple[str, str]:
    cleaned_state = re.sub(r"\s+", " ", str(state)).strip().lower()
    remaining_detail = re.sub(r"\s+", " ", str(detail)).strip()
    if not remaining_detail:
        return "", ""

    secondaries: list[str] = []
    movement_secondary_pattern = (
        r"\s+·\s+("
        r"(?:\d+\s+(?:structured|notes-only)\s+sessions?\b(?:\s*·\s*)?)?"
        r"(?:\d+\s+(?:total\s+)?sets\b.*|loads?\b.*|No stats to report\b.*)"
        r")$"
    )

    for _ in range(3):
        changed = False

        overflow_match = re.search(
            r"(?P<primary>.*?)(?P<secondary>(?:\s*/\s*\+\d+\s+more\s+(?:note\s+reads?|stats?|reads?\s+below))|(?:\s*·\s*\+\d+\s+more\s+reads?\s+below))$",
            remaining_detail,
            flags=re.IGNORECASE,
        )
        if overflow_match:
            primary = overflow_match.group("primary").strip()
            secondary = re.sub(
                r"^[\s/·]+",
                "",
                overflow_match.group("secondary"),
            ).strip()
            if primary and secondary:
                remaining_detail = primary
                secondaries.append(secondary)
                changed = True
                continue

        if " · No stats to report" in remaining_detail:
            primary, _ = remaining_detail.split(" · No stats to report", 1)
            remaining_detail = primary.strip()
            secondaries.insert(0, "No stats to report")
            changed = True
            continue

        if cleaned_state in {"notes-only", "notes-only + no-stats", "no-stats"}:
            session_match = re.search(
                r"^(?P<primary>.*?)(?:\s*·\s*(?P<secondary>\d+\s+(?:(?:notes-only|structured)\s+)?sessions?))$",
                remaining_detail,
                flags=re.IGNORECASE,
            )
            if session_match:
                primary = session_match.group("primary").strip()
                secondary = session_match.group("secondary").strip()
                if primary and secondary:
                    remaining_detail = primary
                    secondaries.append(secondary)
                    changed = True
                    continue

        if cleaned_state == "capture mix" and " / " in remaining_detail:
            primary, secondary = remaining_detail.rsplit(" / ", 1)
            if primary and secondary and re.search(r"\bsets\b|\breps\b|\bloads?\b|:", primary, flags=re.IGNORECASE):
                remaining_detail = primary.strip()
                secondaries.append(secondary.strip())
                changed = True
                continue

        if cleaned_state == "stats reported":
            multi_stat_match = re.search(
                r"^(?P<primary>.+?)(?:\s+/\s+)(?P<secondary>.+)$",
                remaining_detail,
                flags=re.IGNORECASE,
            )
            if multi_stat_match:
                primary = multi_stat_match.group("primary").strip()
                secondary = multi_stat_match.group("secondary").strip()
                if primary and secondary:
                    remaining_detail = primary
                    secondaries.append(secondary)
                    changed = True
                    continue
            coverage_match = re.search(
                r"^(?P<primary>.*?)(?:\s*\((?P<coverage>\d+/\d+\s+sessions?)\))$",
                remaining_detail,
                flags=re.IGNORECASE,
            )
            if coverage_match:
                primary = coverage_match.group("primary").strip()
                coverage = coverage_match.group("coverage").strip()
                if primary and coverage:
                    remaining_detail = primary
                    secondaries.append(coverage)
                    changed = True
                    continue

        if cleaned_state in {"movement data", "notes-derived movements", "capture mix"}:
            movement_suffix_match = re.search(
                movement_secondary_pattern,
                remaining_detail,
                flags=re.IGNORECASE,
            )
            if movement_suffix_match:
                start = movement_suffix_match.start()
                primary = remaining_detail[:start].strip()
                secondary = movement_suffix_match.group(1).strip()
                if primary and secondary:
                    remaining_detail = primary
                    secondaries.insert(0, secondary)
                    changed = True
                    continue

        if not changed:
            break

    if secondaries:
        if cleaned_state in {"movement data", "notes-derived movements", "capture mix"}:
            secondaries = [
                re.sub(r"\btotal sets\b", "sets", secondary, flags=re.IGNORECASE)
                for secondary in secondaries
            ]
            secondaries = [
                re.sub(r"\btotal reps\b", "reps", secondary, flags=re.IGNORECASE)
                for secondary in secondaries
            ]
            secondaries = [
                re.sub(r"\+(\d+)\s+more\s+movements\b", r"+\1 more", secondary, flags=re.IGNORECASE)
                for secondary in secondaries
            ]
        return remaining_detail, " · ".join(secondaries)

    if cleaned_state == "stats reported":
        coverage_match = re.search(
            r"^(?P<primary>.*?)(?:\s*\((?P<coverage>\d+/\d+\s+sessions?)\))$",
            remaining_detail,
            flags=re.IGNORECASE,
        )
        if coverage_match:
            primary = coverage_match.group("primary").strip()
            coverage = coverage_match.group("coverage").strip()
            if primary and coverage:
                return primary, coverage

    if cleaned_state in {"movement data", "notes-derived movements", "capture mix"}:
        movement_suffix_match = re.search(
            movement_secondary_pattern,
            remaining_detail,
            flags=re.IGNORECASE,
        )
        if movement_suffix_match:
            start = movement_suffix_match.start()
            primary = remaining_detail[:start].strip()
            secondary = movement_suffix_match.group(1).strip()
            if primary and secondary:
                secondary = re.sub(r"\btotal sets\b", "sets", secondary, flags=re.IGNORECASE)
                secondary = re.sub(r"\btotal reps\b", "reps", secondary, flags=re.IGNORECASE)
                secondary = re.sub(r"\+(\d+)\s+more\s+movements\b", r"+\1 more", secondary, flags=re.IGNORECASE)
                return primary, secondary

    return remaining_detail, ""


def _compact_secondary_surface_text(
    text: str,
    *,
    limit: int = 88,
) -> str:
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if not cleaned:
        return ""

    replacements = [
        (r"\bnotes-only sessions\b", "notes-only"),
        (r"\bstructured sessions\b", "structured"),
        (r"\bsessions\b", "sess"),
        (r"\bNo stats to report\b", "No stats"),
        (r"\bfocused session notes\b", "session notes"),
    ]
    compact = cleaned
    for pattern, replacement in replacements:
        compact = re.sub(pattern, replacement, compact, flags=re.IGNORECASE)
    compact = re.sub(r"\btotal sets\b", "sets", compact, flags=re.IGNORECASE)
    compact = re.sub(r"\btotal reps\b", "reps", compact, flags=re.IGNORECASE)

    segments = [segment.strip() for segment in compact.split(" · ") if segment.strip()]
    if (
        segments
        and not any(re.search(r"\s[—-]\s", segment) for segment in segments)
        and any(re.search(r"\bsets\b|\breps\b", segment, flags=re.IGNORECASE) for segment in segments)
    ):
        volume_segments: list[str] = []
        load_segments: list[str] = []
        session_segments: list[str] = []
        other_segments: list[str] = []
        for segment in segments:
            if re.search(r"\bsets\b|\breps\b", segment, flags=re.IGNORECASE):
                volume_segments.append(segment)
            elif re.search(r"\bloads?\b", segment, flags=re.IGNORECASE):
                load_segments.append(segment)
            elif re.search(r"\b(?:structured|notes-only|No stats|sess)\b", segment, flags=re.IGNORECASE):
                session_segments.append(segment)
            else:
                other_segments.append(segment)

        reordered_variants = _unique_in_order(
            [
                " · ".join(volume_segments + load_segments + session_segments + other_segments),
                " · ".join(volume_segments + session_segments + load_segments + other_segments),
                compact,
            ]
        )
        compact = next(
            (variant for variant in reordered_variants if len(variant) <= limit),
            reordered_variants[0] if reordered_variants else compact,
        )

    return _compact_preview_detail(compact, limit=limit)


def _suppress_redundant_compact_note_secondary(
    *,
    state: str,
    detail: str,
    detail_secondary: str,
) -> str:
    cleaned_state = re.sub(r"\s+", " ", str(state)).strip().lower()
    cleaned_detail = re.sub(r"\s+", " ", str(detail)).strip()
    cleaned_secondary = re.sub(r"\s+", " ", str(detail_secondary)).strip()
    if not cleaned_state or not cleaned_secondary:
        return cleaned_secondary

    if re.search(r"\s[—-]\s", cleaned_secondary):
        return cleaned_secondary
    if re.search(r"\bsets\b|\breps\b|\bloads?\b|:\s", cleaned_secondary, flags=re.IGNORECASE):
        return cleaned_secondary
    if "notes-only" not in cleaned_state and "no-stats" not in cleaned_state:
        return cleaned_secondary
    if not cleaned_detail:
        return cleaned_secondary

    if (
        "no-stats" in cleaned_state
        and re.match(r"^No stats(?: to report)?(?:\s*·\s*\d+\s+notes-only(?:\s+(?:sess|sessions?))?)?$", cleaned_secondary, flags=re.IGNORECASE)
    ):
        return "No stats to report"

    if re.fullmatch(
        r"(?:No stats(?: to report)?(?:\s*·\s*)?)?(?:\d+\s+notes-only(?:\s+(?:sess|sessions?))?)?",
        cleaned_secondary,
        flags=re.IGNORECASE,
    ):
        return ""
    return cleaned_secondary


def _compact_grouped_metric_suffix(
    text: str,
    *,
    limit: int = 56,
    include_loads: bool = False,
    prefer_volume: bool = False,
) -> str:
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if not cleaned:
        return ""
    replacements = [
        (r"\bnotes-only sessions\b", "notes-only"),
        (r"\bstructured sessions\b", "structured"),
        (r"\bsessions\b", "sess"),
        (r"\bNo stats to report\b", "No stats"),
        (r"\bfocused session notes\b", "session notes"),
    ]
    for pattern, replacement in replacements:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

    volume_segments: list[str] = []
    session_segments: list[str] = []
    load_segments: list[str] = []
    for segment in [part.strip() for part in cleaned.split(" · ") if part.strip()]:
        if re.search(r"\bsets\b|\breps\b", segment, flags=re.IGNORECASE):
            volume_segments.append(segment)
            continue
        if re.search(r"\b(?:structured|notes-only|No stats|sess)\b", segment, flags=re.IGNORECASE):
            session_segments.append(segment)
            continue
        if include_loads and re.search(r"\bloads?\b", segment, flags=re.IGNORECASE):
            load_segments.append(segment)
    candidate_parts: list[list[str]] = []
    if prefer_volume:
        candidate_parts.extend(
            [
                volume_segments + session_segments + load_segments,
                volume_segments + load_segments,
                volume_segments,
                session_segments + volume_segments + load_segments,
            ]
        )
    else:
        candidate_parts.extend(
            [
                session_segments + volume_segments + load_segments,
                session_segments + volume_segments,
                volume_segments + session_segments + load_segments,
                volume_segments,
            ]
        )

    candidates: list[str] = []
    for parts in candidate_parts:
        joined = " · ".join(part for part in parts if part)
        if not joined:
            continue
        candidates.append(_compact_preview_detail(joined, limit=limit))

    ranked = _unique_in_order(candidates)
    if not ranked:
        return ""

    def _candidate_key(text: str) -> tuple[int, int, int]:
        has_sets = bool(re.search(r"\bsets\b", text, flags=re.IGNORECASE))
        has_reps = bool(re.search(r"\breps\b", text, flags=re.IGNORECASE))
        has_ellipsis = "…" in text
        return (
            1 if has_sets and has_reps else 0,
            1 if not has_ellipsis else 0,
            len(text),
        )

    return max(ranked, key=_candidate_key)


def _compact_grouped_workout_name_preview_variants(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if not cleaned:
        return []

    variants = [cleaned]
    dequalified = re.sub(r"\s*\([^)]*\)", "", cleaned).strip()
    dequalified = re.sub(r"\s+", " ", dequalified).strip()
    if dequalified and dequalified not in variants:
        variants.append(dequalified)

    compact_symbols = re.sub(r"\s+\+\s+", " + ", dequalified or cleaned)
    compact_symbols = re.sub(r"\s+", " ", compact_symbols).strip()
    if compact_symbols and compact_symbols not in variants:
        variants.append(compact_symbols)

    shortened = re.sub(
        r"^(.+?)\s+\+\s+.+?(\s+\+\d+\s+more)$",
        r"\1\2",
        compact_symbols or dequalified or cleaned,
        flags=re.IGNORECASE,
    )
    if shortened != cleaned:
        variants.append(shortened)

    lead_only = re.sub(
        r"^(.+?)\s+\+\s+.+$",
        r"\1",
        shortened,
        flags=re.IGNORECASE,
    )
    if lead_only and lead_only not in variants:
        variants.append(lead_only)
    return _unique_in_order(variants)


def _strip_grouped_workout_preview_metrics(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if not cleaned or " · " not in cleaned:
        return cleaned

    prefix_segments: list[str] = []
    volume_segments: list[str] = []
    fallback_metric = ""
    for segment in [part.strip() for part in cleaned.split(" · ") if part.strip()]:
        if re.search(r"\bloads?\b", segment, flags=re.IGNORECASE):
            continue
        if re.search(r"\bsets\b|\breps\b", segment, flags=re.IGNORECASE):
            volume_segments.append(segment)
            continue
        if re.search(r"\b(?:structured|notes-only|No stats|sessions?)\b", segment, flags=re.IGNORECASE):
            if not fallback_metric:
                fallback_metric = segment
            continue
        prefix_segments.append(segment)

    kept = prefix_segments + volume_segments[:2]
    if not volume_segments and fallback_metric:
        kept.append(fallback_metric)
    if not kept:
        return cleaned
    return " · ".join(kept)


def _strip_redundant_badge_prefix(detail: str, badge: str) -> str:
    cleaned_detail = re.sub(r"\s+", " ", str(detail)).strip()
    cleaned_badge = re.sub(r"\s+", " ", str(badge)).strip()
    if not cleaned_detail or not cleaned_badge:
        return cleaned_detail

    prefixes = [f"{cleaned_badge} · ", f"{cleaned_badge}: "]
    if cleaned_badge.endswith(" + notes"):
        base_badge = cleaned_badge[: -len(" + notes")].strip()
        if base_badge:
            prefixes.extend(
                [
                    f"{base_badge} · ",
                    f"{base_badge}: ",
                ]
            )
    if cleaned_badge.endswith(" · notes"):
        base_badge = cleaned_badge[: -len(" · notes")].strip()
        if base_badge:
            prefixes.extend(
                [
                    f"{base_badge} · ",
                    f"{base_badge}: ",
                    f"notes · {base_badge} · ",
                    f"notes · {base_badge}: ",
                ]
            )

    for prefix in prefixes:
        if cleaned_detail.startswith(prefix):
            return cleaned_detail[len(prefix):].strip()
    return cleaned_detail


def _strip_redundant_badge_after_activity_prefix(
    detail: str,
    badge: str,
) -> str:
    cleaned_detail = re.sub(r"\s+", " ", str(detail)).strip()
    cleaned_badge = re.sub(r"\s+", " ", str(badge)).strip()
    if not cleaned_detail or not cleaned_badge:
        return cleaned_detail

    patterns = [
        rf"^(from\s+[^·]+?\s+·\s+){re.escape(cleaned_badge)}\s*·\s*",
        rf"^([^—]+?\s+[—-]\s+){re.escape(cleaned_badge)}\s*·\s*",
    ]
    for pattern in patterns:
        match = re.match(pattern, cleaned_detail)
        if match:
            return (match.group(1) + cleaned_detail[match.end():]).strip()
    return cleaned_detail


def _grouped_preview_prefers_activity_owned_detail(
    visible_state: str,
    detail: str,
) -> bool:
    cleaned_state = re.sub(r"\s+", " ", str(visible_state)).strip()
    cleaned_detail = re.sub(r"\s+", " ", str(detail)).strip()
    if not cleaned_state or not cleaned_detail:
        return False
    if cleaned_state.startswith("notes-only"):
        return bool(
            re.search(r"\bNo stats(?: to report)?\b", cleaned_detail, flags=re.IGNORECASE)
            or len(cleaned_detail.split()) >= 2
        )
    if not re.match(
        r"^\d+\s+(?:stat name|movement)s?(?:\s+\+\s+notes)?$",
        cleaned_state,
        flags=re.IGNORECASE,
    ):
        return False
    return bool(
        re.search(r":", cleaned_detail)
        or re.search(r"\+\d+\s+more\b", cleaned_detail, flags=re.IGNORECASE)
    )


def _grouped_preview_line(
    act_name: str,
    activity_row: dict[str, str],
    *,
    max_chars: int,
    preview_count: int,
) -> str:
    workout_states = {"capture mix", "movement data", "notes-derived movements"}
    preserve_compact_metrics = False
    state = str(activity_row.get("state", "")).strip()
    state_badge = _meaning_row_state_badge(activity_row)
    _, detail, detail_secondary = _meaning_row_display_parts(activity_row)
    detail = re.sub(r"\s*·\s*\+\d+\s+more\s+reads?\s+below\b.*$", "", detail).strip()
    detail = re.sub(r"\s*·\s*\+\d+\s+more\s+reads?\b.*$", "", detail).strip()
    detail = re.sub(r"\s*/\s*\+\d+\s+more\s+reads?\b.*$", "", detail).strip()
    detail_secondary = re.sub(
        r"\s*·\s*\+\d+\s+more\s+reads?\s+below\b.*$",
        "",
        detail_secondary,
    ).strip()
    detail_secondary = re.sub(r"\s*·\s*\+\d+\s+more\s+reads?\b.*$", "", detail_secondary).strip()
    detail_secondary = re.sub(r"\s*/\s*\+\d+\s+more\s+reads?\b.*$", "", detail_secondary).strip()

    visible_state = state
    if (
        state_badge
        and state_badge != state
        and state in DISPLAY_BADGE_STATE_LABELS
    ):
        visible_state = state_badge
    show_visible_state = visible_state
    if _grouped_preview_prefers_activity_owned_detail(visible_state, detail):
        show_visible_state = ""
    if visible_state:
        detail = _strip_redundant_badge_prefix(detail, visible_state)
    if state_badge and state_badge != visible_state:
        detail = _strip_redundant_badge_prefix(detail, state_badge)
    if preview_count > 1 and state in workout_states:
        detail = _strip_session_mix_segment(detail)

    line = act_name
    if show_visible_state:
        line += f" — {show_visible_state}"
    if detail and not show_visible_state:
        line += f" — {detail}"
    elif detail:
        line += f" · {detail}"

    if detail_secondary:
        append_secondary = preview_count == 1 or state not in {
            "capture mix",
            "movement data",
            "notes-derived movements",
        }
        metric_suffix = ""
        if state in {"capture mix", "movement data", "notes-derived movements"}:
            metric_suffix = _compact_grouped_metric_suffix(
                detail_secondary,
                limit=64 if preview_count == 1 else 38,
                include_loads=preview_count == 1,
                prefer_volume=preview_count > 1,
            )
            if metric_suffix:
                metric_candidates: list[str] = []
                candidate = _append_detail_suffix(line, metric_suffix)
                if len(candidate) <= max_chars:
                    metric_candidates.append(candidate)
                if preview_count > 1:
                    available_detail_chars = max(24, max_chars - len(act_name) - 3)
                    compact_variants = []
                    for compact_detail in _compact_grouped_workout_name_preview_variants(detail):
                        variant_part_sets = [
                            [show_visible_state, compact_detail, metric_suffix],
                            [compact_detail, metric_suffix],
                            [show_visible_state, metric_suffix],
                            [metric_suffix],
                        ]
                        for part_set in variant_part_sets:
                            compact_variants.append(
                                " · ".join(part for part in part_set if part)
                            )
                    for compact_content in _unique_in_order(compact_variants):
                        compact_content = _compact_preview_detail(
                            compact_content,
                            limit=available_detail_chars,
                        )
                        compact_candidate = f"{act_name} — {compact_content}" if compact_content else ""
                        if (
                            compact_candidate
                            and len(compact_candidate) <= max_chars
                            and re.search(r"\bsets\b|\breps\b", compact_candidate, flags=re.IGNORECASE)
                        ):
                            metric_candidates.append(compact_candidate)
                if metric_candidates:
                    def _metric_candidate_key(text: str) -> tuple[int, int, int, int, int, int]:
                        has_sets = bool(re.search(r"\bsets\b", text, flags=re.IGNORECASE))
                        has_reps = bool(re.search(r"\breps\b", text, flags=re.IGNORECASE))
                        has_ellipsis = "…" in text
                        preview_head = re.split(r"\s+·\s+\d+\s+sets\b", text, maxsplit=1)[0]
                        preview_content = re.split(r"\s+[—-]\s+", preview_head, maxsplit=1)
                        preview_segments = []
                        if len(preview_content) == 2:
                            preview_segments = [
                                segment.strip()
                                for segment in preview_content[1].split(" · ")
                                if segment.strip()
                            ]
                        generic_preview_pattern = re.compile(
                            r"^(?:"
                            r"\d+\s+(?:stat name|movement)s?(?:\s+\+\s+notes)?|"
                            r"notes-only(?:\s+\+\s+no-stats)?|"
                            r"no-stats|"
                            r"capture mix|"
                            r"movement data|"
                            r"notes-derived movements"
                            r")$",
                            flags=re.IGNORECASE,
                        )
                        metric_preview_pattern = re.compile(
                            r"^(?:\d+\s+(?:sets|reps)\b|loads?\b)",
                            flags=re.IGNORECASE,
                        )
                        has_activity_owned_preview = False
                        for index, segment in enumerate(preview_segments):
                            if generic_preview_pattern.match(segment):
                                continue
                            if metric_preview_pattern.match(segment):
                                continue
                            if re.search(r"[A-Za-z]", segment):
                                has_activity_owned_preview = True
                                break
                        has_count_badge = bool(
                            preview_segments
                            and generic_preview_pattern.match(preview_segments[0])
                        )
                        name_plus_count = preview_head.count(" + ")
                        has_parenthetical = bool(re.search(r"\([^)]*\)", text))
                        return (
                            1 if has_sets and has_reps else 0,
                            1 if not has_ellipsis else 0,
                            1 if has_activity_owned_preview else 0,
                            1 if has_count_badge else 0,
                            name_plus_count,
                            1 if not has_parenthetical else 0,
                            -len(text) if preview_count > 1 else len(text),
                        )

                    line = max(_unique_in_order(metric_candidates), key=_metric_candidate_key)
                    preserve_compact_metrics = preview_count > 1
                    append_secondary = False
        if append_secondary:
            line += f" · {detail_secondary}"

    if preview_count > 1 and state in workout_states and not preserve_compact_metrics:
        line = _strip_grouped_workout_preview_metrics(line)

    return _truncate_text(line, limit=max_chars)


def _meaning_row_display_parts(row: dict[str, str]) -> tuple[str, str, str]:
    state = str(row.get("state", "")).strip()
    badge = str(row.get("state_badge", "")).strip() or state
    detail = str(row.get("detail", "")).strip()
    detail_secondary = str(row.get("detail_secondary", "")).strip()
    workout_secondary = bool(
        re.search(r"\bsets\b|\breps\b|\bloads?\b", detail_secondary, flags=re.IGNORECASE)
    )
    if detail_secondary:
        if workout_secondary or state.startswith("notes-only") or state in {
            "capture mix",
            "movement data",
            "notes-derived movements",
            "no-stats",
        }:
            detail_secondary = _compact_secondary_surface_text(detail_secondary)
        detail = _strip_redundant_badge_prefix(detail, badge)
        if state == "no-stats" and detail_secondary.lower().startswith("no stats"):
            stripped_secondary = re.sub(r"^No stats(?: to report)?\s*·?\s*", "", detail_secondary, flags=re.IGNORECASE).strip()
            detail_secondary = stripped_secondary
        detail_secondary = _suppress_redundant_compact_note_secondary(
            state=state,
            detail=detail,
            detail_secondary=detail_secondary,
        )
        return state, detail, detail_secondary
    primary, secondary = _split_meaning_row_detail(
        state=state,
        detail=detail,
    )
    if secondary and (
        state.startswith("notes-only")
        or state in {"capture mix", "movement data", "notes-derived movements", "no-stats"}
    ):
        secondary = _compact_secondary_surface_text(secondary)
    primary = _strip_redundant_badge_prefix(primary, badge)
    if state == "no-stats" and secondary.lower().startswith("no stats"):
        secondary = re.sub(r"^No stats(?: to report)?\s*·?\s*", "", secondary, flags=re.IGNORECASE).strip()
    secondary = _suppress_redundant_compact_note_secondary(
        state=state,
        detail=primary,
        detail_secondary=secondary,
    )
    return state, primary, secondary


def _signature_surface_meaning_parts(row: dict[str, str]) -> tuple[str, str, str]:
    state, detail, detail_secondary = _meaning_row_display_parts(row)
    title = str(row.get("title", "")).strip()
    secondary_has_activity_owned_preview = bool(
        re.search(r"\s[—-]\s", detail_secondary)
    )
    secondary_is_workout_volume = bool(
        re.search(r"\bsets\b|\breps\b|\bloads?\b", detail_secondary, flags=re.IGNORECASE)
    )
    if (
        title.startswith("+")
        and state
        and detail_secondary
        and not secondary_has_activity_owned_preview
        and not secondary_is_workout_volume
    ):
        detail = re.sub(
            r"\s*·\s*\+\d+\s+more\s+reads?\b.*$",
            "",
            detail,
            flags=re.IGNORECASE,
        ).strip()
        detail_secondary = ""
    return state, detail, detail_secondary


def _meaning_row_state_badge(row: dict[str, str]) -> str:
    state = str(row.get("state", "")).strip()
    detail = str(row.get("detail", "")).strip()
    detail_secondary = str(row.get("detail_secondary", "")).strip()
    if (
        state == "no detail captured"
        and (
            "No notes or stat names captured" in detail
            or "No notes or stat names captured" in detail_secondary
        )
    ):
        return ""
    badge = str(row.get("state_badge", "")).strip()
    if badge:
        return badge
    return state


def _strip_shared_state_from_preview_line(text: str, state: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    shared_state = re.sub(r"\s+", " ", str(state)).strip()
    if not cleaned or not shared_state:
        return cleaned

    pattern = re.compile(
        rf"^(?P<name>.+?)\s+[—-]\s+{re.escape(shared_state)}\s*·\s*(?P<detail>.+)$",
        flags=re.IGNORECASE,
    )
    match = pattern.match(cleaned)
    if not match:
        return cleaned

    activity_name = match.group("name").strip()
    detail = match.group("detail").strip()
    if not activity_name or not detail:
        return cleaned
    return f"{activity_name} — {detail}"


def _overflow_meaning_compact_text(
    act_entries: list[dict],
    *,
    max_chars: int = 78,
) -> str:
    state_label, detail = _activity_state_summary_parts(
        act_entries,
        include_stat_values=False,
    )
    if detail:
        detail = re.sub(r"\s*·\s*\d+\s+total\s+sets\b.*$", "", detail).strip()
        detail = re.sub(
            r"\s*·\s*\d+\s+structured\s+sessions?(?:\s*\+\s*\d+\s+notes-only\s+sessions?)?$",
            "",
            detail,
        ).strip()
        detail = re.sub(r"\s*·\s*\d+\s+notes-only\s+sessions?$", "", detail).strip()
        detail = re.sub(r"\s*·\s*No stats to report$", "", detail).strip()
    text = f"{state_label} · {detail}" if state_label and detail else detail or state_label
    return _compact_preview_detail(text, limit=max_chars)


def _activity_overflow_meaning_text(
    act_entries: list[dict],
    *,
    max_chars: int = 88,
) -> str:
    state_label, detail = _activity_state_summary_parts(
        act_entries,
        include_stat_values=False,
    )
    text = state_label
    if detail:
        text = f"{text} · {detail}" if text else detail
    return _compact_preview_detail(text, limit=max_chars)


def _overflow_meaning_row(
    acts: list[tuple[str, int, list[dict]]],
    *,
    title_acts: list[tuple[str, int, list[dict]]] | None = None,
    prefix_count: int | None = None,
    max_items: int = 1,
    max_chars: int = 164,
    title_max_chars: int = 52,
    compact_title: bool = False,
) -> dict[str, str]:
    if not acts:
        return {}

    def _badge_for_row(row: dict[str, str]) -> str:
        return _meaning_row_state_badge(row)

    preview_acts = _overflow_activity_preview_acts(acts, max_items=max_items)
    preview_names = [act_name for act_name, _, _ in preview_acts]
    title_source = title_acts or acts
    title_names = [act_name for act_name, _, _ in title_source[: len(preview_acts)]]
    title_matches_preview_order = (not compact_title) and preview_names == title_names
    if (
        not compact_title
        and len(preview_acts) > 1
        and preview_names
        and not title_matches_preview_order
    ):
        title_source = preview_acts
        title_names = [act_name for act_name, _, _ in title_source[: len(preview_acts)]]
        title_matches_preview_order = preview_names == title_names

    if compact_title:
        title = _more_group_title(
            prefix_count or len(acts),
            singular="grouped read",
            plural="grouped reads",
        )
        title_visible_names: set[str] = set()
    else:
        title = _overflow_activity_title(
            title_source,
            prefix_count=prefix_count or len(acts),
            max_chars=title_max_chars,
        )
        title_visible_names = {
            name.lower()
            for name in _overflow_activity_title_preview_names(
                title_source,
                prefix_count=prefix_count or len(acts),
                max_chars=title_max_chars,
            )
            if name
        }
    if len(preview_acts) == 1:
        act_name, _, act_entries = preview_acts[0]
        activity_row = _activity_meaning_row(
            act_name,
            act_entries,
            max_chars=min(188, max(96, max_chars + 20)),
        )
        row = {"title": title}
        row_state = str(activity_row.get("state", "")).strip()
        row_state_badge = _badge_for_row(activity_row)
        if row_state:
            row["state"] = row_state
        if row_state_badge and row_state_badge != row_state:
            row["state_badge"] = row_state_badge
        _, detail, detail_secondary = _meaning_row_display_parts(activity_row)
        if row_state in {"capture mix", "movement data", "notes-derived movements"} and detail_secondary:
            merged_detail = " · ".join(part for part in [detail, detail_secondary] if part)
            if compact_title and re.search(r"\bsets\b|\breps\b", detail_secondary, flags=re.IGNORECASE):
                merged_detail = _strip_session_mix_segment(merged_detail)
            detail = _compact_workout_surface_text(
                merged_detail,
                limit=min(188, max(96, max_chars + 12)),
            )
            detail_secondary = ""
        row_detail = detail or str(activity_row.get("detail", "")).strip() or "activity read below"
        if (
            row_state in DISPLAY_BADGE_STATE_LABELS
            and row_state_badge
            and row_state_badge != row_state
            and row_detail
            and not row_detail.startswith(f"{row_state_badge} · ")
            and not row_detail.startswith(f"{row_state_badge}: ")
        ):
            row_detail = f"{row_state_badge} · {row_detail}"
        if act_name.strip().lower() not in title_visible_names and row_detail:
            if compact_title:
                row_detail = f"{act_name} — {row_detail}"
            else:
                row_detail = f"from {act_name} · {row_detail}"
        if row_state_badge and row_detail:
            row_detail = _strip_redundant_badge_after_activity_prefix(
                row_detail,
                row_state_badge,
            )
        row["detail"] = row_detail
        if detail_secondary:
            row["detail_secondary"] = detail_secondary
        return row

    preview_lines: list[str] = []
    preview_states: list[str] = []
    preview_state_badges: list[str] = []
    for act_name, _, act_entries in preview_acts:
        activity_row = _activity_meaning_row(
            act_name,
            act_entries,
            max_chars=min(188, max(96, max_chars + 20)),
        )
        state = str(activity_row.get("state", "")).strip()
        if state:
            preview_states.append(state)
        state_badge = _badge_for_row(activity_row)
        if state_badge:
            preview_state_badges.append(state_badge)
        line_limit = min(188, max(88, max_chars + 24))
        preview_lines.append(
            _grouped_preview_line(
                act_name,
                activity_row,
                max_chars=line_limit,
                preview_count=len(preview_acts),
            )
        )

    preview_lines = _unique_in_order([line for line in preview_lines if line])
    if preview_lines and len(preview_acts) == 1:
        preview_lines = [
            (
                _strip_overflow_activity_name_prefix(line, act_name)
                if act_name.strip().lower() in title_visible_names
                else line
            )
            for line, (act_name, _, _) in zip(preview_lines, preview_acts)
        ] + preview_lines[len(preview_acts):]
    if len(preview_lines) > 1 and title_matches_preview_order and len(preview_acts) == 1:
        preview_lines = [
            _strip_overflow_activity_name_prefix(line, act_name)
            for line, (act_name, _, _) in zip(preview_lines, preview_acts)
        ]
    remaining_hidden = max(0, len(acts) - len(preview_acts))
    if remaining_hidden and preview_lines and len(preview_lines) == 1:
        preview_lines[0] = _append_detail_suffix(
            preview_lines[0],
            _more_reads_suffix(remaining_hidden),
        )

    row = {"title": title}
    unique_preview_states = _unique_in_order([state for state in preview_states if state])
    unique_preview_state_badges = _unique_in_order(
        [badge for badge in preview_state_badges if badge]
    )
    row_state = unique_preview_states[0] if len(unique_preview_states) == 1 else ""
    if row_state:
        row["state"] = row_state
    if (
        row_state
        and len(unique_preview_state_badges) == 1
        and unique_preview_state_badges[0] != row_state
    ):
        row["state_badge"] = unique_preview_state_badges[0]
    if row_state:
        stripped_preview_lines: list[str] = []
        state_prefix = f"{row_state} · "
        for line in preview_lines:
            cleaned_line = line.strip()
            if cleaned_line.startswith(state_prefix):
                cleaned_line = cleaned_line[len(state_prefix):].strip()
            cleaned_line = _strip_shared_state_from_preview_line(
                cleaned_line,
                row_state,
            )
            stripped_preview_lines.append(cleaned_line)
        preview_lines = _unique_in_order(stripped_preview_lines)
    if compact_title and row_state.startswith("notes-only") and len(preview_lines) > 1:
        preview_lines[-1] = re.sub(
            r"\s*·\s*\+\d+\s+more\s+reads?\b.*$",
            "",
            preview_lines[-1],
            flags=re.IGNORECASE,
        ).strip()
    if preview_lines:
        row["detail"] = preview_lines[0]
    else:
        fallback = _overflow_activity_meaning_detail(
            acts,
            title_acts=title_source,
            max_items=max_items,
            max_chars=max_chars,
        )
        row["detail"] = fallback or "activity reads below"
    if len(preview_lines) > 1:
        row["detail_secondary"] = preview_lines[1]
    if len(preview_lines) > 2:
        row["detail_tertiary"] = preview_lines[2]
    if len(preview_lines) > 3:
        row["detail_quaternary"] = preview_lines[3]
    return row


def _strip_overflow_activity_name_prefix(text: str, act_name: str) -> str:
    cleaned = text.strip()
    if not cleaned or not act_name:
        return cleaned

    prefix = f"{act_name} — "
    if cleaned.startswith(prefix):
        return cleaned[len(prefix):].strip()
    return cleaned


def _activity_review_lines(
    act_name: str,
    act_min: int,
    act_entries: list[dict],
    *,
    category_total: int | None = None,
    compact_detail: bool = False,
) -> list[str]:
    raw_cfs = [e.get("custom_fields", {}) or {} for e in act_entries]
    computed_stats = _compute_structured_stats(raw_cfs)
    exercise_lines = _exercise_rollup_lines(raw_cfs)
    computed_items = _computed_stat_items(computed_stats, total_sessions=len(act_entries))
    aggregate_items = _non_exercise_aggregate_items(act_entries)
    visible_stat_items = computed_items + [
        item for item in aggregate_items if item not in computed_items
    ]
    structured_sessions = sum(1 for cf in raw_cfs if cf)
    notes_only_entries = [entry for entry, cf in zip(act_entries, raw_cfs) if not cf]
    notes_derived_items = _notes_derived_exercise_items(notes_only_entries) if structured_sessions == 0 else []
    notes_derived_lines = _notes_derived_exercise_lines(notes_derived_items)
    note_clues = _extract_note_clues(
        act_entries,
        skip_exercise_bullets=bool(notes_derived_items),
    )
    notes_only_clues = _extract_note_clues(
        notes_only_entries,
        skip_exercise_bullets=bool(notes_derived_items),
    )
    has_explicit_no_stats = any("no stats to report" in e.get("notes", "").lower() for e in act_entries)
    explicit_no_stats_notes_only = sum(
        1 for entry in notes_only_entries if "no stats to report" in entry.get("notes", "").lower()
    )
    notes_only_count = sum(1 for cf in raw_cfs if not cf)
    header = f"- **{act_name}** · {_duration_pair_text(act_min)} · {_count_phrase(len(act_entries), 'log')}"
    if category_total:
        share = round((act_min / category_total) * 100)
        header += f" · {share}% of category"
    header_state_text = _activity_header_state_label(
        total_sessions=len(act_entries),
        structured_sessions=structured_sessions,
        notes_only_count=notes_only_count,
        exercise_lines=exercise_lines,
        visible_stat_items=visible_stat_items,
        notes_derived_lines=notes_derived_lines,
        note_derived_names=[str(item.get("name", "")).strip() for item in notes_derived_items],
        note_clues=note_clues,
        has_explicit_no_stats=has_explicit_no_stats,
    )
    header += " · " + header_state_text
    lines = [header]
    session_pattern_line = ""
    capture_mix_line = ""
    session_split_lines = _activity_session_split_lines(
        act_entries,
        compact_detail=compact_detail,
    )

    if 0 < notes_only_count < len(act_entries):
        capture_mix_line = (
            "  - session mix: "
            f"{_count_phrase(structured_sessions, 'structured session')} · "
            f"{_count_phrase(notes_only_count, 'notes-only session')}"
        )
        if explicit_no_stats_notes_only:
            capture_mix_line += f" · {_count_phrase(explicit_no_stats_notes_only, 'explicit no-stats session')}"

    if len(act_entries) > 1:
        session_pattern = _capture_mix_detail_suffix(structured_sessions, notes_only_count)
        if not session_pattern:
            session_pattern = _session_phrase(len(act_entries))
        session_pattern_line = f"  - session pattern: {session_pattern}"
        if (
            not capture_mix_line
            and compact_detail
            and (exercise_lines or visible_stat_items or notes_derived_lines or note_clues)
        ):
            session_pattern_line = ""
        if not capture_mix_line and (
            notes_only_count == len(act_entries)
            or structured_sessions == len(act_entries)
        ):
            session_pattern_line = ""

    if exercise_lines:
        movement_names = [line.split(" — ", 1)[0] for line in exercise_lines]
        movement_summary = _movement_read_summary(
            exercise_lines,
            structured_sessions=structured_sessions,
        )
        movement_name_line = None
        if (
            not _header_already_carries_names(
                header_state_text,
                movement_names,
                overflow_singular="movement",
            )
            and not _text_carries_name_preview(
                movement_summary,
                movement_names,
                overflow_singular="movement",
            )
        ):
            movement_name_line = _name_list_line(
                label="movement names",
                names=movement_names,
                max_items=4,
                overflow_singular="movement",
            )
        if movement_name_line:
            lines.append(movement_name_line)
        if movement_summary:
            lines.append(f"  - movement read: {movement_summary}")
        lines.extend(
            _chunk_detail_lines(
                label="reported movements",
                continuation_label="more movements",
                items=exercise_lines,
                max_items=2 if compact_detail else 4,
                overflow_label=_overflow_count_label(
                    len(exercise_lines) - (2 if compact_detail else 4),
                    "movement",
                ),
                overflow_preview_titles=[
                    line.split(" — ", 1)[0].strip()
                    for line in exercise_lines[(2 if compact_detail else 4):]
                    if line.strip()
                ],
            )
        )

    if visible_stat_items:
        stat_names = [str(item.get("name", "")).strip() for item in visible_stat_items]
        stat_name_line = None
        header_uses_overflow_stat_preview = "more stat name" in header_state_text
        if header_uses_overflow_stat_preview or not _header_already_carries_names(
            header_state_text,
            stat_names,
            overflow_singular="stat name",
        ):
            stat_name_line = _name_list_line(
                label="stat names",
                names=stat_names,
                max_items=4,
                overflow_singular="stat name",
            )
        if stat_name_line:
            lines.append(stat_name_line)
        lines.append(f"  - stats read: {_stat_read_line(visible_stat_items, prefer_detail=True)}")

    if notes_derived_lines:
        note_derived_names = [str(item.get("name", "")).strip() for item in notes_derived_items]
        notes_derived_summary = _notes_derived_movement_read_summary(
            notes_derived_items,
            notes_only_count=len(notes_only_entries),
        )
        notes_movement_name_line = None
        if (
            not _header_already_carries_names(
                header_state_text,
                note_derived_names,
                overflow_singular="movement",
            )
            and not _text_carries_name_preview(
                notes_derived_summary,
                note_derived_names,
                overflow_singular="movement",
            )
        ):
            notes_movement_name_line = _name_list_line(
                label="movement names from notes",
                names=note_derived_names,
                max_items=4,
                overflow_singular="movement",
            )
        if notes_movement_name_line:
            lines.append(notes_movement_name_line)
        if notes_derived_summary:
            lines.append(f"  - notes-derived movement read: {notes_derived_summary}")
        lines.extend(
            _chunk_detail_lines(
                label="reported movements from notes",
                continuation_label="more note-derived movements",
                items=notes_derived_lines,
                max_items=4 if compact_detail else 6,
                overflow_label=_overflow_count_label(
                    len(notes_derived_lines) - (4 if compact_detail else 6),
                    "note-derived movement",
                ),
                overflow_preview_titles=[
                    line.split(" — ", 1)[0].strip()
                    for line in notes_derived_lines[(4 if compact_detail else 6):]
                    if line.strip()
                ],
            )
        )

    if not exercise_lines and not visible_stat_items and not notes_derived_lines:
        review_state = _activity_review_state(
            total_sessions=len(act_entries),
            structured_sessions=structured_sessions,
            exercise_lines=exercise_lines,
            visible_stat_lines=[item["detail"] for item in visible_stat_items],
            note_clues=note_clues,
            has_explicit_no_stats=has_explicit_no_stats,
        )
        if note_clues:
            note_label = "notes-only"
            if has_explicit_no_stats:
                note_label = "notes-only + no-stats"
            if len(act_entries) > 1:
                note_label += f" across {len(act_entries)} sessions"
            lines.append(f"  - {note_label}: {_visible_note_clue_read(note_clues)}")
        elif review_state:
            if has_explicit_no_stats:
                lines.append(f"  - no-stats: {review_state}")
            else:
                lines.append(f"  - review state: {review_state}")
        if has_explicit_no_stats and note_clues and len(act_entries) == 1:
            lines.append("  - No stats to report")
    elif notes_only_clues and notes_only_count:
        notes_label = "notes read"
        if structured_sessions and notes_only_count < len(act_entries):
            notes_label = "notes read from notes-only sessions"
        lines.append(f"  - {notes_label}: {_visible_note_clue_read(notes_only_clues)}")
    if session_split_lines:
        insert_after = 1
        for prefix in (
            "  - movement read:",
            "  - stats read:",
            "  - notes-derived movement read:",
        ):
            for index, line in enumerate(lines):
                if line.startswith(prefix):
                    insert_after = index + 1
        lines[insert_after:insert_after] = session_split_lines
    if (
        explicit_no_stats_notes_only
        and structured_sessions
        and not any("No stats to report" in line for line in lines)
    ):
        lines.append(
            "  - No stats to report in "
            f"{_count_phrase(explicit_no_stats_notes_only, 'notes-only session')}"
        )
    if capture_mix_line:
        lines.append(capture_mix_line)
    if session_pattern_line:
        lines.append(session_pattern_line)

    return lines


def _category_activity_state_lines(
    acts: list[tuple[str, int, list[dict]]],
    *,
    max_items: int = TOP_PROOF_VISIBLE_ACTIVITY_COUNT,
) -> list[str]:
    if not acts:
        return ["- activity read: no activity read yet"]

    lines = ["- activity read:"]
    for row in _category_time_scan_meaning_rows(
        acts,
        max_items=max_items,
        compact_overflow_title=True,
    ):
        title = str(row.get("title", "")).strip()
        state, detail, detail_secondary = _signature_surface_meaning_parts(row)
        state_badge = _meaning_row_state_badge(row)
        raw_detail = str(row.get("detail", "")).strip()
        display_state = state_badge if state in DISPLAY_BADGE_STATE_LABELS and state_badge else state
        if (
            title.startswith("+")
            and display_state.startswith("notes-only")
            and re.search(r"\s[—-]\s", detail)
        ):
            display_state = ""
        if (
            re.match(
                r"^\d+\s+(?:stat name|movement)s?(?:\s+\+\s+notes)?$",
                display_state,
                flags=re.IGNORECASE,
            )
            and (
                re.search(r":", detail)
                or re.search(r"\s[—-]\s.*:", detail)
                or re.search(r"\+\d+\s+more\b", detail, flags=re.IGNORECASE)
            )
        ):
            display_state = ""
        if (
            state == "stats reported"
            and detail_secondary
            and re.fullmatch(r"\d+/\d+\s+sessions?", detail_secondary)
        ):
            detail = f"{detail} ({detail_secondary})" if detail else f"({detail_secondary})"
            detail_secondary = ""
        if state == "no-stats" and "No stats to report" in raw_detail and "No stats to report" not in detail:
            detail = _append_detail_suffix(detail, "No stats to report")
        meaning_parts = [part for part in (title, display_state, detail) if part]
        meaning_line = " — ".join(meaning_parts[:2])
        if len(meaning_parts) > 2:
            meaning_line += f" · {meaning_parts[2]}"
        if detail_secondary:
            meaning_line += f" · {detail_secondary}"
        lines.append(f"  - {meaning_line}")
    return lines


def _category_glance_lines(
    cat: str,
    total: int,
    total_min: int,
    acts: list[tuple[str, int, list[dict]]],
    *,
    visible_count: int = TOP_PROOF_VISIBLE_ACTIVITY_COUNT,
) -> list[str]:
    label = cat.replace("-", " ").title()
    share = round((total / total_min) * 100) if total_min else 0
    activity_count = len(acts)
    log_count = sum(len(act_entries) for _, _, act_entries in acts)
    lines = [
        f"- **{label}** — {_duration_summary_text(total, share)} across {_count_phrase(activity_count, 'activity', 'activities')} / {_count_phrase(log_count, 'log')}"
    ]
    lines.extend(
        f"  {line}" for line in _category_time_stack_lines(acts, label="time stack", visible_count=3)
    )
    activity_rows = _category_time_scan_meaning_rows(
        acts,
        max_items=visible_count,
        compact_overflow_title=True,
    )
    if activity_rows:
        lines.append("  - activity read:")
        for row in activity_rows:
            title = str(row.get("title", "")).strip()
            state = str(row.get("state", "")).strip()
            state_badge = _meaning_row_state_badge(row)
            raw_detail = str(row.get("detail", "")).strip()
            _, detail, detail_secondary = _meaning_row_display_parts(row)
            display_state = state_badge if state in DISPLAY_BADGE_STATE_LABELS and state_badge else state
            if (
                state == "stats reported"
                and detail_secondary
                and re.fullmatch(r"\d+/\d+\s+sessions?", detail_secondary)
            ):
                detail = f"{detail} ({detail_secondary})" if detail else f"({detail_secondary})"
                detail_secondary = ""
            if state == "no-stats" and "No stats to report" in raw_detail and "No stats to report" not in detail:
                detail = _append_detail_suffix(detail, "No stats to report")
            meaning_parts = [part for part in (title, display_state, detail) if part]
            meaning_line = " — ".join(meaning_parts[:2])
            if len(meaning_parts) > 2:
                meaning_line += f" · {meaning_parts[2]}"
            if detail_secondary:
                meaning_line += f" · {detail_secondary}"
            lines.append(f"    - {meaning_line}")
    focus_row = _category_focus_glance_row(acts, total=total)
    if focus_row:
        lines.append(f"  - {focus_row}")
    return lines


def _category_totals_strip_line(
    cats: list[tuple[str, int, list[tuple[str, int, list[dict]]]]],
    total_min: int,
    *,
    max_items: int = 4,
) -> str:
    if not cats or total_min <= 0:
        return "- category totals: no time logged yet"

    visible_parts = []
    for cat, total, acts in cats[:max_items]:
        label = cat.replace("-", " ").title()
        share = round((total / total_min) * 100) if total_min else 0
        stack_preview = _summary_category_strip_time_preview(acts, total=total)
        part = f"{label} {_duration_summary_text(total, share)}"
        if stack_preview:
            part += f" | {stack_preview}"
        visible_parts.append(part)

    remaining = len(cats) - max_items
    if remaining > 0:
        visible_parts.append(f"+{remaining} more categories")

    return "- category totals: " + " · ".join(visible_parts)


def _category_totals_glance_lines(
    cats: list[tuple[str, int, list[tuple[str, int, list[dict]]]]],
    total_min: int,
    *,
    max_items: int = 4,
) -> list[str]:
    if not cats or total_min <= 0:
        return ["- category totals: no time logged yet"]

    lines: list[str] = []
    for cat, total, acts in cats[:max_items]:
        label = cat.replace("-", " ").title()
        share = round((total / total_min) * 100) if total_min else 0
        stack_preview = _summary_category_strip_time_preview(acts, total=total)
        line = f"- category total: **{label}** {_duration_summary_text(total, share)}"
        if stack_preview:
            line += f" | {stack_preview}"
        lines.append(line)

    remaining = len(cats) - max_items
    if remaining > 0:
        lines.append(f"- category totals overflow: +{remaining} more categories")

    return lines


def _category_glance_surface_html(
    cats: list[tuple[str, int, list[tuple[str, int, list[dict]]]]],
    total_min: int,
    *,
    days: int,
    visible_count: int = TOP_PROOF_VISIBLE_ACTIVITY_COUNT,
) -> str:
    if not cats or total_min <= 0:
        return "\n".join(
            [
                '<div class="har-glance-surface">',
                '  <div class="har-glance-surface-head">',
                '    <span class="har-glance-surface-label">Category Totals At A Glance</span>',
                '    <span class="har-glance-surface-copy">No time logged yet.</span>',
                "  </div>",
                "</div>",
            ]
        )

    scan_copy = _range_surface_scan_copy(days)
    time_overflow_preview_items = 2 if days <= 7 else 3
    lines = [
        '<div class="har-glance-surface">',
        '  <div class="har-glance-surface-head">',
        '    <span class="har-glance-surface-label">Category Totals At A Glance</span>',
        f'    <span class="har-glance-surface-copy">{html.escape(scan_copy["glance_copy"])}</span>',
        "  </div>",
        '  <div class="har-glance-surface-cards">',
    ]

    for cat, total, acts in cats:
        label = cat.replace("-", " ").title()
        share = round((total / total_min) * 100) if total_min else 0
        activity_count = len(acts)
        log_count = sum(len(act_entries) for _, _, act_entries in acts)
        density = _range_surface_density(days)
        meta_text = " · ".join(
            _compact_category_meta_items(
                activity_count,
                log_count,
                visible_count=visible_count,
            )
        )
        lines.extend(
            [
                '    <div class="har-glance-card">',
                '      <div class="har-glance-card-head">',
                f'        <span class="har-glance-card-name">{html.escape(label)}</span>',
                '        <span class="har-glance-card-total">',
                f'          <span class="har-glance-card-total-primary">{html.escape(_human_duration(total))}</span>',
                f'          <span class="har-glance-card-total-secondary">{total} min · {share}% of range</span>',
                "        </span>",
                "      </div>",
                f'      <div class="har-glance-card-meta">{html.escape(meta_text)}</div>',
                '      <div class="har-glance-card-lanes">',
            ]
        )

        time_rows = _category_time_scan_rows(
            acts,
            visible_count=visible_count,
            overflow_title_max_chars=density["glance_time_overflow_title_chars"],
            compact_overflow_title=True,
            overflow_preview_items=time_overflow_preview_items,
        )
        if time_rows:
            lines.extend(
                [
                    '        <div class="har-glance-lane">',
                    '          <span class="har-glance-lane-label">Activity Time</span>',
                    '          <div class="har-glance-lane-rows">',
                ]
            )
            for row in time_rows:
                title = str(row.get("title", "")).strip()
                detail = str(row.get("detail", "")).strip()
                duration = str(row.get("duration", "")).strip()
                meta = str(row.get("meta", "")).strip()
                extra_detail_lines = _row_extra_detail_lines(row)
                row_class = "har-glance-row"
                if title.startswith("+"):
                    row_class += " is-more"
                lines.extend(
                    [
                        f'            <div class="{row_class}">',
                        f'              <span class="har-glance-row-title">{html.escape(title)}</span>',
                        '              <span class="har-glance-row-read is-time">',
                    ]
                )
                if duration:
                    lines.append(
                        f'                <span class="har-glance-row-detail-primary">{html.escape(duration)}</span>'
                    )
                lines.extend(
                    [
                        f'                <span class="har-glance-row-detail">{html.escape(meta or detail)}</span>',
                    ]
                )
                for extra_detail in extra_detail_lines:
                    lines.append(
                        f'                <span class="har-glance-row-detail-secondary">{html.escape(extra_detail)}</span>'
                    )
                lines.extend(
                    [
                        "              </span>",
                        "            </div>",
                    ]
                )
            lines.extend(
                [
                    "          </div>",
                    "        </div>",
                ]
            )

        meaning_rows = _category_time_scan_meaning_rows(
            acts,
            max_items=visible_count,
            overflow_preview_items=2,
            compact_overflow_title=True,
        )
        if meaning_rows:
            lines.extend(
                [
                    '        <div class="har-glance-lane is-meaning">',
                    '          <span class="har-glance-lane-label">Activity Read</span>',
                    '          <div class="har-glance-lane-rows">',
                ]
            )
            for row in meaning_rows:
                title = str(row.get("title", "")).strip()
                state, detail, detail_secondary = _signature_surface_meaning_parts(row)
                state_badge = _meaning_row_state_badge(row)
                row_class = "har-glance-row"
                if title.startswith("+"):
                    row_class += " is-more"
                lines.extend(
                    [
                        f'            <div class="{row_class}">',
                        f'              <span class="har-glance-row-title">{html.escape(title)}</span>',
                        '              <div class="har-glance-row-meaning">',
                    ]
                )
                if state_badge:
                    lines.append(
                        f'                <span class="har-glance-row-state">{html.escape(state_badge)}</span>'
                    )
                lines.append(
                    f'                <span class="har-glance-row-detail">{html.escape(detail)}</span>'
                )
                if detail_secondary:
                    lines.append(
                        f'                <span class="har-glance-row-detail-secondary">{html.escape(detail_secondary)}</span>'
                    )
                lines.extend(
                    [
                        "              </div>",
                        "            </div>",
                    ]
                )
            lines.extend(
                [
                    "          </div>",
                    "        </div>",
                ]
            )

        lines.extend(
            [
                "      </div>",
                "    </div>",
            ]
        )

    lines.extend(
        [
            "  </div>",
            "</div>",
        ]
    )
    return "\n".join(lines)


def _summary_panel_html(
    *,
    title: str,
    kicker: str,
    lines: list[str],
    emphasized: bool = False,
    extra_class: str = "",
    raw_body_html: str = "",
) -> str:
    card_class = "har-summary-panel is-emphasis" if emphasized else "har-summary-panel"
    if extra_class:
        card_class += f" {extra_class}"
    if raw_body_html:
        body = raw_body_html.splitlines()
    else:
        body = ["  <div class=\"har-summary-panel-body\">"]
        for line in lines:
            if not line:
                continue
            body.append(f"    <div class=\"har-summary-line\">{html.escape(_strip_markdown_emphasis(line))}</div>")
        body.append("  </div>")
    return "\n".join(
        [
            f"<div class=\"{card_class}\">",
            f"  <div class=\"har-summary-kicker\">{html.escape(kicker)}</div>",
            f"  <div class=\"har-summary-title\">{html.escape(title)}</div>",
            *body,
            "</div>",
        ]
    )


def _range_surface_role(days: int) -> dict[str, str]:
    if days <= 7:
        return {
            "class_name": "is-signature",
            "eyebrow": "Signature Weekly Review",
            "title": "Main ritual",
            "copy": "Category totals first, activity time second, activity stats and notes third.",
        }
    if days >= 36500:
        return {
            "class_name": "is-reference",
            "eyebrow": "All-Time Reference",
            "title": "Calibration lens",
            "copy": "Same contract against the full baseline. Use only to calibrate the weekly read.",
        }
    return {
        "class_name": "is-pattern",
        "eyebrow": "30-Day Pattern Extension",
        "title": "Pattern extension",
        "copy": "Same contract with denser patterns. Open after the weekly lane.",
    }


def _range_surface_review_sequence(days: int) -> list[str]:
    if days <= 7:
        return [
            "1. Category totals",
            "2. Stacked activity time",
            "3. Activity stats",
            "4. Deep review",
        ]
    if days >= 36500:
        return [
            "1. Baseline totals",
            "2. Top activity time",
            "3. Activity stats",
            "4. Deep review",
        ]
    return [
        "1. Category totals",
        "2. Top activity time",
        "3. Activity stats",
        "4. Deep review",
    ]


def _range_surface_scan_copy(days: int) -> dict[str, str]:
    if days <= 7:
        return {
            "table_copy": "Three activity-time rows per category first. Use activity reads only to explain the split.",
            "glance_copy": "Category total first, then time rows and activity stats/notes.",
            "chips_copy": "Activity Stats Review keeps fuller stat labels, workout rollups, and honest fallback states.",
        }
    if days >= 36500:
        return {
            "table_copy": "Totals first, then two activity-time rows and one compact stats/notes read.",
            "glance_copy": "Total first, then two time rows plus a compact stats/notes read.",
            "chips_copy": "Calibration Activity Stats Review keeps fuller activity-level stat, movement, and fallback reads.",
        }
    return {
        "table_copy": "Totals first, then two activity-time rows and one compact stats/notes read.",
        "glance_copy": "Total first, then two time rows plus a compact stats/notes read.",
        "chips_copy": "Pattern Activity Stats Review keeps fuller activity-level stat, movement, and fallback reads.",
    }


def _range_surface_density(days: int) -> dict[str, int]:
    if days <= 7:
        return {
            "summary_panel_meaning_items": 3,
            "summary_strip_time_items": 2,
            "table_visible_items": 3,
            "chip_visible_items": 3,
            "summary_panel_time_overflow_chars": 68,
            "summary_panel_time_overflow_title_chars": 84,
            "summary_panel_meaning_chars": 148,
            "summary_panel_overflow_meaning_chars": 164,
            "summary_strip_meaning_items": 2,
            "summary_strip_time_overflow_chars": 68,
            "summary_strip_time_overflow_title_chars": 84,
            "summary_strip_meaning_chars": 148,
            "summary_strip_overflow_meaning_chars": 164,
            "table_time_overflow_chars": 52,
            "table_time_overflow_title_chars": 68,
            "table_meaning_chars": 122,
            "table_overflow_meaning_chars": 138,
            "chip_meaning_chars": 164,
            "chip_overflow_meaning_chars": 182,
            "glance_time_overflow_title_chars": 68,
        }
    if days >= 36500:
        return {
            "summary_panel_meaning_items": 2,
            "summary_strip_time_items": 1,
            "table_visible_items": 2,
            "chip_visible_items": 2,
            "summary_panel_time_overflow_chars": 58,
            "summary_panel_time_overflow_title_chars": 72,
            "summary_panel_meaning_chars": 104,
            "summary_panel_overflow_meaning_chars": 126,
            "summary_strip_meaning_items": 1,
            "summary_strip_time_overflow_chars": 56,
            "summary_strip_time_overflow_title_chars": 72,
            "summary_strip_meaning_chars": 96,
            "summary_strip_overflow_meaning_chars": 112,
            "table_time_overflow_chars": 54,
            "table_time_overflow_title_chars": 68,
            "table_meaning_chars": 92,
            "table_overflow_meaning_chars": 112,
            "chip_meaning_chars": 118,
            "chip_overflow_meaning_chars": 138,
            "glance_time_overflow_title_chars": 68,
        }
    return {
        "summary_panel_meaning_items": 2,
        "summary_strip_time_items": 1,
        "table_visible_items": 2,
        "chip_visible_items": 2,
        "summary_panel_time_overflow_chars": 58,
        "summary_panel_time_overflow_title_chars": 72,
        "summary_panel_meaning_chars": 106,
        "summary_panel_overflow_meaning_chars": 128,
        "summary_strip_meaning_items": 1,
        "summary_strip_time_overflow_chars": 56,
        "summary_strip_time_overflow_title_chars": 72,
        "summary_strip_meaning_chars": 98,
        "summary_strip_overflow_meaning_chars": 116,
        "table_time_overflow_chars": 54,
        "table_time_overflow_title_chars": 68,
        "table_meaning_chars": 94,
        "table_overflow_meaning_chars": 114,
        "chip_meaning_chars": 120,
        "chip_overflow_meaning_chars": 140,
        "glance_time_overflow_title_chars": 68,
    }


def _range_role_banner_html(days: int) -> str:
    role = _range_surface_role(days)
    return "\n".join(
        [
            f'<div class="har-main-review-role {role["class_name"]}">',
            f'  <div class="har-main-review-role-kicker">{html.escape(role["eyebrow"])}</div>',
            f'  <div class="har-main-review-role-title">{html.escape(role["title"])}</div>',
            f'  <div class="har-main-review-role-copy">{html.escape(role["copy"])}</div>',
            "</div>",
        ]
    )


def _summary_category_total_lines(
    cats: list[tuple[str, int, list[tuple[str, int, list[dict]]]]],
    *,
    total_min: int,
    max_items: int = 3,
) -> list[str]:
    if not cats or total_min <= 0:
        return ["no category time logged yet"]

    lines: list[str] = []
    for cat, total, acts in cats[:max_items]:
        share = round((total / total_min) * 100) if total_min else 0
        activity_count = len(acts)
        log_count = sum(len(act_entries) for _, _, act_entries in acts)
        meta_text = " · ".join(
            _compact_category_meta_items(
                activity_count,
                log_count,
                visible_count=1,
            )
        )
        label = cat.replace("-", " ").title()
        preview = _summary_category_strip_time_preview(
            acts,
            total=total,
            max_items=1,
            max_chars=72,
        )
        line = (
            f"{label}: {_human_duration(total)} ({total} min, {share}% of range) · "
            f"{meta_text}"
        )
        if preview:
            line += f" · {preview}"
        lines.append(line)

    remaining = len(cats) - max_items
    if remaining > 0:
        lines.append(f"+{remaining} more categories below")
    return lines


def _summary_category_totals_panel_body_html(
    cats: list[tuple[str, int, list[tuple[str, int, list[dict]]]]],
    *,
    total_min: int,
    days: int,
    max_categories: int = 3,
    preview_items: int = 1,
) -> str:
    if not cats or total_min <= 0:
        return "\n".join(
            [
                '  <div class="har-summary-panel-body is-category-totals-grid is-empty">',
                '    <div class="har-summary-line">no category time logged yet</div>',
                "  </div>",
            ]
        )

    density = _range_surface_density(days)
    time_overflow_preview_items = 2 if days <= 7 else 3
    range_class = (
        "is-signature" if days <= 7 else "is-reference" if days >= 36500 else "is-pattern"
    )
    lines = [f'  <div class="har-summary-panel-body is-category-totals-grid {range_class}">']
    for cat, total, acts in cats[:max_categories]:
        label = cat.replace("-", " ").title()
        share = round((total / total_min) * 100) if total_min else 0
        activity_count = len(acts)
        log_count = sum(len(act_entries) for _, _, act_entries in acts)
        meta_items = _compact_category_meta_items(
            activity_count,
            log_count,
            visible_count=preview_items,
        )
        stack_preview = _summary_category_strip_rows_html(
            acts,
            total=total,
            max_items=preview_items,
            overflow_max_chars=density["summary_panel_time_overflow_chars"],
            overflow_title_max_chars=density["summary_panel_time_overflow_title_chars"],
            include_overflow=True,
            overflow_preview_items=time_overflow_preview_items,
        )
        meaning_preview = _summary_category_strip_meaning_rows_html(
            acts,
            max_items=density["summary_panel_meaning_items"],
            max_chars=density["summary_panel_meaning_chars"],
            overflow_max_chars=density["summary_panel_overflow_meaning_chars"],
            include_overflow=True,
            overflow_preview_items=2,
        )
        meta_html = _meta_pills_html(
            "har-summary-category-total-meta",
            meta_items,
        )
        lines.extend(
            [
                '    <div class="har-summary-category-total-row">',
                '      <div class="har-summary-category-total-head">',
                f'        <span class="har-summary-category-total-name">{html.escape(label)}</span>',
                '        <span class="har-summary-category-total-value">',
                f'          <span class="har-summary-category-total-primary">{html.escape(_human_duration(total))}</span>',
                f'          <span class="har-summary-category-total-secondary">{total} min · {share}% of range</span>',
                "        </span>",
                "      </div>",
                f"      {meta_html}",
            ]
        )
        if stack_preview:
            lines.extend(f"      {line}" for line in stack_preview.splitlines())
        if meaning_preview:
            lines.extend(f"      {line}" for line in meaning_preview.splitlines())
        lines.append("    </div>")

    remaining = len(cats) - max_categories
    if remaining > 0:
        lines.extend(
            [
                '    <div class="har-summary-category-total-row is-more">',
                f'      <div class="har-summary-line">+{remaining} more categories below</div>',
                "    </div>",
            ]
        )

    lines.append("  </div>")
    return "\n".join(lines)


def _meta_pills_html(base_class: str, segments: list[str]) -> str:
    cleaned = [re.sub(r"\s+", " ", str(segment)).strip() for segment in segments if str(segment).strip()]
    if not cleaned:
        return ""
    items = "".join(
        f'<span class="{base_class}-item">{html.escape(segment)}</span>'
        for segment in cleaned
    )
    return f'<span class="{base_class}">{items}</span>'


def _review_sequence_html(days: int) -> str:
    lines = [
        '<div class="har-review-sequence" aria-label="Main review order">',
        '  <span class="har-review-sequence-label">Review Order</span>',
        '  <div class="har-review-sequence-pills">',
    ]
    for pill in _range_surface_review_sequence(days):
        lines.append(
            f'    <span class="har-review-sequence-pill">{html.escape(pill)}</span>'
        )
    lines.extend(
        [
            "  </div>",
            "</div>",
        ]
    )
    return "\n".join(lines)


def _summary_category_strip_html(
    cats: list[tuple[str, int, list[tuple[str, int, list[dict]]]]],
    total_min: int,
    *,
    days: int,
) -> str:
    if not cats or total_min <= 0:
        return '<div class="har-summary-category-strip">category totals: no time logged yet</div>'

    density = _range_surface_density(days)
    parts = ['<div class="har-summary-category-strip">', '  <span class="har-summary-category-strip-label">category totals</span>']
    for cat, total, acts in cats[:4]:
        label = cat.replace("-", " ").title()
        share = round((total / total_min) * 100) if total_min else 0
        activity_count = len(acts)
        log_count = sum(len(act_entries) for _, _, act_entries in acts)
        meta_items = _compact_category_meta_items(
            activity_count,
            log_count,
            visible_count=density["summary_strip_time_items"],
        )
        time_preview_html = _summary_category_strip_time_preview_html(
            acts,
            total=total,
            max_items=density["summary_strip_time_items"],
            max_chars=density["summary_strip_time_overflow_chars"] + 56,
            overflow_title_max_chars=density["summary_strip_time_overflow_title_chars"],
        )
        meaning_preview_html = _summary_category_strip_meaning_preview_html(
            acts,
            max_items=density["summary_strip_meaning_items"],
            max_chars=density["summary_strip_meaning_chars"],
            overflow_max_chars=density["summary_strip_overflow_meaning_chars"],
        )
        parts.append(
            "  <span class=\"har-summary-category-pill\">"
            f"<span class=\"har-summary-category-pill-name\">{html.escape(label)}</span>"
            "<span class=\"har-summary-category-pill-total\">"
            f"<span class=\"har-summary-category-pill-total-primary\">{html.escape(_human_duration(total))}</span>"
            f"<span class=\"har-summary-category-pill-total-secondary\">{total} min · {share}%</span>"
            f"<span class=\"har-summary-category-pill-meta\">{html.escape(' · '.join(meta_items))}</span>"
            f"{time_preview_html}"
            f"{meaning_preview_html}"
            "</span>"
            "</span>"
        )
    if len(cats) > 4:
        parts.append(
            "  <span class=\"har-summary-category-pill is-more\">"
            f"+{len(cats) - 4} more"
            "</span>"
        )
    parts.append("</div>")
    return "\n".join(parts)


def _summary_category_strip_rows_html(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
    max_items: int = 2,
    overflow_max_chars: int = 68,
    overflow_title_max_chars: int | None = None,
    include_overflow: bool = True,
    overflow_preview_items: int = 2,
) -> str:
    rows = _category_time_scan_rows(
        acts,
        visible_count=max_items,
        overflow_max_chars=overflow_max_chars,
        overflow_title_max_chars=overflow_title_max_chars,
        compact_overflow_title=True,
        overflow_preview_items=overflow_preview_items,
    )
    if not include_overflow:
        rows = [row for row in rows if not str(row.get("title", "")).strip().startswith("+")]
    if not rows or total <= 0:
        return ""

    lines = ['<span class="har-summary-category-pill-stack">']
    lines.append('  <span class="har-summary-category-pill-stack-label">Activity Time</span>')
    lines.append('  <span class="har-summary-category-pill-stack-rows">')
    for row in rows:
        title = str(row.get("title", "")).strip()
        detail = str(row.get("detail", "")).strip()
        duration = str(row.get("duration", "")).strip()
        meta = str(row.get("meta", "")).strip()
        extra_detail_lines = _row_extra_detail_lines(row)
        row_class = "har-summary-category-pill-stack-row"
        if title.startswith("+"):
            row_class += " is-more"
        lines.extend(
            [
                f'    <span class="{row_class}">',
                '      <span class="har-summary-category-pill-stack-row-head">',
                f'        <span class="har-summary-category-pill-stack-title">{html.escape(title)}</span>',
                "      </span>",
                '      <span class="har-summary-category-pill-stack-read">',
            ]
        )
        if duration:
            lines.append(
                f'        <span class="har-summary-category-pill-stack-detail-primary">{html.escape(duration)}</span>'
            )
        lines.append(
            f'        <span class="har-summary-category-pill-stack-detail">{html.escape(meta or detail)}</span>'
        )
        for extra_detail in extra_detail_lines:
            lines.append(
                f'        <span class="har-summary-category-pill-stack-detail-secondary">{html.escape(extra_detail)}</span>'
            )
        lines.extend(
            [
                "      </span>",
                "    </span>",
            ]
        )
    lines.append("  </span>")
    lines.append("</span>")
    return "\n".join(lines)


def _summary_category_strip_meaning_rows_html(
    acts: list[tuple[str, int, list[dict]]],
    *,
    max_items: int = 1,
    max_chars: int = 148,
    overflow_max_chars: int = 164,
    include_overflow: bool = True,
    overflow_preview_items: int = 1,
) -> str:
    rows = _category_time_scan_meaning_rows(
        acts,
        max_items=max_items,
        max_chars=max_chars,
        overflow_max_chars=overflow_max_chars,
        overflow_preview_items=max(1, overflow_preview_items),
        overflow_title_max_chars=max(68, min(84, overflow_max_chars)),
        compact_overflow_title=True,
    )
    if not include_overflow:
        rows = [row for row in rows if not str(row.get("title", "")).strip().startswith("+")]
    if not rows:
        return ""

    lines = ['<span class="har-summary-category-pill-stack is-meaning">']
    lines.append('  <span class="har-summary-category-pill-stack-label">Activity Read</span>')
    lines.append('  <span class="har-summary-category-pill-stack-rows">')
    for row in rows:
        title = str(row.get("title", "")).strip()
        state, detail, detail_secondary = _signature_surface_meaning_parts(row)
        state_badge = _meaning_row_state_badge(row)
        row_class = "har-summary-category-pill-stack-row is-meaning"
        if title.startswith("+"):
            row_class += " is-more"
        lines.extend(
            [
                f'    <span class="{row_class}">',
                '      <span class="har-summary-category-pill-stack-row-head">',
                f'        <span class="har-summary-category-pill-stack-title">{html.escape(title)}</span>',
            ]
        )
        if state_badge:
            lines.append(
                f'        <span class="har-summary-category-pill-stack-state">{html.escape(state_badge)}</span>'
            )
        lines.extend(
            [
                "      </span>",
                '      <span class="har-summary-category-pill-stack-read">',
            ]
        )
        if detail:
            lines.append(
                f'        <span class="har-summary-category-pill-stack-detail">{html.escape(detail)}</span>'
            )
        if detail_secondary:
            lines.append(
                f'        <span class="har-summary-category-pill-stack-detail-secondary">{html.escape(detail_secondary)}</span>'
            )
        lines.extend(
            [
                "      </span>",
                "    </span>",
            ]
        )
    lines.append("  </span>")
    lines.append("</span>")
    return "\n".join(lines)


def _summary_category_focus_rows_html(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
) -> str:
    rows = _category_chip_focus_rows(acts, total=total)
    if not rows:
        return ""

    lines = ['<span class="har-summary-category-pill-stack is-focus">']
    lines.append('  <span class="har-summary-category-pill-stack-label">Focus</span>')
    lines.append('  <span class="har-summary-category-pill-stack-rows">')
    for row in rows:
        title = str(row.get("title", "")).strip()
        detail = str(row.get("detail", "")).strip()
        lines.extend(
            [
                '    <span class="har-summary-category-pill-stack-row is-focus">',
                '      <span class="har-summary-category-pill-stack-row-head">',
                f'        <span class="har-summary-category-pill-stack-title">{html.escape(title)}</span>',
                "      </span>",
                '      <span class="har-summary-category-pill-stack-read">',
            ]
        )
        if detail:
            lines.append(
                f'        <span class="har-summary-category-pill-stack-detail">{html.escape(detail)}</span>'
            )
        lines.extend(
            [
                "      </span>",
                "    </span>",
            ]
        )
    lines.append("  </span>")
    lines.append("</span>")
    return "\n".join(lines)


def _summary_category_strip_time_preview_html(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
    max_items: int = 1,
    max_chars: int = 108,
    overflow_title_max_chars: int | None = None,
) -> str:
    if not acts or total <= 0:
        return ""

    rows = _category_time_scan_rows(
        acts,
        visible_count=max_items,
        overflow_max_chars=max_chars,
        overflow_title_max_chars=overflow_title_max_chars,
        compact_overflow_title=True,
    )
    if not rows:
        return ""
    return _summary_category_strip_preview_rows_html(
        "Activity Time",
        rows,
        extra_class="is-time",
    )


def _summary_category_strip_time_preview(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
    max_items: int = 2,
    max_chars: int = 108,
) -> str:
    if not acts or total <= 0:
        return ""

    def _preview_row(act_name: str, act_min: int) -> str:
        share = round((act_min / total) * 100) if total else 0
        return f"{_human_activity_duration(act_min)} {act_name} · {share}%"

    if len(acts) == 1:
        act_name, act_min, _ = acts[0]
        return f"Activity Time: {_preview_row(act_name, act_min)}"

    visible_rows = [_preview_row(act_name, act_min) for act_name, act_min, _ in acts[:max_items]]
    remainder = max(0, len(acts) - len(visible_rows))
    text = " / ".join(visible_rows)
    if remainder:
        text += f" / +{remainder} more"
    if len(text) <= max_chars:
        return f"Activity Time: {text}"

    lead_name, lead_min, _ = acts[0]
    lead_preview = _preview_row(_truncate_text(lead_name, limit=28), lead_min)
    if remainder:
        return f"Activity Time: {lead_preview} / +{remainder} more"
    return f"Activity Time: {lead_preview}"


def _summary_category_strip_meaning_preview_html(
    acts: list[tuple[str, int, list[dict]]],
    *,
    max_items: int,
    max_chars: int,
    overflow_max_chars: int,
) -> str:
    rows = _category_time_scan_meaning_rows(
        acts,
        max_items=max_items,
        max_chars=max_chars,
        overflow_max_chars=overflow_max_chars,
        overflow_preview_items=2,
        overflow_title_max_chars=max(68, min(84, overflow_max_chars)),
        compact_overflow_title=True,
    )
    if not rows:
        return ""

    preview_rows: list[dict[str, str]] = []
    for row in rows:
        title = str(row.get("title", "")).strip()
        state, primary, secondary = _meaning_row_display_parts(row)
        state_badge = _meaning_row_state_badge(row)
        preview_rows.append(
            {
                "title": title,
                "state": state_badge or state,
                "detail": primary,
                "detail_secondary": secondary,
            }
        )

    return _summary_category_strip_preview_rows_html(
        "Activity Read",
        preview_rows,
        extra_class="is-meaning",
    )


def _summary_category_strip_meaning_preview(
    acts: list[tuple[str, int, list[dict]]],
    *,
    max_items: int,
    max_chars: int,
    overflow_max_chars: int,
) -> str:
    rows = _category_time_scan_meaning_rows(
        acts,
        max_items=max_items,
        max_chars=max_chars,
        overflow_max_chars=overflow_max_chars,
        overflow_preview_items=2,
        overflow_title_max_chars=max(68, min(84, overflow_max_chars)),
        compact_overflow_title=True,
    )
    if not rows:
        return ""

    parts: list[str] = []
    for row in rows:
        title = str(row.get("title", "")).strip()
        state, primary, secondary = _meaning_row_display_parts(row)
        part = title
        if state:
            part += f" — {state}"
        if primary:
            part += f": {primary}"
        if secondary:
            part += f" · {secondary}"
        parts.append(part)

    summary = _compact_preview_parts(parts, limit=max_chars + 36)
    if not summary:
        return ""
    return _truncate_text(summary, limit=max_chars + 36)


def _summary_category_strip_preview_html(
    label: str,
    detail: str,
    *,
    extra_class: str = "",
) -> str:
    cleaned = detail.strip() if detail else ""
    prefix = f"{label}: "
    if cleaned.startswith(prefix):
        cleaned = cleaned[len(prefix):].strip()
    if not cleaned:
        return ""

    class_name = "har-summary-category-pill-preview"
    if extra_class:
        class_name += f" {extra_class}"

    return "\n".join(
        [
            f'<span class="{class_name}">',
            f'  <span class="har-summary-category-pill-preview-label">{html.escape(label)}</span>',
            f'  <span class="har-summary-category-pill-preview-detail">{html.escape(cleaned)}</span>',
            "</span>",
        ]
    )


def _summary_category_strip_preview_rows_html(
    label: str,
    rows: list[dict[str, str]],
    *,
    extra_class: str = "",
) -> str:
    if not rows:
        return ""

    class_name = "har-summary-category-pill-preview"
    if extra_class:
        class_name += f" {extra_class}"
    if len(rows) == 1:
        class_name += " is-single-row"

    lines = [
        f'<span class="{class_name}">',
        f'  <span class="har-summary-category-pill-preview-label">{html.escape(label)}</span>',
        '  <span class="har-summary-category-pill-preview-rows">',
    ]
    for row in rows:
        title = str(row.get("title", "")).strip()
        state, detail, detail_secondary = _signature_surface_meaning_parts(row)
        state_badge = _meaning_row_state_badge(row)
        duration = str(row.get("duration", "")).strip()
        meta = str(row.get("meta", "")).strip()
        extra_detail_lines = _meaning_row_extra_detail_lines(
            row,
            signature_surface=True,
        )
        row_class = "har-summary-category-pill-preview-row"
        if title.startswith("+"):
            row_class += " is-more"
        if detail_secondary or extra_detail_lines:
            row_class += " has-secondary"
        lines.extend(
            [
                f'    <span class="{row_class}">',
                '      <span class="har-summary-category-pill-preview-row-head">',
                f'        <span class="har-summary-category-pill-preview-row-title">{html.escape(title)}</span>',
            ]
        )
        if state_badge:
            lines.append(
                f'        <span class="har-summary-category-pill-preview-row-state">{html.escape(state_badge)}</span>'
            )
        lines.extend(
            [
                "      </span>",
                '      <span class="har-summary-category-pill-preview-row-read">',
            ]
        )
        if duration:
            lines.append(
                f'        <span class="har-summary-category-pill-preview-detail-primary">{html.escape(duration)}</span>'
            )
        if duration:
            lines.append(
                f'        <span class="har-summary-category-pill-preview-detail">{html.escape(meta or detail)}</span>'
            )
        elif detail:
            lines.append(
                f'        <span class="har-summary-category-pill-preview-detail">{html.escape(detail)}</span>'
            )
        elif meta:
            lines.append(
                f'        <span class="har-summary-category-pill-preview-detail">{html.escape(meta)}</span>'
            )
        if duration:
            for extra_detail in extra_detail_lines:
                lines.append(
                    f'        <span class="har-summary-category-pill-preview-detail-secondary">{html.escape(extra_detail)}</span>'
                )
        elif extra_detail_lines:
            for extra_detail in extra_detail_lines:
                lines.append(
                    f'        <span class="har-summary-category-pill-preview-detail-secondary">{html.escape(extra_detail)}</span>'
                )
        lines.extend(
            [
                "      </span>",
                "    </span>",
            ]
        )
    lines.extend(
        [
            "  </span>",
            "</span>",
        ]
    )
    return "\n".join(lines)


def _summary_category_table_html(
    cats: list[tuple[str, int, list[tuple[str, int, list[dict]]]]],
    total_min: int,
    *,
    days: int = 7,
    visible_count: int = TOP_PROOF_VISIBLE_ACTIVITY_COUNT,
) -> str:
    if not cats or total_min <= 0:
        return ""

    scan_copy = _range_surface_scan_copy(days)
    density = _range_surface_density(days)
    time_overflow_preview_items = 2 if days <= 7 else 3
    lines = [
        "<div class=\"har-summary-category-table\">",
        "  <div class=\"har-summary-category-table-head\">",
        "    <span class=\"har-summary-category-table-label\">Category Time Review</span>",
        f"    <span class=\"har-summary-category-table-copy\">{html.escape(scan_copy['table_copy'])}</span>",
        "  </div>",
        "  <div class=\"har-summary-category-table-rows\">",
    ]

    for cat, total, acts in cats:
        label = cat.replace("-", " ").title()
        share = round((total / total_min) * 100) if total_min else 0
        activity_count = len(acts)
        log_count = sum(len(act_entries) for _, _, act_entries in acts)
        meta_text = _category_visibility_meta_text(
            activity_count,
            log_count,
            visible_count=visible_count,
        )
        meaning_rows_html = _category_time_scan_meaning_rows_html(
            acts,
            visible_count=visible_count,
            max_chars=density["table_meaning_chars"],
            overflow_max_chars=density["table_overflow_meaning_chars"],
            overflow_preview_items=2,
            overflow_title_max_chars=max(68, min(84, density["table_overflow_meaning_chars"])),
            compact_overflow_title=True,
        )
        lines.extend(
            [
                "    <div class=\"har-summary-category-table-row\">",
                "      <div class=\"har-summary-category-table-main\">",
                f"        <span class=\"har-summary-category-table-name\">{html.escape(label)}</span>",
                f"        <span class=\"har-summary-category-table-meta\">{html.escape(meta_text)}</span>",
                "      </div>",
                "      <div class=\"har-summary-category-table-total\">",
                f"        <span class=\"har-summary-category-table-minutes\">{html.escape(_human_duration(total))}</span>",
                f"        <span class=\"har-summary-category-table-share\">{share}% of range</span>",
                "      </div>",
                "      <div class=\"har-summary-category-table-read\">",
            ]
        )
        time_rows_html = _category_time_scan_rows_html(
            acts,
            visible_count=visible_count,
            overflow_max_chars=density["table_time_overflow_chars"],
            overflow_title_max_chars=density["table_time_overflow_title_chars"],
            compact_overflow_title=True,
            overflow_preview_items=time_overflow_preview_items,
        )
        if time_rows_html:
            lines.append(
                "        <div class=\"har-summary-category-table-item is-stack\">"
                "          <span class=\"har-summary-category-table-item-label\">Activity Time</span>"
                f"{time_rows_html}"
                "        </div>"
            )
        if meaning_rows_html:
            lines.append(
                "        <div class=\"har-summary-category-table-item is-meaning\">"
                "          <span class=\"har-summary-category-table-item-label\">Activity Read</span>"
                f"{meaning_rows_html}"
                "        </div>"
            )
        lines.extend(
            [
                "      </div>",
                "    </div>",
            ]
        )

    lines.extend(
        [
            "  </div>",
            "</div>",
        ]
    )
    return "\n".join(lines)


def _category_chip_row_shell_html(chips: list[str], *, days: int) -> str:
    if not chips:
        return ""

    scan_copy = _range_surface_scan_copy(days)
    label = "Activity Stats Review"
    if days >= 36500:
        label = "Calibration Activity Stats Review"
    elif days > 7:
        label = "Pattern Activity Stats Review"
    lines = [
        '<div class="har-category-chip-shell">',
        '  <div class="har-category-chip-shell-head">',
        f'    <span class="har-category-chip-shell-label">{html.escape(label)}</span>',
        f'    <span class="har-category-chip-shell-copy">{html.escape(scan_copy["chips_copy"])}</span>',
        "  </div>",
        '  <div class="har-category-chip-row">',
        *chips,
        "  </div>",
        "</div>",
    ]
    return "\n".join(lines)


def _category_chip_time_rows(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
    max_items: int = TOP_PROOF_VISIBLE_ACTIVITY_COUNT,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for act_name, act_min, act_entries in acts[:max_items]:
        rows.append(
            _activity_time_row_data(
                act_name,
                act_min,
                len(act_entries),
                total=total,
            )
        )

    if len(acts) > max_items:
        remaining_acts = acts[max_items:]
        remaining_logs = sum(len(act_entries) for _, _, act_entries in remaining_acts)
        remaining_minutes = sum(act_min for _, act_min, _ in remaining_acts)
        remaining_share = round((remaining_minutes / total) * 100) if total else 0
        duration = _human_activity_duration(remaining_minutes)
        remaining_parts = [_count_phrase(remaining_logs, "log")]
        if remaining_share:
            remaining_parts.append(f"{remaining_share}% of category")
        rows.append(
            {
                "title": _overflow_activity_title(remaining_acts, prefix_count=len(remaining_acts)),
                "detail": " · ".join([duration, *remaining_parts]),
                "duration": duration,
                "meta": " · ".join(remaining_parts),
            }
        )
    return rows


def _overflow_activity_title_parts(
    acts: list[tuple[str, int, list[dict]]],
    *,
    prefix_count: int,
    max_names: int = 2,
    max_chars: int = 52,
) -> tuple[str, list[str]]:
    names = [act_name for act_name, _, _ in acts]
    cleaned = _unique_in_order(names)
    if not cleaned:
        return (f"+{prefix_count} more activities", [])

    prefix = f"+{prefix_count} more: "
    available_chars = max(16, max_chars)
    preferred_max_names = min(len(cleaned), max_names)

    # Keep hidden activity identity visible even in compact rows. Prefer one
    # more literal hidden name when it fits, but preserve a residual `+N more`
    # cue so the row still reads like overflow instead of a renamed activity.
    variant_sets: list[list[str]] = [cleaned]
    trimmed = [_trim_parenthetical_suffix(name) or name for name in cleaned]
    if trimmed != cleaned:
        variant_sets.append(trimmed)

    preview = ""
    preview_visible_names: list[str] = []
    for variant_names in variant_sets:
        max_visible = min(preferred_max_names, len(variant_names))
        for visible_count in range(max_visible, 0, -1):
            visible = variant_names[:visible_count]
            candidate = " + ".join(visible)
            remainder = len(variant_names) - visible_count
            if remainder > 0 and visible_count > 1:
                candidate += f" +{remainder} more"
            if candidate and len(candidate) <= available_chars:
                preview = candidate
                preview_visible_names = cleaned[:visible_count]
                break
        if preview:
            break

    if not preview:
        preview = _leading_name_preview(
            cleaned,
            max_names=1,
            max_chars=available_chars,
        )
        if preview:
            preview_visible_names = cleaned[:1]
    if preview:
        return (f"{prefix}{preview}", preview_visible_names)
    return (f"+{prefix_count} more activities", [])


def _overflow_activity_title(
    acts: list[tuple[str, int, list[dict]]],
    *,
    prefix_count: int,
    max_names: int = 2,
    max_chars: int = 52,
) -> str:
    title, _ = _overflow_activity_title_parts(
        acts,
        prefix_count=prefix_count,
        max_names=max_names,
        max_chars=max_chars,
    )
    return title


def _overflow_activity_title_preview_names(
    acts: list[tuple[str, int, list[dict]]],
    *,
    prefix_count: int | None = None,
    max_names: int = 2,
    max_chars: int = 52,
) -> list[str]:
    _, visible_names = _overflow_activity_title_parts(
        acts,
        prefix_count=prefix_count or len(acts),
        max_names=max_names,
        max_chars=max_chars,
    )
    return visible_names


def _compact_grouped_time_title_parts(
    acts: list[tuple[str, int, list[dict]]],
    *,
    prefix_count: int,
    max_chars: int = 84,
) -> tuple[str, list[str]]:
    default_title = _more_group_title(
        prefix_count,
        singular="grouped",
        plural="grouped",
    )
    return (default_title, [])


def _grouped_time_preview_detail(
    acts: list[tuple[str, int, list[dict]]],
    *,
    max_names: int = 2,
    max_chars: int = 68,
) -> str:
    return " · ".join(
        _grouped_time_preview_lines(
            acts,
            max_names=max_names,
            max_chars=max_chars,
        )
    )


def _grouped_time_preview_lines(
    acts: list[tuple[str, int, list[dict]]],
    *,
    max_names: int = 2,
    max_chars: int = 68,
) -> list[str]:
    if not acts:
        return []

    preview_acts = acts[:max_names]
    if not preview_acts:
        return []

    preview_lines: list[str] = []
    remaining = max(0, len(acts) - len(preview_acts))
    for index, (act_name, act_min, _) in enumerate(preview_acts):
        line = f"{act_name} {_human_activity_duration(act_min)}".strip()
        suffix = ""
        if index == len(preview_acts) - 1 and remaining > 0:
            suffix = f" · +{remaining} more"
        if suffix and len(f"{line}{suffix}") > max_chars:
            compact_line = _compact_preview_detail(
                line,
                limit=max(24, max_chars - len(suffix)),
            )
            if compact_line:
                line = compact_line
        if suffix and len(f"{line}{suffix}") <= max_chars:
            line = f"{line}{suffix}"
        elif suffix and not line:
            line = suffix.lstrip(" ·")
        preview_lines.append(_truncate_text(line, limit=max_chars))
    return [line for line in preview_lines if line]


def _row_extra_detail_lines(row: dict[str, str]) -> list[str]:
    extra_lines: list[str] = []
    for key in ("detail_secondary", "detail_tertiary", "detail_quaternary"):
        value = str(row.get(key, "")).strip()
        if value:
            extra_lines.append(value)
    return extra_lines


def _meaning_row_extra_detail_lines(
    row: dict[str, str],
    *,
    signature_surface: bool = False,
) -> list[str]:
    if signature_surface:
        _, _, detail_secondary = _signature_surface_meaning_parts(row)
    else:
        _, _, detail_secondary = _meaning_row_display_parts(row)

    extra_lines: list[str] = []
    if detail_secondary:
        extra_lines.append(detail_secondary)
    for key in ("detail_tertiary", "detail_quaternary"):
        value = str(row.get(key, "")).strip()
        if value:
            extra_lines.append(value)
    return extra_lines


def _overflow_activity_preview_acts(
    acts: list[tuple[str, int, list[dict]]],
    *,
    max_items: int = 1,
) -> list[tuple[str, int, list[dict]]]:
    if not acts:
        return []

    def _state_priority(state_label: str) -> int:
        priorities = {
            "capture mix": 7,
            "movement data": 6,
            "notes-derived movements": 5,
            "stats reported": 4,
            "notes-only + no-stats": 3,
            "no-stats": 2,
            "notes-only": 1,
        }
        return priorities.get(state_label, 0)

    ranked_acts = sorted(
        enumerate(acts),
        key=lambda item: (
            -_state_priority(_activity_state_summary_parts(item[1][2], include_stat_values=False)[0]),
            item[0],
        ),
    )
    return [act for _, act in ranked_acts[:max_items]]


def _overflow_activity_meaning_detail(
    acts: list[tuple[str, int, list[dict]]],
    *,
    title_acts: list[tuple[str, int, list[dict]]] | None = None,
    max_items: int = 1,
    max_chars: int = 164,
) -> str:
    if not acts:
        return "activity reads below"

    preview_acts = _overflow_activity_preview_acts(acts, max_items=max_items)
    preview_chars = max(72, min(112, max_chars - 52))
    preview_parts: list[str] = []
    title_preview_names = {
        name.lower()
        for name in _overflow_activity_title_preview_names(
            title_acts or acts,
            prefix_count=len(title_acts or acts),
            max_chars=max(52, min(72, max_chars - 24)),
        )
        if name
    }

    for act_name, _, act_entries in preview_acts:
        text = _activity_summary_meaning_text(
            act_entries,
            max_chars=min(176, preview_chars + 72),
        )
        # Hidden-activity preview rows should keep one strongest concrete read,
        # not stack that activity's own overflow suffixes on top of the group
        # overflow handoff.
        text = re.sub(r"\s*/\s*\+\d+\s+more\s+note\s+reads?\b.*$", "", text).strip()
        text = re.sub(r"\s*/\s*\+\d+\s+more\s+reads?\b.*$", "", text).strip()
        text = re.sub(r"\s*/\s*\+\d+\s+more\s+stats?\b.*$", "", text).strip()
        text = re.sub(r"\s*·\s*\+\d+\s+more\s+reads?\s+below\b.*$", "", text).strip()
        text = re.sub(r"\s*·\s*\+\d+\s+more\s+details?\b.*$", "", text).strip()
        if text.startswith(("capture mix ·", "movement data ·", "notes-derived movements ·")):
            text = re.sub(r"\s*/\s*[^/]+$", "", text).strip()
        text = re.sub(r"\s*·\s*\+\d+\s+more\s+reads?\b.*$", "", text).strip()
        if len(text) > max_chars:
            compact_text = re.sub(r"\btotal sets\b", "sets", text)
            compact_text = re.sub(r"\btotal reps\b", "reps", compact_text)
            compact_text = re.sub(r"\bloads\b", "loads", compact_text)
            if len(compact_text) <= max_chars:
                text = compact_text
        if len(text) > 148:
            text = _compact_segmented_preview(text, limit=148, min_keep=4)
        same_activity_prefix = rf"^from\s+{re.escape(act_name.strip())}\s+·\s*"
        text = re.sub(same_activity_prefix, "", text, flags=re.IGNORECASE).strip()
        if not text:
            text = "activity read below"
        elif act_name.strip().lower() not in title_preview_names:
            text = f"from {act_name} · {text}"
        preview_parts.append(text)

    summary, hidden_count = _bounded_preview_join(
        _unique_in_order(preview_parts),
        max_chars=max_chars,
    )
    remaining_hidden = hidden_count + max(0, len(acts) - len(preview_acts))
    if remaining_hidden > 1:
        summary = _append_detail_suffix(summary, _more_reads_suffix(remaining_hidden))
    if summary.startswith(("capture mix ·", "movement data ·", "notes-derived movements ·")):
        return _compact_workout_surface_text(summary, limit=max_chars)
    return _compact_preview_detail(summary or "activity reads below", limit=max_chars)


def _category_chip_meaning_rows(
    acts: list[tuple[str, int, list[dict]]],
    *,
    max_items: int = TOP_PROOF_VISIBLE_ACTIVITY_COUNT,
    max_chars: int = 148,
    overflow_max_chars: int = 164,
    overflow_preview_items: int = 1,
    overflow_title_max_chars: int | None = None,
    compact_overflow_title: bool = False,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for act_name, _, act_entries in acts[:max_items]:
        rows.append(_activity_meaning_row(act_name, act_entries, max_chars=max_chars))

    if len(acts) > max_items:
        remaining_acts = acts[max_items:]
        rows.append(
            _overflow_meaning_row(
                remaining_acts,
                title_acts=remaining_acts,
                prefix_count=len(remaining_acts),
                max_items=max(1, overflow_preview_items),
                max_chars=overflow_max_chars,
                title_max_chars=(
                    overflow_title_max_chars
                    if isinstance(overflow_title_max_chars, int)
                    else max(64, min(74, overflow_max_chars))
                ),
                compact_title=compact_overflow_title,
            )
        )
    return rows


def _category_chip_focus_rows(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
) -> list[dict[str, str]]:
    title, detail = _category_focus_row_parts(acts, total=total)
    if not title or not detail:
        return []

    return [
        {
            "title": title,
            "detail": _compact_preview_detail(detail, limit=128),
        }
    ]


def _activity_time_row_data(
    act_name: str,
    act_min: int,
    log_count: int,
    *,
    total: int,
    detail_secondary: str = "",
) -> dict[str, str]:
    share = round((act_min / total) * 100) if total else 0
    meta_parts = [_count_phrase(log_count, "log")]
    if share:
        meta_parts.append(f"{share}% of category")
    duration = _human_activity_duration(act_min)
    detail_parts = [duration, *meta_parts]
    return {
        "title": act_name,
        "detail": " · ".join(detail_parts),
        "duration": duration,
        "meta": " · ".join(meta_parts),
        "detail_secondary": detail_secondary,
    }


def _overflow_time_context_detail(
    acts: list[tuple[str, int, list[dict]]],
) -> str:
    if not acts:
        return ""

    leaders, _ = _leader_names(acts)
    if not leaders:
        return ""

    preview = _compact_name_preview(
        leaders,
        max_names=2,
        max_chars=52,
        overflow_singular="activity",
    )
    if not preview:
        preview = leaders[0]
    if not preview:
        return ""

    if len(leaders) > 1:
        return f"Hidden lead tie: {preview}"
    return f"Hidden lead: {preview}"


def _category_time_scan_rows(
    acts: list[tuple[str, int, list[dict]]],
    *,
    visible_count: int = TOP_PROOF_VISIBLE_ACTIVITY_COUNT,
    overflow_max_chars: int = 52,
    overflow_title_max_chars: int | None = None,
    compact_overflow_title: bool = False,
    overflow_preview_items: int = 2,
) -> list[dict[str, str]]:
    if not acts:
        return []

    total = sum(act_min for _, act_min, _ in acts)
    rows: list[dict[str, str]] = []
    for act_name, act_min, act_entries in acts[:visible_count]:
        rows.append(
            _activity_time_row_data(
                act_name,
                act_min,
                len(act_entries),
                total=total,
            )
        )

    if len(acts) > visible_count:
        remaining_acts = acts[visible_count:]
        remaining_minutes = sum(act_min for _, act_min, _ in remaining_acts)
        remaining_logs = sum(len(act_entries) for _, _, act_entries in remaining_acts)
        remaining_share = round((remaining_minutes / total) * 100) if total else 0
        duration = _human_activity_duration(remaining_minutes)
        meta_parts = [_count_phrase(remaining_logs, "log")]
        if remaining_share:
            meta_parts.append(f"{remaining_share}% of category")
        preview_title_names: list[str] = []
        overflow_title = ""
        if compact_overflow_title:
            overflow_title, preview_title_names = _compact_grouped_time_title_parts(
                remaining_acts,
                prefix_count=len(remaining_acts),
                max_chars=overflow_title_max_chars or overflow_max_chars,
            )
        else:
            overflow_title = _overflow_activity_title(
                remaining_acts,
                prefix_count=len(remaining_acts),
                max_chars=overflow_title_max_chars or overflow_max_chars,
            )
        grouped_preview_lines: list[str] = []
        if compact_overflow_title and not preview_title_names:
            grouped_preview_lines = _grouped_time_preview_lines(
                remaining_acts,
                max_names=max(1, overflow_preview_items),
                max_chars=max(48, overflow_max_chars + 8),
            )
        rows.append(
            {
                "title": overflow_title,
                "detail": " · ".join([duration, *meta_parts]),
                "duration": duration,
                "meta": " · ".join(meta_parts),
                "detail_secondary": (
                    grouped_preview_lines[0]
                    if grouped_preview_lines
                    else (
                        ""
                        if preview_title_names
                        else _overflow_time_context_detail(remaining_acts)
                    )
                ),
                "detail_tertiary": grouped_preview_lines[1] if len(grouped_preview_lines) > 1 else "",
                "detail_quaternary": grouped_preview_lines[2] if len(grouped_preview_lines) > 2 else "",
            }
        )

    return rows


def _category_chip_meter_html(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
    max_items: int = TOP_PROOF_VISIBLE_ACTIVITY_COUNT,
) -> str:
    if not acts or total <= 0 or len(acts) <= 1:
        return ""

    segments: list[str] = ['<div class="har-category-chip-meter" aria-hidden="true">']
    visible_acts = acts[:max_items]
    for index, (act_name, act_min, _) in enumerate(visible_acts, start=1):
        share = round((act_min / total) * 100) if total else 0
        if share <= 0:
            continue
        title = f"{act_name} — {_human_activity_duration(act_min)} — {share}% of category"
        segments.append(
            f'  <span class="har-category-chip-meter-segment is-rank-{index}"'
            f' title="{html.escape(title)}" style="width: {share}%;"></span>'
        )

    if len(acts) > max_items:
        remaining_acts = acts[max_items:]
        remaining_minutes = sum(act_min for _, act_min, _ in remaining_acts)
        remaining_share = round((remaining_minutes / total) * 100) if total else 0
        if remaining_share > 0:
            title = (
                f"+{len(remaining_acts)} more activities — "
                f"{_human_activity_duration(remaining_minutes)} — "
                f"{remaining_share}% of category"
            )
            segments.append(
                f'  <span class="har-category-chip-meter-segment is-more"'
                f' title="{html.escape(title)}" style="width: {remaining_share}%;"></span>'
            )

    segments.append("</div>")
    return "\n".join(segments)


def _category_chip_share_rows(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
    max_items: int = TOP_PROOF_VISIBLE_ACTIVITY_COUNT,
) -> list[dict[str, str]]:
    if not acts or total <= 0:
        return []

    rows: list[dict[str, str]] = []
    for index, (act_name, act_min, act_entries) in enumerate(acts[:max_items], start=1):
        share = round((act_min / total) * 100) if total else 0
        detail_parts = [_human_activity_duration(act_min)]
        if share:
            detail_parts.append(f"{share}% of category")
        detail_parts.append(_count_phrase(len(act_entries), "log"))
        rows.append(
            {
                "title": act_name,
                "detail": " · ".join(detail_parts),
                "rank_class": f"is-rank-{index}",
            }
        )

    if len(acts) > max_items:
        remaining_acts = acts[max_items:]
        remaining_logs = sum(len(act_entries) for _, _, act_entries in remaining_acts)
        remaining_minutes = sum(act_min for _, act_min, _ in remaining_acts)
        remaining_share = round((remaining_minutes / total) * 100) if total else 0
        detail_parts = [_human_activity_duration(remaining_minutes)]
        if remaining_share:
            detail_parts.append(f"{remaining_share}% of category")
        detail_parts.append(_count_phrase(remaining_logs, "log"))
        rows.append(
            {
                "title": _overflow_activity_title(remaining_acts, prefix_count=len(remaining_acts)),
                "detail": " · ".join(detail_parts),
                "rank_class": "is-more",
            }
        )
    return rows


def _category_chip_share_rail_html(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
    max_items: int = TOP_PROOF_VISIBLE_ACTIVITY_COUNT,
) -> str:
    if len(acts) <= 1:
        return ""
    rows = _category_chip_share_rows(acts, total=total, max_items=max_items)
    if not rows:
        return ""

    lines = [
        '<div class="har-category-chip-share-rail">',
        '  <span class="har-category-chip-share-rail-label">Activity Time Share</span>',
        '  <div class="har-category-chip-share-rail-list">',
    ]
    for row in rows:
        title = str(row.get("title", "")).strip()
        detail = str(row.get("detail", "")).strip()
        rank_class = str(row.get("rank_class", "")).strip()
        pill_class = "har-category-chip-share-pill"
        if rank_class:
            pill_class += f" {rank_class}"
        lines.extend(
            [
                f'    <div class="{pill_class}">',
                f'      <span class="har-category-chip-share-pill-title">{html.escape(title)}</span>',
                f'      <span class="har-category-chip-share-pill-detail">{html.escape(detail)}</span>',
                "    </div>",
            ]
        )
    lines.extend(
        [
            "  </div>",
            "</div>",
        ]
    )
    return "\n".join(lines)


def _category_time_scan_rows_html(
    acts: list[tuple[str, int, list[dict]]],
    *,
    visible_count: int = TOP_PROOF_VISIBLE_ACTIVITY_COUNT,
    overflow_max_chars: int = 52,
    overflow_title_max_chars: int | None = None,
    compact_overflow_title: bool = False,
    overflow_preview_items: int = 2,
) -> str:
    rows = _category_time_scan_rows(
        acts,
        visible_count=visible_count,
        overflow_max_chars=overflow_max_chars,
        overflow_title_max_chars=overflow_title_max_chars,
        compact_overflow_title=compact_overflow_title,
        overflow_preview_items=overflow_preview_items,
    )
    if not rows:
        return ""

    lines = ['<div class="har-summary-category-table-time-rows">']
    for row in rows:
        title = str(row.get("title", "")).strip()
        detail = str(row.get("detail", "")).strip()
        duration = str(row.get("duration", "")).strip()
        meta = str(row.get("meta", "")).strip()
        extra_detail_lines = _row_extra_detail_lines(row)
        row_class = "har-summary-category-time-row"
        if title.startswith("+"):
            row_class += " is-more"
        lines.extend(
            [
                f'  <div class="{row_class}">',
                '    <div class="har-summary-category-time-row-head">',
                f'      <span class="har-summary-category-time-row-title">{html.escape(title)}</span>',
                "    </div>",
                '    <div class="har-summary-category-time-row-read">',
            ]
        )
        if duration:
            lines.append(
                f'      <span class="har-summary-category-time-row-detail-primary">{html.escape(duration)}</span>'
            )
        lines.append(
            f'      <span class="har-summary-category-time-row-detail">{html.escape(meta or detail)}</span>'
        )
        for extra_detail in extra_detail_lines:
            lines.append(
                f'      <span class="har-summary-category-time-row-detail-secondary">{html.escape(extra_detail)}</span>'
            )
        lines.extend(
            [
                "    </div>",
                "  </div>",
            ]
        )
    lines.append("</div>")
    return "\n".join(lines)


def _category_chip_lane_rows_html(
    *,
    label: str,
    rows: list[dict[str, str]],
    lane_class: str = "",
) -> str:
    class_attr = "har-category-chip-lane"
    if lane_class:
        class_attr += f" {lane_class}"

    lines = [
        f"<div class=\"{class_attr}\">",
        f"  <span class=\"har-category-chip-lane-label\">{html.escape(label)}</span>",
        "  <div class=\"har-category-chip-lane-body har-category-chip-lane-list\">",
    ]
    for row in rows:
        title = str(row.get("title", "")).strip()
        state, detail, detail_secondary = _meaning_row_display_parts(row)
        state_badge = _meaning_row_state_badge(row)
        duration = str(row.get("duration", "")).strip()
        meta = str(row.get("meta", "")).strip()
        row_class = "har-category-chip-lane-row"
        if title.startswith("+"):
            row_class += " is-more"
        lines.append(f"    <div class=\"{row_class}\">")
        lines.append("      <div class=\"har-category-chip-row-head\">")
        lines.append(f"        <span class=\"har-category-chip-row-title\">{html.escape(title)}</span>")
        if state_badge:
            lines.append(f"        <span class=\"har-category-chip-row-state\">{html.escape(state_badge)}</span>")
        lines.append("      </div>")
        if duration:
            lines.append("      <span class=\"har-category-chip-row-read is-time\">")
            lines.append(
                f"        <span class=\"har-category-chip-row-detail-primary\">{html.escape(duration)}</span>"
            )
            lines.append(
                f"        <span class=\"har-category-chip-row-detail\">{html.escape(meta or detail)}</span>"
            )
            lines.append("      </span>")
        elif detail:
            lines.append(f"      <span class=\"har-category-chip-row-detail\">{html.escape(detail)}</span>")
        if detail_secondary:
            lines.append(
                f"      <span class=\"har-category-chip-row-detail-secondary\">{html.escape(detail_secondary)}</span>"
            )
        lines.append("    </div>")
    lines.append("  </div>")
    lines.append("</div>")
    return "\n".join(lines)


def _category_chip_html(
    *,
    label: str,
    total: int,
    share: int,
    activity_count: int,
    log_count: int,
    acts: list[tuple[str, int, list[dict]]],
    time_rows: list[dict[str, str]],
    meaning_rows: list[dict[str, str]],
    visible_count: int,
    include_time_lane: bool = True,
    include_meter: bool = True,
    include_share_rail: bool = True,
) -> str:
    meta = " · ".join(
        _compact_category_meta_items(
            activity_count,
            log_count,
            visible_count=visible_count,
        )
    )
    lines = [
        "<div class=\"har-category-chip\">",
        "  <div class=\"har-category-chip-head\">"
        f"<span class=\"har-category-chip-name\">{html.escape(label)}</span>"
        "<span class=\"har-category-chip-total\">"
        f"<span class=\"har-category-chip-total-primary\">{html.escape(_human_duration(total))}</span>"
        f"<span class=\"har-category-chip-total-secondary\">{total} min · {share}%</span>"
        "</span></div>",
        f"  <div class=\"har-category-chip-meta\">{html.escape(meta)}</div>",
    ]
    meter_html = ""
    if include_meter:
        meter_html = _category_chip_meter_html(acts, total=total, max_items=visible_count)
    if meter_html:
        lines.append(meter_html)
    share_rail_html = ""
    if include_share_rail:
        share_rail_html = _category_chip_share_rail_html(acts, total=total, max_items=visible_count)
    if share_rail_html:
        lines.append(share_rail_html)
    if include_time_lane and time_rows:
        lines.append(_category_chip_lane_rows_html(label="Activity Time", rows=time_rows))
    if meaning_rows:
        lines.append(
            _category_chip_lane_rows_html(
                label="Activity Read",
                rows=meaning_rows,
                lane_class="is-meaning",
            )
        )
    lines.append("</div>")
    return "\n".join(lines)


def _chart_shell_range_match_items(
    cats: list[tuple[str, int, list[tuple[str, int, list[dict]]]]],
    *,
    total_min: int,
    max_items: int = 4,
) -> list[str]:
    if not cats or total_min <= 0:
        return []

    items: list[str] = []
    visible = cats[:max_items]
    for label, total, _ in visible:
        share = round((total / total_min) * 100) if total_min else 0
        items.append(
            f"{label.replace('-', ' ').title()} {_human_duration(total)} · {share}%"
        )

    hidden = max(0, len(cats) - len(visible))
    if hidden:
        items.append(f"+{hidden} more categories")
    return items


def _chart_shell_range_match_html(
    cats: list[tuple[str, int, list[tuple[str, int, list[dict]]]]],
    *,
    total_min: int,
    max_items: int = 4,
) -> str:
    items = _chart_shell_range_match_items(cats, total_min=total_min, max_items=max_items)
    if not items:
        return ""

    lines = [
        '  <div class="har-chart-shell-glance" aria-label="Chart range match">',
        '    <span class="har-chart-shell-glance-label">Range Match</span>',
        '    <span class="har-chart-shell-glance-items">',
    ]
    for item in items:
        lines.append(
            f'      <span class="har-chart-shell-glance-item">{html.escape(item)}</span>'
        )
    lines.extend(
        [
            "    </span>",
            "  </div>",
        ]
    )
    return "\n".join(lines)


def _category_chart_shell_html(
    chart_file: str,
    *,
    days: int,
    cats: list[tuple[str, int, list[tuple[str, int, list[dict]]]]] | None = None,
    total_min: int = 0,
) -> str:
    read_pills: list[str] = []
    if days <= 7:
        shell_variant = "signature"
        kicker = "Category Time Chart"
        copy = (
            "Confirmation layer for the same category totals above. "
            "Bars show share shape; labels keep visible activity context. "
            "Hover is optional desktop detail only."
        )
        read_pills = [
            "1. Category total first",
            "2. Activity mix inside the bar",
            "3. Range share + activity/log counts",
        ]
    elif days >= 36500:
        shell_variant = "reference"
        kicker = "Calibration Chart"
        copy = (
            "Quiet confirmation after the compact category rows. Use it only to calibrate the weekly read against the full baseline."
        )
    else:
        shell_variant = "pattern"
        kicker = "Pattern Chart"
        copy = (
            "Quiet confirmation after the compact category rows. Open it only after the weekly pass."
        )

    lines = [
        f"<div class=\"har-chart-shell\" data-chart-variant=\"{html.escape(shell_variant)}\">",
        "  <div class=\"har-chart-shell-head\">",
        f"    <div class=\"har-chart-shell-kicker\">{html.escape(kicker)}</div>",
        f"    <div class=\"har-chart-shell-copy\">{html.escape(copy)}</div>",
        "  </div>",
    ]
    range_match_html = _chart_shell_range_match_html(cats or [], total_min=total_min)
    if range_match_html:
        lines.append(range_match_html)
    if read_pills:
        lines.extend(
            [
                "  <div class=\"har-chart-shell-read\" aria-label=\"Chart read order\">",
                *[
                    f"    <span class=\"har-chart-shell-read-pill\">{html.escape(pill)}</span>"
                    for pill in read_pills
                ],
                "  </div>",
            ]
        )
    lines.extend(
        [
            "  <div class=\"har-chart-shell-frame\">",
            f"![[_derived/har-graphs/{chart_file}]]",
            "  </div>",
            "</div>",
        ]
    )
    return "\n".join(lines)


def _summary_category_totals_line(
    cats: list[tuple[str, int, list[tuple[str, int, list[dict]]]]],
    total_min: int,
) -> str:
    line = _category_totals_strip_line(cats, total_min, max_items=4)
    return line[2:] if line.startswith("- ") else line


def _main_review_summary_html(
    *,
    total_min: int,
    entry_count: int,
    category_count: int,
    bounds: tuple[date, date] | None,
    date_values: list[str],
    active_days: int,
    capture_line: str,
    top_category: tuple[str, int, list[tuple[str, int, list[dict]]]] | None,
    cats: list[tuple[str, int, list[tuple[str, int, list[dict]]]]],
    days: int,
    top_surface_visible_count: int,
) -> str:
    role = _range_surface_role(days)
    density = _range_surface_density(days)
    include_visual_share_chrome = days <= 7
    # The weekly page-top shell already owns the ranked category-totals strip.
    # Keep the embedded `Main Review Surface` focused on proof/detail so the
    # page does not open with two near-identical category-total scans in a row.
    include_summary_category_strip = False
    summary_panel_preview_items = top_surface_visible_count if days <= 7 else 2
    max_summary_categories = 3
    table_visible_count = density["table_visible_items"]
    chip_visible_count = density["chip_visible_items"]
    coverage_lines: list[str] = []
    if bounds:
        window_start, window_end = bounds
        if days >= 36500:
            coverage_lines.append(f"selected window: all logged time through {window_end.isoformat()}")
        else:
            coverage_lines.append(f"selected window: {_plain_date_span(window_start, window_end)}")
    if date_values:
        if min(date_values) == max(date_values):
            coverage_lines.append(f"logged date: {date_values[0]} only")
        else:
            coverage_lines.append(f"logged dates: {min(date_values)} to {max(date_values)}")
        coverage_lines.append(f"active days: {active_days}")
        if bounds and days < 36500:
            window_start, window_end = bounds
            selected_days = (window_end - window_start).days + 1
            empty_days = max(0, selected_days - active_days)
            coverage_lines.append(f"coverage: {active_days} of {selected_days} days")
            if empty_days:
                coverage_lines.append(f"empty days: {empty_days}")
    sparse_week_handoff = _sparse_week_handoff_line(days=days, active_days=active_days)
    if sparse_week_handoff:
        coverage_lines.append(
            sparse_week_handoff[2:]
            .replace("**", "")
            .replace(", so keep this weekly read for honesty and open ", "; keep the weekly read for honesty, then open ")
            .replace(" next if you need a denser pattern read", " for denser pattern context")
        )

    category_lines = [f"{category_count} {'category' if category_count == 1 else 'categories'} with time"]
    if top_category:
        top_label = top_category[0].replace("-", " ").title()
        category_lines.append(f"biggest category: {top_label} at {_duration_summary_text(top_category[1])}")

    summary_panels = [
        _summary_panel_html(
            title=f"{_human_duration(total_min)} · {entry_count} entries",
            kicker="Range Read",
            lines=[capture_line[2:] if capture_line.startswith("- ") else capture_line],
            emphasized=True,
        ),
    ]
    if days > 7:
        summary_panels.append(
            _summary_panel_html(
                title="Category Totals",
                kicker="Category First",
                lines=_summary_category_total_lines(cats, total_min=total_min),
                extra_class="is-category-totals",
                raw_body_html=_summary_category_totals_panel_body_html(
                    cats,
                    total_min=total_min,
                    days=days,
                    max_categories=max_summary_categories,
                    preview_items=min(summary_panel_preview_items, top_surface_visible_count),
                ),
            )
        )
    summary_panels.append(
        _summary_panel_html(
            title="Window Coverage",
            kicker="Coverage",
            lines=coverage_lines + category_lines,
        )
    )

    chips = []
    for cat, total, acts in cats:
        label = cat.replace("-", " ").title()
        share = round((total / total_min) * 100) if total_min else 0
        activity_count = len(acts)
        log_count = sum(len(act_entries) for _, _, act_entries in acts)
        chips.append(
            _category_chip_html(
                label=label,
                total=total,
                share=share,
                activity_count=activity_count,
                log_count=log_count,
                acts=acts,
                time_rows=_category_chip_time_rows(
                    acts,
                    total=total,
                    max_items=chip_visible_count,
                ),
                meaning_rows=_category_chip_meaning_rows(
                    acts,
                    max_items=chip_visible_count,
                    max_chars=density["chip_meaning_chars"],
                    overflow_max_chars=density["chip_overflow_meaning_chars"],
                    overflow_preview_items=2,
                    overflow_title_max_chars=max(68, min(84, density["chip_overflow_meaning_chars"])),
                    compact_overflow_title=True,
                ),
                visible_count=chip_visible_count,
                include_time_lane=False,
                include_meter=include_visual_share_chrome,
                include_share_rail=include_visual_share_chrome,
            )
        )

    parts = [
        f"<div class=\"har-main-review-summary {role['class_name']}\">",
        "  <div class=\"har-summary-panels\">",
        *summary_panels,
        "  </div>",
        _range_role_banner_html(days),
    ]
    if include_summary_category_strip:
        parts.append(_summary_category_strip_html(cats, total_min, days=days))
    parts.append(
        _summary_category_table_html(
            cats,
            total_min,
            days=days,
            visible_count=table_visible_count,
        )
    )
    parts.append(_category_chip_row_shell_html(chips, days=days))
    parts.append("</div>")
    return "\n".join(parts)


def _window_capture_line(entries: list[dict]) -> str:
    if not entries:
        return "- log coverage: no logged entries yet"

    structured_logs = 0
    notes_only_logs = 0
    explicit_no_stats_logs = 0
    for entry in entries:
        custom_fields = entry.get("custom_fields", {}) or {}
        notes = entry.get("notes", "")
        has_explicit_no_stats = "no stats to report" in notes.lower()
        note_clues = _extract_note_clues([entry], limit=1)
        if custom_fields:
            structured_logs += 1
        elif note_clues or has_explicit_no_stats:
            notes_only_logs += 1
        if has_explicit_no_stats:
            explicit_no_stats_logs += 1

    line = (
        f"- log coverage: {_count_phrase(structured_logs, 'structured log')}"
        f" · {_count_phrase(notes_only_logs, 'notes-only log')}"
    )
    if explicit_no_stats_logs:
        line += f" ({_count_phrase(explicit_no_stats_logs, 'explicit no-stats log')})"
    return line


def _category_review_summary_line(acts: list[tuple[str, int, list[dict]]]) -> str:
    activity_count = len(acts)
    log_count = sum(len(act_entries) for _, _, act_entries in acts)
    line = f"- category read: {_category_visibility_meta_text(activity_count, log_count, visible_count=TOP_PROOF_VISIBLE_ACTIVITY_COUNT)}"
    if not acts:
        return line
    total = sum(act_min for _, act_min, _ in acts)
    focus_title, focus_detail = _category_focus_row_parts(acts, total=total)
    if focus_title and focus_detail:
        line += f" · {focus_title}: {focus_detail}"
    return line


def _category_stack_read_line(total: int, acts: list[tuple[str, int, list[dict]]]) -> str:
    if not acts or total <= 0:
        return "- stack read: no activity time yet"

    stack_parts = []
    for act_name, act_min, _ in acts[:4]:
        share = round((act_min / total) * 100) if total else 0
        stack_parts.append(f"{act_name} {share}%")

    if len(acts) > 4:
        visible_total = sum(act_min for _, act_min, _ in acts[:4])
        remaining = total - visible_total
        remaining_count = len(acts) - 4
        if remaining > 0:
            stack_parts.append(
                f"other {_count_phrase(remaining_count, 'activity', 'activities')} {round((remaining / total) * 100)}%"
            )

    return "- stack read: " + " · ".join(stack_parts)


def _category_time_stack_line(
    acts: list[tuple[str, int, list[dict]]],
    *,
    visible_count: int = 3,
) -> str:
    if not acts:
        return "time stack: no activity time yet"

    total = sum(act_min for _, act_min, _ in acts)
    parts = []
    for act_name, act_min, act_entries in acts[:visible_count]:
        share = round((act_min / total) * 100) if total else 0
        line = f"{_human_activity_duration(act_min)} {act_name} · {_count_phrase(len(act_entries), 'log')}"
        if share:
            line += f" · {share}%"
        parts.append(line)
    if len(acts) > visible_count:
        remaining_acts = acts[visible_count:]
        remaining_logs = sum(len(act_entries) for _, _, act_entries in remaining_acts)
        remaining_minutes = sum(act_min for _, act_min, _ in remaining_acts)
        remaining_share = round((remaining_minutes / total) * 100) if total else 0
        overflow = (
            f"+{len(remaining_acts)} more activities · {_count_phrase(remaining_logs, 'log')} · "
            f"{_human_activity_duration(remaining_minutes)}"
        )
        if remaining_share:
            overflow += f" · {remaining_share}%"
        parts.append(overflow)
    return "time stack: " + " · ".join(parts)


def _category_time_stack_rows(
    acts: list[tuple[str, int, list[dict]]],
    *,
    visible_count: int = 3,
    share_label: str = "of category",
) -> list[str]:
    if not acts:
        return ["no activity time yet"]

    total = sum(act_min for _, act_min, _ in acts)
    rows: list[str] = []
    for act_name, act_min, act_entries in acts[:visible_count]:
        share = round((act_min / total) * 100) if total else 0
        row = f"{_human_activity_duration(act_min)} {act_name} · {_count_phrase(len(act_entries), 'log')}"
        if share:
            row += f" · {share}% {share_label}"
        rows.append(row)

    if len(acts) > visible_count:
        remaining_acts = acts[visible_count:]
        remaining_logs = sum(len(act_entries) for _, _, act_entries in remaining_acts)
        remaining_minutes = sum(act_min for _, act_min, _ in remaining_acts)
        remaining_share = round((remaining_minutes / total) * 100) if total else 0
        overflow = (
            f"+{len(remaining_acts)} more activities · "
            f"{_human_activity_duration(remaining_minutes)} · {_count_phrase(remaining_logs, 'log')}"
        )
        if remaining_share:
            overflow += f" · {remaining_share}% {share_label}"
        rows.append(overflow)

    return rows


def _category_time_stack_lines(
    acts: list[tuple[str, int, list[dict]]],
    *,
    label: str,
    visible_count: int = 3,
) -> list[str]:
    rows = _category_time_stack_rows(acts, visible_count=visible_count)
    return [f"- {label}:"] + [f"  - {row}" for row in rows]


def _category_time_scan_preview(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
    visible_count: int = 3,
) -> str:
    if not acts or total <= 0:
        return ""

    rows = _category_time_scan_rows(acts, visible_count=visible_count)
    if not rows:
        return ""

    parts = []
    for row in rows:
        title = str(row.get("title", "")).strip()
        detail = str(row.get("detail", "")).strip()
        parts.append(f"{title} {detail}".strip())

    return "top stack: " + " / ".join(parts)


def _category_time_scan_meaning_preview(
    acts: list[tuple[str, int, list[dict]]],
    *,
    max_items: int = 3,
) -> str:
    if not acts:
        return ""

    parts: list[str] = []
    for act_name, _, act_entries in acts[:max_items]:
        state_label, detail = _activity_state_summary_parts(
            act_entries,
            include_stat_values=False,
        )
        part = f"{act_name} — {state_label}"
        if detail:
            part += f": {detail}"
        parts.append(part)

    summary = " / ".join(parts)
    if len(summary) > 220:
        summary = _truncate_text(summary, limit=220)
    total_hidden = max(0, len(acts) - max_items)
    if total_hidden > 0:
        overflow_text = f"+{total_hidden} more activities below"
        summary = f"{summary} / {overflow_text}" if summary else overflow_text

    if not summary:
        return ""
    return "activity read: " + summary


def _category_time_scan_meaning_rows(
    acts: list[tuple[str, int, list[dict]]],
    *,
    max_items: int = TOP_PROOF_VISIBLE_ACTIVITY_COUNT,
    max_chars: int = 148,
    overflow_max_chars: int = 164,
    overflow_preview_items: int = 1,
    overflow_title_max_chars: int | None = None,
    compact_overflow_title: bool = False,
) -> list[dict[str, str]]:
    if not acts:
        return []

    rows: list[dict[str, str]] = []
    for act_name, _, act_entries in acts[:max_items]:
        rows.append(_activity_meaning_row(act_name, act_entries, max_chars=max_chars))

    if len(acts) > max_items:
        remaining_acts = acts[max_items:]
        rows.append(
            _overflow_meaning_row(
                remaining_acts,
                title_acts=remaining_acts,
                prefix_count=len(remaining_acts),
                max_items=max(1, overflow_preview_items),
                max_chars=overflow_max_chars,
                title_max_chars=(
                    overflow_title_max_chars
                    if isinstance(overflow_title_max_chars, int)
                    else max(64, min(74, overflow_max_chars))
                ),
                compact_title=compact_overflow_title,
            )
        )
    return rows


def _category_time_scan_meaning_rows_html(
    acts: list[tuple[str, int, list[dict]]],
    *,
    visible_count: int = TOP_PROOF_VISIBLE_ACTIVITY_COUNT,
    max_chars: int = 148,
    overflow_max_chars: int = 164,
    overflow_preview_items: int = 1,
    overflow_title_max_chars: int | None = None,
    compact_overflow_title: bool = False,
) -> str:
    rows = _category_time_scan_meaning_rows(
        acts,
        max_items=visible_count,
        max_chars=max_chars,
        overflow_max_chars=overflow_max_chars,
        overflow_preview_items=overflow_preview_items,
        overflow_title_max_chars=overflow_title_max_chars,
        compact_overflow_title=compact_overflow_title,
    )
    if not rows:
        return ""

    lines = ['<div class="har-summary-category-table-meaning-rows">']
    for row in rows:
        title = str(row.get("title", "")).strip()
        state, detail, detail_secondary = _meaning_row_display_parts(row)
        state_badge = _meaning_row_state_badge(row)
        extra_detail_lines = _meaning_row_extra_detail_lines(row)
        row_class = "har-summary-category-meaning-row"
        if title.startswith("+"):
            row_class += " is-more"
        lines.extend(
            [
                f'  <div class="{row_class}">',
                '    <div class="har-summary-category-meaning-row-head">',
                f'      <span class="har-summary-category-meaning-row-title">{html.escape(title)}</span>',
            ]
        )
        if state_badge:
            lines.append(
                f'      <span class="har-summary-category-meaning-row-state">{html.escape(state_badge)}</span>'
            )
        lines.extend(
            [
                "    </div>",
                '    <div class="har-summary-category-meaning-row-read">',
                f'      <span class="har-summary-category-meaning-row-detail">{html.escape(detail)}</span>'
            ]
        )
        if extra_detail_lines:
            for extra_detail in extra_detail_lines:
                lines.append(
                    f'      <span class="har-summary-category-meaning-row-detail-secondary">{html.escape(extra_detail)}</span>'
                )
        lines.extend(
            [
                "    </div>",
                "  </div>",
            ]
        )
    lines.append("</div>")
    return "\n".join(lines)


def _category_carry_summary(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
) -> str:
    if not acts or total <= 0:
        return ""
    if len(acts) == 1:
        return f"all {_duration_pair_text(total)} in one activity"
    visible_count = min(TOP_PROOF_VISIBLE_ACTIVITY_COUNT, len(acts))
    visible_total = sum(act_min for _, act_min, _ in acts[:visible_count])
    visible_share = round((visible_total / total) * 100) if total else 0
    return (
        f"{_top_activity_label(visible_count)} carry "
        f"{_human_duration(visible_total)} of {_human_duration(total)} "
        f"({visible_total} of {total} min, {visible_share}%)"
    )


def _compact_top_carry_text(carry_text: str) -> str:
    return re.sub(
        r"^top \d+ activities carry .* \((?:\d+ of \d+ min, )?(\d+)%\)$",
        r"top carry \1%",
        carry_text,
    )


def _compact_focus_lead_text(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
) -> str:
    leaders, lead_minutes = _leader_names(acts)
    if not leaders or lead_minutes <= 0 or total <= 0:
        return ""

    share = round((lead_minutes / total) * 100)
    names_text = _compact_name_preview(
        leaders,
        max_names=2,
        max_chars=44,
        overflow_singular="activity",
    ) or leaders[0]
    if len(leaders) == 1:
        return f"{names_text} {share}%"
    return f"{names_text} {share}% each"


def _category_focus_row_parts(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
) -> tuple[str, str]:
    lead_summary = _compact_focus_lead_text(acts, total=total)
    carry_text = _category_carry_summary(acts, total=total)
    if not lead_summary and not carry_text:
        return "", ""

    if len(acts) == 1:
        return "", ""

    if lead_summary and carry_text:
        return "Lead Mix", f"{lead_summary} · {_compact_top_carry_text(carry_text)}"

    return "Lead Mix", lead_summary or _compact_top_carry_text(carry_text)


def _category_focus_glance_row(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
) -> str:
    title, detail = _category_focus_row_parts(acts, total=total)
    if not title or not detail:
        return ""
    return f"{title}: {detail}"


def _category_focus_row_html(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
) -> str:
    title, detail = _category_focus_row_parts(acts, total=total)
    if not title or not detail:
        return ""

    return "\n".join(
        [
            '<div class="har-summary-category-focus-row">',
            f'  <span class="har-summary-category-focus-row-title">{html.escape(title)}</span>',
            f'  <span class="har-summary-category-focus-row-detail">{html.escape(detail)}</span>',
            "</div>",
        ]
    )


def _shell_overview_metric_card_html(
    kicker: str,
    title: str,
    copy: str,
    *,
    emphasis: bool = False,
) -> str:
    classes = "har-time-shell-metric"
    if emphasis:
        classes += " is-emphasis"
    return "\n".join(
        [
            f'<div class="{classes}">',
            f'  <div class="har-time-shell-metric-kicker">{html.escape(kicker)}</div>',
            f'  <div class="har-time-shell-metric-title">{html.escape(title)}</div>',
            f'  <div class="har-time-shell-metric-copy">{html.escape(copy)}</div>',
            "</div>",
        ]
    )


def _shell_overview_window_role_card_html(
    *,
    name: str,
    role: str,
    summary: str,
    copy: str,
    primary: bool = False,
) -> str:
    role_class = "har-time-shell-window-role"
    if primary:
        role_class += " is-primary"
    return "\n".join(
        [
            f'<div class="{role_class}">',
            f'  <div class="har-time-shell-window-role-name">{html.escape(name)}</div>',
            f'  <div class="har-time-shell-window-role-title">{html.escape(role)}</div>',
            f'  <div class="har-time-shell-window-role-summary">{html.escape(summary)}</div>',
            f'  <div class="har-time-shell-window-role-copy">{html.escape(copy)}</div>',
            "</div>",
        ]
    )


def _window_role_entries(entries: list[dict], days: int) -> list[dict]:
    if days >= 36500:
        return sorted(
            [entry for entry in entries if entry.get("date")],
            key=lambda entry: (entry["date"], entry.get("time", ""), entry.get("stem", "")),
            reverse=True,
        )
    return window_entries(entries, days)


def _shell_overview_window_role_summary(entries: list[dict], days: int) -> tuple[str, str]:
    window = _window_role_entries(entries, days)
    total_min = sum(entry.get("duration") or 0 for entry in window)
    cats = category_breakdown(window)
    summary = (
        f"{_human_duration(total_min)} · {len(window)} logs · "
        f"{_count_phrase(len(cats), 'category', 'categories')}"
    )
    if not cats:
        return summary, "No logged category yet"

    top_label, top_total, _ = cats[0]
    share = round((top_total / total_min) * 100) if total_min else 0
    return summary, f"Top: {top_label.replace('-', ' ').title()} {_human_duration(top_total)} ({share}%)"


def _shell_overview_lane_rows_html(
    label: str,
    rows: list[dict[str, str]],
    *,
    meaning: bool = False,
) -> str:
    if not rows:
        return ""

    lane_class = "har-time-shell-category-lane"
    if meaning:
        lane_class += " is-meaning"
    if len(rows) == 1:
        lane_class += " is-single-row"

    lines = [
        f'<div class="{lane_class}">',
        f'  <div class="har-time-shell-category-lane-label">{html.escape(label)}</div>',
        '  <div class="har-time-shell-category-lane-rows">',
    ]

    for row in rows:
        title = str(row.get("title", "")).strip()
        duration = str(row.get("duration", "")).strip()
        meta = str(row.get("meta", "")).strip()
        rendered_detail_secondary = ""
        extra_detail_lines: list[str] = []
        if meaning:
            _, _, rendered_detail_secondary = _signature_surface_meaning_parts(row)
        else:
            extra_detail_lines = _row_extra_detail_lines(row)
        row_class = "har-time-shell-category-lane-row"
        if title.startswith("+"):
            row_class += " is-more"
        if rendered_detail_secondary or extra_detail_lines:
            row_class += " has-secondary"
        lines.extend(
            [
                f'    <div class="{row_class}">',
                '      <div class="har-time-shell-category-lane-row-head">',
                f'        <span class="har-time-shell-category-lane-row-title">{html.escape(title)}</span>',
            ]
        )
        if meaning:
            state_badge = _meaning_row_state_badge(row)
            if state_badge:
                lines.append(
                    f'        <span class="har-time-shell-category-lane-row-state">{html.escape(state_badge)}</span>'
                )
        lines.append("      </div>")

        if meaning:
            _, detail, detail_secondary = _signature_surface_meaning_parts(row)
            if detail:
                lines.append(
                    f'      <div class="har-time-shell-category-lane-row-detail">{html.escape(detail)}</div>'
                )
            if detail_secondary:
                lines.append(
                    f'      <div class="har-time-shell-category-lane-row-detail-secondary">{html.escape(detail_secondary)}</div>'
                )
        else:
            if duration:
                lines.append(
                    f'      <div class="har-time-shell-category-lane-row-duration">{html.escape(duration)}</div>'
                )
            if meta:
                lines.append(
                    f'      <div class="har-time-shell-category-lane-row-detail">{html.escape(meta)}</div>'
                )
            for extra_detail in extra_detail_lines:
                lines.append(
                    f'      <div class="har-time-shell-category-lane-row-detail-secondary">{html.escape(extra_detail)}</div>'
                )
        lines.append("    </div>")

    lines.extend(
        [
            "  </div>",
            "</div>",
        ]
    )
    return "\n".join(lines)


def _shell_overview_time_rows(
    acts: list[tuple[str, int, list[dict]]],
    *,
    visible_count: int,
    time_overflow_max_chars: int,
    time_overflow_title_max_chars: int,
) -> list[dict[str, Any]]:
    return _category_time_scan_rows(
        acts,
        visible_count=visible_count,
        overflow_max_chars=time_overflow_max_chars,
        overflow_title_max_chars=time_overflow_title_max_chars,
        compact_overflow_title=True,
        overflow_preview_items=TOP_SHELL_OVERFLOW_PREVIEW_COUNT,
    )


def _shell_overview_meaning_rows(
    acts: list[tuple[str, int, list[dict]]],
    *,
    visible_count: int,
    meaning_max_chars: int,
    meaning_overflow_max_chars: int,
    meaning_overflow_title_max_chars: int,
) -> list[dict[str, Any]]:
    meaning_rows = _category_time_scan_meaning_rows(
        acts,
        max_items=visible_count,
        max_chars=meaning_max_chars,
        overflow_max_chars=meaning_overflow_max_chars,
        overflow_preview_items=TOP_SHELL_OVERFLOW_PREVIEW_COUNT,
        overflow_title_max_chars=meaning_overflow_title_max_chars,
        compact_overflow_title=True,
    )
    rendered_rows: list[dict[str, Any]] = []
    for meaning_row in meaning_rows:
        state_badge = _meaning_row_state_badge(meaning_row)
        _, detail, _ = _signature_surface_meaning_parts(meaning_row)
        detail_lines = _meaning_row_extra_detail_lines(
            meaning_row,
            signature_surface=True,
        )
        rendered_rows.append(
            {
                "title": str(meaning_row.get("title", "")).strip(),
                "state_badge": state_badge,
                "detail": detail,
                "detail_secondary": detail_lines[0] if detail_lines else "",
                "detail_tertiary": detail_lines[1] if len(detail_lines) > 1 else "",
                "detail_quaternary": detail_lines[2] if len(detail_lines) > 2 else "",
            }
        )
    return rendered_rows


def _shell_overview_signature_rows_html(
    label: str,
    rows: list[dict[str, Any]],
) -> str:
    if not rows:
        return ""

    lines = [
        '<div class="har-time-shell-category-strip-pill-breakdown">',
        f'  <span class="har-time-shell-category-strip-pill-breakdown-label">{html.escape(label)}</span>',
        '  <div class="har-time-shell-category-strip-pill-breakdown-rows">',
    ]
    for row in rows:
        title = str(row.get("title", "")).strip()
        duration = str(row.get("duration", "")).strip()
        meta = str(row.get("meta", "")).strip()
        state_badge = str(row.get("state_badge", "")).strip()
        detail = str(row.get("detail", "")).strip()
        if duration and meta and detail == f"{duration} · {meta}":
            detail = ""
        row_class = "har-time-shell-category-strip-pill-breakdown-row"
        if title.startswith("+"):
            row_class += " is-more"

        lines.extend(
            [
                f'    <div class="{row_class}">',
                '      <div class="har-time-shell-category-strip-pill-breakdown-row-head">',
                '        <span class="har-time-shell-category-strip-pill-breakdown-row-head-main">',
                f'          <span class="har-time-shell-category-strip-pill-breakdown-row-title">{html.escape(title)}</span>',
            ]
        )
        if state_badge:
            lines.append(
                f'          <span class="har-time-shell-category-strip-pill-breakdown-row-state">{html.escape(state_badge)}</span>'
            )
        lines.append("        </span>")
        if duration or meta:
            lines.extend(
                [
                    '        <span class="har-time-shell-category-strip-pill-breakdown-row-time">',
                    (
                        f'          <span class="har-time-shell-category-strip-pill-breakdown-row-duration">{html.escape(duration)}</span>'
                        if duration
                        else ""
                    ),
                    (
                        f'          <span class="har-time-shell-category-strip-pill-breakdown-row-meta">{html.escape(meta)}</span>'
                        if meta
                        else ""
                    ),
                    "        </span>",
                ]
            )
        lines.append("      </div>")
        if detail:
            lines.append(
                f'      <div class="har-time-shell-category-strip-pill-breakdown-row-detail">{html.escape(detail)}</div>'
            )
        for key in ("detail_secondary", "detail_tertiary", "detail_quaternary"):
            extra = str(row.get(key, "")).strip()
            if extra:
                lines.append(
                    f'      <div class="har-time-shell-category-strip-pill-breakdown-row-detail-secondary">{html.escape(extra)}</div>'
                )
        lines.append("    </div>")

    lines.extend(
        [
            "  </div>",
            "</div>",
        ]
    )
    return "\n".join(lines)


def _shell_overview_share_rail_copy(
    acts: list[tuple[str, int, list[dict]]],
    *,
    max_items: int,
) -> str:
    if len(acts) <= 1:
        return "100% of category"
    return "Share of category total"


def _shell_overview_share_pill_title(
    row: dict[str, str],
    *,
    grouped_count: int | None = None,
) -> str:
    rank_class = str(row.get("rank_class", "")).strip()
    if rank_class == "is-more" and grouped_count:
        return f"+{grouped_count} more"
    return str(row.get("title", "")).strip()


def _shell_overview_share_pill_detail(row: dict[str, str]) -> str:
    detail = str(row.get("detail", "")).strip()
    if not detail:
        return ""
    parts = [part.strip() for part in detail.split(" · ") if part.strip()]
    for part in parts:
        if "% of category" in part:
            return part.replace(" of category", "")
    return parts[0] if parts else ""


def _shell_overview_share_rail_html(
    acts: list[tuple[str, int, list[dict]]],
    *,
    total: int,
    max_items: int = 3,
) -> str:
    if not acts or total <= 0:
        return ""

    share_rows = _category_chip_share_rows(acts, total=total, max_items=max_items)
    if not share_rows:
        return ""

    meter_html = _category_chip_meter_html(acts, total=total, max_items=max_items)
    single_activity = len(share_rows) == 1 and len(acts) == 1
    grouped_count = max(0, len(acts) - max_items)
    lines = [
        (
            '<div class="har-time-shell-category-share-rail is-single-activity">'
            if single_activity
            else '<div class="har-time-shell-category-share-rail">'
        ),
        '  <div class="har-time-shell-category-share-rail-head">',
        '    <span class="har-time-shell-category-share-rail-label">Activity Split</span>',
        (
            f'    <span class="har-time-shell-category-share-rail-copy">'
            f'{html.escape(_shell_overview_share_rail_copy(acts, max_items=max_items))}</span>'
        ),
    ]
    lines.append("  </div>")
    if meter_html and not single_activity:
        lines.append(meter_html.replace("har-category-chip-meter", "har-time-shell-category-meter"))
    if single_activity:
        row = share_rows[0]
        detail = str(row.get("detail", "")).strip()
        meta = detail
        if " · " in detail:
            _, meta = detail.split(" · ", 1)
        meta = meta.strip() or detail
        lines.extend(
            [
                '  <div class="har-time-shell-category-share-rail-single">',
                f'    <span class="har-time-shell-category-share-rail-single-detail">{html.escape(meta)}</span>',
                "  </div>",
                "</div>",
            ]
        )
        return "\n".join(lines)

    lines.append('  <div class="har-time-shell-category-share-rail-list">')
    for row in share_rows:
        title = _shell_overview_share_pill_title(row, grouped_count=grouped_count)
        detail = _shell_overview_share_pill_detail(row)
        rank_class = str(row.get("rank_class", "")).strip()
        pill_class = "har-time-shell-category-share-pill is-compact"
        if rank_class:
            pill_class += f" {rank_class}"
        lines.extend(
            [
                f'    <div class="{pill_class}">',
                f'      <span class="har-time-shell-category-share-pill-title">{html.escape(title)}</span>',
                f'      <span class="har-time-shell-category-share-pill-detail">{html.escape(detail)}</span>',
                "    </div>",
            ]
        )
    lines.extend(
        [
            "  </div>",
            "</div>",
        ]
    )
    return "\n".join(lines)


def _shell_overview_category_row_html(
    label: str,
    total: int,
    total_range_minutes: int,
    acts: list[tuple[str, int, list[dict]]],
    *,
    rank: int,
    total_categories: int,
) -> str:
    share = round((total / total_range_minutes) * 100) if total_range_minutes else 0
    activity_count = len(acts)
    log_count = sum(len(act_entries) for _, _, act_entries in acts)
    meta_items = _compact_category_meta_items(
        activity_count,
        log_count,
        visible_count=2,
    )
    row_class = "har-time-shell-category-row"
    if activity_count == 1:
        row_class += " is-single-activity"
    time_rows = _shell_overview_time_rows(
        acts,
        visible_count=2,
        time_overflow_max_chars=92,
        time_overflow_title_max_chars=84,
    )
    meaning_rows = _shell_overview_meaning_rows(
        acts,
        visible_count=2,
        meaning_max_chars=104,
        meaning_overflow_max_chars=132,
        meaning_overflow_title_max_chars=84,
    )
    share_rail_html = ""
    if activity_count > 1:
        share_rail_html = _shell_overview_share_rail_html(
            acts,
            total=total,
            max_items=TOP_SHELL_VISIBLE_ACTIVITY_COUNT,
        )
    return "\n".join(
        [
            f'<div class="{row_class}">',
            '  <div class="har-time-shell-category-row-head">',
            '    <span class="har-time-shell-category-row-head-main">',
            f'      <span class="har-time-shell-category-row-rank">{rank} of {total_categories}</span>',
            f'      <span class="har-time-shell-category-row-name">{html.escape(label.replace("-", " ").title())}</span>',
            "    </span>",
            '    <span class="har-time-shell-category-row-total">',
            f'      <span class="har-time-shell-category-row-total-primary">{html.escape(_human_duration(total))}</span>',
            f'      <span class="har-time-shell-category-row-total-secondary">{total} min · {share}% of week</span>',
            "    </span>",
            "  </div>",
            (
                f'  <div class="har-time-shell-category-row-meta">'
                f'{html.escape(" · ".join(meta_items))}</div>'
            ),
            _shell_overview_signature_rows_html("Activity Time", time_rows),
            _shell_overview_signature_rows_html("Activity Read", meaning_rows),
            share_rail_html,
            "</div>",
        ]
    )


def _shell_overview_category_totals_strip_html(
    cats: list[tuple[str, int, list[tuple[str, int, list[dict]]]]],
    *,
    total_range_minutes: int,
    max_items: int | None = TOP_SHELL_VISIBLE_CATEGORY_COUNT,
) -> str:
    if not cats or total_range_minutes <= 0:
        return ""

    visible_categories = cats if max_items is None else cats[:max_items]

    hidden = max(0, len(cats) - len(visible_categories))
    copy = "Ranked by week total with visible activity time and activity read. Split rail and deep read are proof."
    if hidden:
        noun = "category continues" if hidden == 1 else "categories continue"
        copy += f" {hidden} more {noun} in the deep read."

    lines = [
        '<div class="har-time-shell-category-strip">',
        '  <div class="har-time-shell-category-strip-head">',
        '    <span class="har-time-shell-category-strip-label">Category Totals This Week</span>',
        f'    <span class="har-time-shell-category-strip-copy">{html.escape(copy)}</span>',
        "  </div>",
        '  <div class="har-time-shell-category-strip-pills">',
    ]

    for rank, (label, total, acts) in enumerate(visible_categories, start=1):
        share = round((total / total_range_minutes) * 100) if total_range_minutes else 0
        activity_count = len(acts)
        log_count = sum(len(act_entries) for _, _, act_entries in acts)
        pill_class = "har-time-shell-category-strip-pill"
        if activity_count == 1:
            pill_class += " is-single-activity"
        meta_html = _meta_pills_html(
            "har-time-shell-category-strip-pill-meta",
            [
                _count_phrase(log_count, "log"),
                _top_surface_visibility_meta_text(activity_count),
                f"{share}% of week",
            ],
        )
        time_rows = _shell_overview_time_rows(
            acts,
            visible_count=TOP_SHELL_VISIBLE_ACTIVITY_COUNT,
            time_overflow_max_chars=68,
            time_overflow_title_max_chars=84,
        )
        meaning_rows = _shell_overview_meaning_rows(
            acts,
            visible_count=TOP_SHELL_VISIBLE_ACTIVITY_COUNT,
            meaning_max_chars=116,
            meaning_overflow_max_chars=148,
            meaning_overflow_title_max_chars=84,
        )
        share_rail_html = ""
        if activity_count > 1:
            pill_class += " has-share-rail"
            share_rail_html = _shell_overview_share_rail_html(
                acts,
                total=total,
                max_items=TOP_SHELL_VISIBLE_ACTIVITY_COUNT,
            )
        lines.extend(
            [
                f'    <div class="{pill_class}">',
                '      <div class="har-time-shell-category-strip-pill-head">',
                f'        <span class="har-time-shell-category-strip-pill-rank">{rank}</span>',
                f'        <span class="har-time-shell-category-strip-pill-name">{html.escape(label.replace("-", " ").title())}</span>',
                f'        <span class="har-time-shell-category-strip-pill-total">{html.escape(_human_duration(total))}</span>',
                "      </div>",
                f"      {meta_html}",
            ]
        )
        if time_rows or meaning_rows:
            lines.append('      <div class="har-time-shell-category-strip-pill-lanes">')
            if time_rows:
                lines.append(
                    _shell_overview_signature_rows_html(
                        "Activity Time",
                        time_rows,
                    )
                )
            if meaning_rows:
                lines.append(
                    _shell_overview_signature_rows_html(
                        "Activity Read",
                        meaning_rows,
                    )
                )
            if share_rail_html:
                lines.append(share_rail_html)
            lines.append("      </div>")
        lines.append("    </div>")

    lines.extend(
        [
            "  </div>",
            "</div>",
        ]
    )
    return "\n".join(lines)


def _shell_overview_category_scan_html(
    cats: list[tuple[str, int, list[tuple[str, int, list[dict]]]]],
    *,
    total_range_minutes: int,
    max_items: int = 2,
) -> str:
    if not cats or total_range_minutes <= 0:
        return ""

    visible = cats[:max_items]
    hidden = max(0, len(cats) - len(visible))
    copy = (
        "Stay in the weekly shell here. Open the deep read only when a split still needs proof."
    )
    if hidden:
        noun = "category continues" if hidden == 1 else "categories continue"
        copy += f" {hidden} more {noun} below."

    lines = [
        '<div class="har-time-shell-overview-category-scan">',
        '  <div class="har-time-shell-category-strip-head">',
        '    <span class="har-time-shell-overview-category-scan-label">Top Categories This Week</span>',
        f'    <span class="har-time-shell-category-strip-copy">{html.escape(copy)}</span>',
        "  </div>",
        '  <div class="har-time-shell-overview-category-scan-rows">',
    ]
    total_categories = len(cats)
    for rank, (label, total, acts) in enumerate(visible, start=1):
        lines.append(
            _shell_overview_category_row_html(
                label,
                total,
                total_range_minutes,
                acts,
                rank=rank,
                total_categories=total_categories,
            )
        )
    lines.extend(
        [
            "  </div>",
            "</div>",
        ]
    )
    return "\n".join(lines)


def render_time_shell_overview(entries: list[dict]) -> str:
    window = window_entries(entries, 7)
    total_min = sum(e["duration"] or 0 for e in window)
    cats = category_breakdown(window)
    bounds = _window_bounds(entries, 7)
    active_days = len({e["date"] for e in window if e.get("date")})
    selected_days = 7
    top_category = cats[0] if cats else None

    coverage_copy = f"{active_days} of {selected_days} days with logs"
    date_values = [e["date"] for e in window if e.get("date")]
    if date_values:
        unique_dates = sorted({d for d in date_values if d})
        if len(unique_dates) == 1:
            coverage_copy += f" · logged {unique_dates[0]}"
        else:
            coverage_copy += f" · logged {unique_dates[0]} to {unique_dates[-1]}"
    log_coverage_title = _window_capture_line(window).replace("- log coverage: ", "", 1)

    top_category_title = "No logged category yet"
    top_category_copy = "Log time in the selected week to establish the first category baseline."
    if top_category:
        label, total, acts = top_category
        top_category_title = f"{label.replace('-', ' ').title()} · {_human_duration(total)}"
        log_count = sum(len(act_entries) for _, _, act_entries in acts)
        top_category_copy = (
            f"{_count_phrase(log_count, 'log')} · "
            f"{_top_surface_visibility_meta_text(len(acts))}"
        )

    lines = [
        "---",
        "type: derived",
        "derived_from: HAR action entries",
        "---",
        "",
        "# HAR Time & Stats Shell Overview",
        "",
        '<div class="har-time-shell-overview is-live">',
        '  <div class="har-time-shell-overview-kicker">Signature Weekly Review</div>',
        (
            f'  <div class="har-time-shell-overview-title">{html.escape(_human_duration(total_min))} this week · {html.escape(_count_phrase(len(cats), "category", "categories"))}</div>'
        ),
        (
            f'  <div class="har-time-shell-overview-copy">Week: {html.escape(bounds[0].isoformat())} to {html.escape(bounds[1].isoformat())}. '
            'Read week total, coverage, log coverage, then ranked category totals. Open deep read only for proof.</div>'
            if bounds
            else '  <div class="har-time-shell-overview-copy">Read week total, coverage, log coverage, then ranked category totals. Open deep read only for proof.</div>'
        ),
        '  <div class="har-time-shell-overview-metrics">',
        _shell_overview_metric_card_html(
            "Week Total",
            f"{_human_duration(total_min)} · {len(window)} logs",
            f"{_count_phrase(len(cats), 'category', 'categories')} with time",
            emphasis=True,
        ),
        _shell_overview_metric_card_html(
            "Coverage",
            coverage_copy,
            "Selected-range day coverage for this week.",
        ),
        _shell_overview_metric_card_html(
            "Log Coverage",
            log_coverage_title,
            "Structured, notes-only, and no-stats mix for this week.",
        ),
        _shell_overview_metric_card_html(
            "Top Category",
            top_category_title,
            top_category_copy,
        ),
        "  </div>",
    ]
    strip_html = _shell_overview_category_totals_strip_html(
        cats,
        total_range_minutes=total_min,
        max_items=TOP_SHELL_VISIBLE_CATEGORY_COUNT,
    )
    if strip_html:
        lines.append(strip_html)
    lines.append("</div>")
    return "\n".join(lines).rstrip() + "\n"


def _category_time_breakdown_line(acts: list[tuple[str, int, list[dict]]]) -> str:
    time_stack_text = _category_time_stack_line(acts, visible_count=3)
    if not time_stack_text.endswith("no activity time yet"):
        time_stack_text = time_stack_text.replace("time stack: ", "", 1)
    return f"- time breakdown: {time_stack_text}" if time_stack_text else "- time breakdown: no activity time yet"


def _category_time_breakdown_lines(acts: list[tuple[str, int, list[dict]]]) -> list[str]:
    return _category_time_stack_lines(acts, label="time breakdown", visible_count=3)


def _category_callout_title(
    label: str,
    total: int,
    share: int,
    acts: list[tuple[str, int, list[dict]]],
) -> str:
    activity_count = len(acts)
    log_count = sum(len(act_entries) for _, _, act_entries in acts)
    title = (
        f"{label} — {_duration_summary_text(total, share)}"
        f" · {_category_visibility_meta_text(activity_count, log_count, visible_count=TOP_PROOF_VISIBLE_ACTIVITY_COUNT)}"
    )
    focus_title, focus_detail = _category_focus_row_parts(acts, total=total)
    if focus_title and focus_detail:
        title += f" · {focus_title}: {focus_detail}"
    return title


def category_breakdown(entries: list[dict]) -> list[tuple[str, int, list[tuple[str, int, list[dict]]]]]:
    buckets: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for e in entries:
        if e["duration"] is None:
            continue
        cat = e.get("category", "unknown")
        act = activity_family(e["activity"])
        buckets[cat][act].append(e)

    result = []
    for cat in sorted(buckets, key=lambda c: -sum(e["duration"] or 0 for entries in buckets[c].values() for e in entries)):
        act_list = []
        for act in sorted(
            buckets[cat],
            key=lambda a: (
                -sum(e["duration"] or 0 for e in buckets[cat][a]),
                a,
            ),
        ):
            act_list.append((act, sum(e["duration"] or 0 for e in buckets[cat][act]), buckets[cat][act]))
        total = sum(d for _, d, _ in act_list)
        result.append((cat, total, act_list))
    return result


def render_time_window(entries: list[dict], title: str, days: int, chart_file: str) -> str:
    window = window_entries(entries, days)
    total_min = sum(e["duration"] or 0 for e in window)
    cats = category_breakdown(window)
    bounds = _window_bounds(entries, days)
    compact_detail = days > 7
    top_surface_visible_count = 3 if days <= 7 else 2

    lines = [
        "---",
        "type: derived",
        "derived_from: HAR action entries",
        f"range_days: {days}",
        "---",
        "",
        f"# {title}",
        "",
        f"- total logged: {_human_duration(total_min)} ({total_min} min) · {len(window)} entries",
        "",
        "## Main Review Surface",
        "",
    ]

    category_count = len(cats)
    top_category = cats[0] if cats else None
    date_values = [e["date"] for e in window if e.get("date")]
    active_days = len({e["date"] for e in window if e.get("date")})
    window_read = [
        f"- **{total_min} min** across **{len(window)}** logged entries",
        f"- human-time total: **{_human_duration(total_min)}**",
        f"- **{category_count}** {'category' if category_count == 1 else 'categories'} with time in this range",
    ]
    if bounds:
        window_start, window_end = bounds
        if days >= 36500:
            window_read.append(f"- selected window: all logged time through **{window_end.isoformat()}**")
        else:
            window_read.append(f"- selected window: {_format_date_span(window_start, window_end)}")
    if date_values:
        window_read.append(_format_logged_dates_line(date_values))
        window_read.append(f"- active days with logs: **{active_days}**")
        if bounds and days < 36500:
            window_start, window_end = bounds
            selected_days = (window_end - window_start).days + 1
            window_read.append(
                _range_coverage_line(
                    active_days=active_days,
                    selected_days=selected_days,
                )
            )
            empty_days = max(0, selected_days - active_days)
            if empty_days:
                window_read.append(f"- empty days in selected window: **{empty_days}**")
    sparse_week_handoff = _sparse_week_handoff_line(days=days, active_days=active_days)
    if sparse_week_handoff:
        window_read.append(sparse_week_handoff)
    capture_line = _window_capture_line(window)
    window_read.append(capture_line)
    if top_category:
        top_label = top_category[0].replace("-", " ").title()
        window_read.append(f"- biggest category: **{top_label}** at **{_duration_summary_text(top_category[1])}**")
    lines.append(
        _main_review_summary_html(
            total_min=total_min,
            entry_count=len(window),
            category_count=category_count,
            bounds=bounds,
            date_values=date_values,
            active_days=active_days,
            capture_line=capture_line,
            top_category=top_category,
            cats=cats,
            days=days,
            top_surface_visible_count=top_surface_visible_count,
        )
    )
    lines.append("")
    lines.extend(_render_callout("note", "Window Read", window_read))
    lines.append("")
    lines.append(
        _category_glance_surface_html(cats, total_min, days=days, visible_count=top_surface_visible_count)
    )
    lines.append("")
    lines.append(_category_chart_shell_html(chart_file, days=days, cats=cats, total_min=total_min))
    lines.append("")

    for cat, total, acts in cats:
        label = cat.replace("-", " ").title()
        share = round((total / total_min) * 100) if total_min else 0
        callout_body = _category_time_breakdown_lines(acts)
        callout_body.extend(
            [
                _category_review_summary_line(acts),
            ]
        )
        callout_body.extend(
            _category_activity_state_lines(
                acts,
                max_items=top_surface_visible_count,
            )
        )
        for act_name, act_min, act_entries in acts:
            callout_body.extend(
                _activity_review_lines(
                    act_name,
                    act_min,
                    act_entries,
                    category_total=total,
                    compact_detail=compact_detail,
                )
            )
        lines.extend(_render_callout("review", _category_callout_title(label, total, share, acts), callout_body))
        lines.append("")

    # Keep latest actions available in the derived file without letting the
    # `Main Review Surface` embed swallow them into the signature lane.
    lines.append("## Latest Actions")
    for e in window[:8]:
        dur = f"{e['duration']} min" if e["duration"] else "?"
        lines.append(f"- {e['date']} {e['time'] or ''} — [[{e['stem']}|{e['activity']}]] ({dur}, {e['category']})")

    return "\n".join(lines).rstrip() + "\n"


def render_home_summary(entries: list[dict]) -> str:
    window = window_entries(entries, 7)
    total_min = sum(e["duration"] or 0 for e in window)
    cat_totals: Counter[str] = Counter()
    stats_entries = [e for e in window if e["custom_fields"] or "no stats to report" in e["notes"].lower()]

    for e in window:
        if e["duration"]:
            cat_totals[e["category"]] += e["duration"]

    lines = [
        "---",
        "type: derived",
        "derived_from: HAR action entries",
        "---",
        "",
        "# HAR Home Summary",
        "",
        "## This Week So Far",
        f"- entries: {len(window)}",
        f"- total logged minutes: {total_min}",
        "",
        "## This Week Category Mix",
        "![[_derived/har-graphs/har-time-last-7-days-chart.svg]]",
        "",
    ]
    lines.extend(top_lines(sorted(cat_totals.items(), key=lambda x: (-x[1], x[0])), " min"))

    lines.extend(["", "## Latest Activities"])
    for e in window[:5]:
        dur = f"{e['duration']} min" if e["duration"] else "?"
        lines.append(f"- {e['date']} — [[{e['stem']}|{e['activity']}]] ({dur}, {e['category']})")

    lines.extend(["", "## Latest Stats Reported This Week"])
    if not stats_entries:
        lines.append("- none yet")
    else:
        for e in stats_entries[:5]:
            s = stats_summary(e)
            if s:
                lines.append(f"- [[{e['stem']}|{e['activity']}]] ({e['date']}): {s}")
            else:
                lines.append(f"- [[{e['stem']}|{e['activity']}]] ({e['date']})")

    return "\n".join(lines).rstrip() + "\n"


def render_notes_summary(entries: list[dict]) -> tuple[str, str, str]:
    chrono = sorted(
        [e for e in entries if e["has_notes"]],
        key=lambda e: (e["date"], e["time"], e["stem"]),
        reverse=True,
    )
    # Group by activity family, not raw activity name
    by_act_family: dict[str, list[dict]] = defaultdict(list)
    for e in chrono:
        family = activity_family(e["activity"])
        by_act_family[family].append(e)
    
    ranked = sorted(by_act_family.items(), key=lambda x: (-len(x[1]), x[0]))

    lines = [
        "---",
        "type: derived",
        "derived_from: HAR action entries",
        "---",
        "",
        "# HAR General Notes Summary",
        "",
        "## Note Activity Chart",
        "![[_derived/har-graphs/har-general-notes-chart.svg]]",
        "",
        "## Activities Ranked by Note Count",
    ]
    for act, note_entries in ranked:
        journal_slug = act.lower().replace(" ", "-").replace("/", "-")
        journal_path = f"_derived/har-activity-journals/{journal_slug}"
        count = len(note_entries)
        lines.append(f"- [[{journal_path}|{act}]]: {count} notes")

    lines.extend(["", "## Recent Notes"])
    for e in chrono[:10]:
        first = e["notes"].splitlines()[0][:140] if e["notes"] else ""
        lines.extend([
            f"### {e['date']} — [[{e['stem']}|{e['activity']}]]",
            f"> {first}",
            "",
        ])

    summary = "\n".join(lines).rstrip() + "\n"

    # Activity journal index
    idx_lines = [
        "---",
        "type: derived",
        "derived_from: HAR action entries",
        "---",
        "",
        "# HAR Activity Journals",
        "",
        "## Activities",
    ]
    for act, note_entries in ranked:
        journal_slug = act.lower().replace(" ", "-").replace("/", "-")
        journal_path = f"_derived/har-activity-journals/{journal_slug}"
        count = len(note_entries)
        idx_lines.append(f"- [[{journal_path}|{act}]]: {count} note-bearing entries")

    return summary, "\n".join(idx_lines).rstrip() + "\n", chrono


def render_activity_journal(activity_family_name: str, entries: list[dict], chrono_notes: list[dict]) -> str | None:
    """Render a single activity journal page with all notes in chronological order."""
    matching = [e for e in chrono_notes 
                if activity_family(e["activity"]) == activity_family_name 
                and e["has_notes"]]
    if not matching:
        return None
    
    # Sort chronologically ascending
    matching_asc = sorted(matching, key=lambda e: (e["date"], e["time"], e["stem"]))
    
    category = matching_asc[0].get("category", "unknown")
    journal_slug = activity_family_name.lower().replace(" ", "-").replace("/", "-")
    
    lines = [
        "---",
        "type: derived",
        "derived_from: HAR action entries",
        f"activity: {activity_family_name}",
        "---",
        "",
        f"# {activity_family_name}",
        "",
        f"- category: {category}",
        f"- total entries with notes: {len(matching)}",
        f"- date range: {matching_asc[0]['date']} — {matching_asc[-1]['date']}",
        "",
    ]
    
    for e in matching_asc:
        dur = f"{e['duration']} min" if e["duration"] else "? min"
        lines.extend([
            f"### {e['date']} — {e['time'] or ''}",
            f"- duration: {dur}",
            f"- entry: [[{e['stem']}|{e['activity']}]]",
            "",
        ])
        if e["notes"]:
            lines.append("> ")
            for note_line in e["notes"].splitlines():
                lines.append(f"> {note_line}")
            lines.append("")
    
    return "\n".join(lines).rstrip() + "\n"


def render_general_notes_chart(entries: list[dict], config: dict) -> str:
    """Render a chart showing note count per activity family."""
    by_family: Counter[str] = Counter()
    for e in entries:
        if e["has_notes"]:
            by_family[activity_family(e["activity"])] += 1
    
    if not by_family:
        return render_chart("HAR General Notes - Note Count by Activity", entries, config)
    
    # Create synthetic entries for chart rendering
    # Each activity family gets one pseudo-entry with duration = note count
    synth_entries = []
    for fam, count in by_family.items():
        synth_entries.append({
            "duration": count * 10,  # Scale for bar visibility
            "category": "notes",
            "activity": fam,
            "date": "",
            "time": "",
            "stem": "",
        })
    
    return render_chart("HAR General Notes - Note Count by Activity", synth_entries, config, force_category_colors=True)


def render_chart(title: str, entries: list[dict[str, Any]], config: dict[str, Any], force_category_colors: bool = False) -> str:
    """Render an SVG bar chart.

    If force_category_colors is True, use the notes-specific chart config.
    """
    if not entries:
        # Empty chart
        width, h = 1080, 200
        svg = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{h}" viewBox="0 0 {width} {h}" role="img" aria-label="{title}">',
            f'<rect x="0" y="0" width="{width}" height="{h}" fill="#fbfaf7" />',
            f'<text x="42" y="60" font-size="24" font-weight="700" fill="#1f2937" font-family="Helvetica, Arial, sans-serif">{html.escape(title)}</text>',
            f'<text x="42" y="100" font-size="16" fill="#6b7280" font-family="Helvetica, Arial, sans-serif">No data</text>',
            "</svg>",
        ]
        return "\n".join(svg)
    
    if force_category_colors:
        # For notes chart, use a fixed set of colors for individual activities
        activity_colors = [
            "#4A9B7F", "#4A7AB5", "#8B5CF6", "#D4A843", "#E07840",
            "#9B59B6", "#2ECC71", "#E74C3C", "#3498DB", "#F39C12",
            "#1ABC9C", "#E67E22", "#2980B9", "#27AE60", "#C0392B",
        ]
        cats = sorted(set(e["activity"] for e in entries), key=lambda a: -sum(
            e.get("duration", 0) or 0 for e in entries if e["activity"] == a
        ))
        width, left = 1080, 220
        bar_w = 660
        row_h = 28
        gap = 56
        max_total = max((e.get("duration") or 0) for e in entries)
        h = 130 + max(1, len(cats)) * gap
        top = 90
        
        svg = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{h}" viewBox="0 0 {width} {h}" role="img" aria-label="{title}">',
            f'<rect x="0" y="0" width="{width}" height="{h}" fill="#fbfaf7" />',
            f'<text x="42" y="40" font-size="24" font-weight="700" fill="#1f2937" font-family="Helvetica, Arial, sans-serif">{html.escape(title)}</text>',
        ]
        
        # Tick marks based on note counts
        max_notes = max_total // 10  # Scale back
        for tick in range(5):
            x = left + bar_w * tick / 4
            val = round(max_notes * tick / 4) if max_notes else 0
            svg.append(f'<line x1="{x}" y1="{top}" x2="{x}" y2="{h - 30}" stroke="#e5e7eb" stroke-width="1"/>')
            svg.append(f'<text x="{x}" y="{top - 8}" font-size="11" text-anchor="middle" fill="#6b7280" font-family="Helvetica, Arial, sans-serif">{val}</text>')
        
        for i, activity in enumerate(cats):
            y = top + i * gap
            total = max((e.get("duration") or 0) for e in entries if e["activity"] == activity)
            count = round(total / 10)
            color = activity_colors[i % len(activity_colors)]
            
            svg.append(f'<text x="42" y="{y + 14}" font-size="15" font-weight="700" fill="#111827" font-family="Helvetica, Arial, sans-serif">{html.escape(activity)}</text>')
            svg.append(f'<text x="{left + bar_w + 16}" y="{y + 16}" font-size="13" font-weight="700" fill="#374151" font-family="Helvetica, Arial, sans-serif">{count}</text>')
            
            sw = max(1.0, (total / (max_total or 1)) * bar_w)
            svg.append(f'<g><title>{html.escape(activity)} · {count} notes</title><rect x="{left}" y="{y}" width="{round(sw, 2)}" height="{row_h}" rx="8" fill="{color}" /></g>')
        
        svg.append("</svg>")
        return "\n".join(svg)
    
    # Standard time chart
    cat_totals: dict[str, int] = defaultdict(int)
    act_totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    cat_log_counts: dict[str, int] = defaultdict(int)
    for e in entries:
        if e["duration"] is None or not e.get("category"):
            continue
        cat_totals[e["category"]] += e["duration"]
        act_totals[e["category"]][activity_family(e["activity"])] += e["duration"]
        cat_log_counts[e["category"]] += 1

    cats = sorted(cat_totals, key=lambda c: (-cat_totals[c], c))
    width = 1080
    left = 320
    top = 90
    bar_w = 560
    row_h = 28
    gap = 82
    max_total = max(cat_totals.values(), default=1)
    all_total = sum(cat_totals.values()) or 1
    h = 130 + max(1, len(cats)) * gap

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{h}" viewBox="0 0 {width} {h}" role="img" aria-label="{title}">',
        f'<rect x="0" y="0" width="{width}" height="{h}" fill="#fbfaf7" />',
        f'<text x="42" y="40" font-size="24" font-weight="700" fill="#1f2937" font-family="Helvetica, Arial, sans-serif">{html.escape(title)}</text>',
    ]

    for tick in range(5):
        x = left + bar_w * tick / 4
        val = round(max_total * tick / 4)
        svg.append(f'<line x1="{x}" y1="{top}" x2="{x}" y2="{h - 30}" stroke="#e5e7eb" stroke-width="1"/>')
        svg.append(f'<text x="{x}" y="{top - 8}" font-size="11" text-anchor="middle" fill="#6b7280" font-family="Helvetica, Arial, sans-serif">{val}</text>')

    for i, cat in enumerate(cats):
        y = top + i * gap
        total = cat_totals[cat]
        acts = sorted(act_totals[cat], key=lambda a: (-act_totals[cat][a], a))
        base = str(config.get(cat, {}).get("color", "#6b7280"))
        label = cat.replace("-", " ").title()
        lead_hint = ""
        top_mix_hint = ""
        if acts:
            chart_acts = [(act_name, act_totals[cat][act_name], []) for act_name in acts]
            lead_hint = _format_lead_summary(chart_acts, total=total, prefix="lead")
            top_mix_hint = _top_mix_summary(chart_acts, total=total)
        share_of_all = round((total / all_total) * 100) if all_total else 0
        activity_count = len(acts)
        log_count = cat_log_counts[cat]

        svg.append(
            f'<text x="42" y="{y + 14}" font-size="15" font-weight="700" fill="#111827" font-family="Helvetica, Arial, sans-serif">'
            f"{html.escape(label)} · {_human_duration(total)}"
            "</text>"
        )
        if lead_hint:
            svg.append(f'<text x="42" y="{y + 32}" font-size="12" fill="#6b7280" font-family="Helvetica, Arial, sans-serif">{html.escape(lead_hint)}</text>')
        if top_mix_hint:
            svg.append(f'<text x="42" y="{y + 48}" font-size="12" fill="#6b7280" font-family="Helvetica, Arial, sans-serif">{html.escape(top_mix_hint)}</text>')
        svg.append(
            f'<text x="{left + bar_w + 16}" y="{y + 12}" font-size="13" font-weight="700" fill="#374151" font-family="Helvetica, Arial, sans-serif">'
            f"{share_of_all}% of logged time"
            "</text>"
        )
        svg.append(
            f'<text x="{left + bar_w + 16}" y="{y + 28}" font-size="12" fill="#6b7280" font-family="Helvetica, Arial, sans-serif">'
            f'{activity_count} {"activity" if activity_count == 1 else "activities"} · {log_count} {"log" if log_count == 1 else "logs"}'
            "</text>"
        )
        svg.append(f'<rect x="{left}" y="{y}" width="{bar_w}" height="{row_h}" rx="8" fill="#ece9e3" />')

        cx = left
        sw = max(1.0, (total / max_total) * bar_w)
        for act in acts:
            m = act_totals[cat][act]
            seg = max(1.0, sw * m / total)
            color = mix_hex(base, (255, 255, 255), 0.08 + 0.38 * acts.index(act) / max(1, len(acts) - 1))
            svg.append(f'<g><title>{label} · {html.escape(act)} · {m} min</title><rect x="{round(cx, 2)}" y="{y}" width="{round(seg, 2)}" height="{row_h}" rx="8" fill="{color}" /></g>')
            cx += seg

    svg.append("</svg>")
    return "\n".join(svg)


def _read_wiki_preview(path: Path) -> str:
    """Read a HAR wiki file and return its BLUF (first paragraph of Current answer)."""
    if not path.exists():
        return "Placeholder — wiki not yet created"
    try:
        text = path.read_text(encoding="utf-8")
        # Extract content after "**Current answer:**" (bold format) or "## Current answer" (heading)
        in_answer = False
        lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if "Current answer" in stripped and (stripped.startswith("#") or stripped.startswith("**")):
                # Extract text after the **Current answer:**
                if "**Current answer:**" in stripped:
                    after = stripped.split("**Current answer:**", 1)[1].strip()
                    if after:
                        lines.append(after)
                in_answer = True
                continue
            if in_answer:
                if stripped.startswith("#") and "Current answer" not in stripped:
                    break
                if stripped.startswith("*") and "Updated" in stripped:
                    break
                if stripped:
                    lines.append(stripped)
        body = " ".join(lines).strip()
        if len(body) > 300:
            body = body[:300] + "…"
        return body or "See wiki for full routine"
    except Exception:
        return "See wiki for full routine"


def _find_wiki_content(slug: str) -> dict | None:
    """Try to find a HAR wiki matching this activity slug and return its content."""
    wiki_dir = REPO_ROOT / "wiki" / "digital-products" / "har"
    if not wiki_dir.exists():
        return None
    candidates = [
        wiki_dir / f"what-is-calebs-{slug}.md",
        wiki_dir / f"what-is-{slug}.md",
    ]
    for wiki_path in candidates:
        if not wiki_path.exists():
            continue
        try:
            text = wiki_path.read_text(encoding="utf-8")
            frontmatter: dict[str, Any] = {}
            body_parts = text
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    for line in parts[1].strip().splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            frontmatter[k.strip()] = v.strip().strip('"')
                    body_parts = parts[2]
            sections: dict[str, str] = {}
            cur = None
            cur_lines: list[str] = []
            for line in body_parts.splitlines():
                if line.startswith("## "):
                    if cur and cur_lines:
                        sections[cur] = "\n".join(cur_lines).strip()
                    cur = line[3:].strip()
                    cur_lines = []
                elif cur:
                    cur_lines.append(line)
            if cur and cur_lines:
                sections[cur] = "\n".join(cur_lines).strip()
            current_answer = sections.get("Current answer", "")
            if not current_answer:
                for line in body_parts.splitlines():
                    if "**Current answer:**" in line:
                        current_answer = line.split("**Current answer:**", 1)[1].strip()
                        break
            sections_list = []
            for heading, body in sections.items():
                truncated = body[:600] + "…" if len(body) > 600 else body
                sections_list.append({"heading": heading, "body": truncated})
            return {
                "exists": True,
                "title": frontmatter.get("title", ""),
                "stage": frontmatter.get("stage", "seedling"),
                "updated": frontmatter.get("updated", ""),
                "current_answer": current_answer[:500] if current_answer else "",
                "sections": sections_list,
                "has_wiki": True,
            }
        except Exception:
            return None
    return None


def _generate_wiki_data(entries: list[dict], config: dict) -> dict:
    """Generate wiki data for the web dashboard.

    Returns:
        dict with:
          - by_category: list of {category, color, activities: [{name, slug, total_minutes, entry_count}]}
          - by_activity: dict of slug -> {activity, category, slug, total_minutes, entry_count,
                            avg_duration, best_week, streak, consistency, recent_entries, created, updated}
    """
    # Group entries by activity family
    buckets: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for e in entries:
        if e["duration"] is None:
            continue
        cat = e.get("category", "unknown")
        act = activity_family(e["activity"])
        buckets[cat][act].append(e)

    # Collect all dates for week calculations
    all_dates_sorted = sorted(set(
        date.fromisoformat(e["date"]) for e in entries if e["date"]
    ))

    by_category = []
    by_activity = {}

    for cat in sorted(buckets, key=lambda c: -sum(
        e["duration"] or 0 for entries in buckets[c].values() for e in entries
    )):
        acts = []
        for act_name in sorted(buckets[cat], key=lambda a: -sum(
            e["duration"] or 0 for e in buckets[cat][a]
        )):
            act_entries = buckets[cat][act_name]
            slug = act_name.lower().replace(" ", "-").replace("/", "-")

            # Basic stats
            durations = [e["duration"] or 0 for e in act_entries]
            total_minutes = sum(durations)
            entry_count = len(act_entries)
            avg_duration = round(total_minutes / entry_count) if entry_count else 0

            # Date range
            entry_dates = sorted(set(
                date.fromisoformat(e["date"]) for e in act_entries if e["date"]
            ))
            first_entry = str(entry_dates[0]) if entry_dates else ""
            last_entry = str(entry_dates[-1]) if entry_dates else ""

            # Weekly stats: group entries by ISO week
            weekly: dict[str, int] = defaultdict(int)
            for e in act_entries:
                if e["date"]:
                    d = date.fromisoformat(e["date"])
                    week_key = d.isocalendar()[:2]  # (year, week)
                    weekly[f"{week_key[0]}-W{week_key[1]:02d}"] += e["duration"] or 0

            best_week = max(weekly.values()) if weekly else 0

            # Streak: consecutive weeks from last entry backward
            if entry_dates:
                last_d = entry_dates[-1]
                streak = 0
                check = last_d
                while True:
                    week_key = f"{check.isocalendar()[0]}-W{check.isocalendar()[1]:02d}"
                    if week_key in weekly:
                        streak += 1
                        check -= timedelta(days=7)
                    else:
                        break
            else:
                streak = 0

            # Consistency: % of weeks since first entry that have entries
            if first_entry and last_entry and all_dates_sorted:
                first_d = entry_dates[0]
                last_d = entry_dates[-1]
                total_weeks = max(1, math.ceil((last_d - first_d).days / 7) + 1)
                unique_weeks = len(weekly)
                consistency = round(unique_weeks / total_weeks, 2) if total_weeks > 0 else 0
            else:
                consistency = 0

            # Recent entries (reverse chronological, max 10)
            sorted_entries = sorted(
                act_entries,
                key=lambda e: (e["date"], e["time"] or ""),
                reverse=True,
            )[:10]

            recent = []
            for e in sorted_entries:
                notes_preview = (e["notes"][:120] + "…") if len(e["notes"]) > 120 else e["notes"]
                recent.append({
                    "date": e["date"],
                    "time": e["time"],
                    "duration": e["duration"],
                    "activity": e["activity"],
                    "stem": e["stem"],
                    "notes_preview": notes_preview,
                    "notes_body": e["notes"],
                    "computed_stats": _compute_structured_stats([e.get("custom_fields", {})]),
                })

            # Aggregate computed_stats across ALL entries for this activity
            all_cfs = [e.get("custom_fields", {}) or {} for e in act_entries]
            activity_computed_stats = _compute_structured_stats(all_cfs)

            wiki_content = _find_wiki_content(slug)

            act_info = {
                "computed_stats": activity_computed_stats or None,
                "activity": act_name,
                "category": cat,
                "slug": slug,
                "total_minutes": total_minutes,
                "entry_count": entry_count,
                "avg_duration": avg_duration,
                "best_week": best_week,
                "streak": streak,
                "consistency": consistency,
                "first_entry": first_entry,
                "last_entry": last_entry,
                "recent_entries": recent,
                "wiki": wiki_content,
            }

            # Deduplicate slugs: append -2, -3, etc. on collision
            if slug in by_activity:
                base = slug
                counter = 2
                while f"{base}-{counter}" in by_activity:
                    counter += 1
                slug = f"{base}-{counter}"
            by_activity[slug] = act_info

            acts.append({
                "name": act_name,
                "slug": slug,
                "total_minutes": total_minutes,
                "entry_count": entry_count,
            })

        color = str(config.get(cat, {}).get("color", "#6b7280"))
        cat_total = sum(a["total_minutes"] for a in acts)
        by_category.append({
            "category": cat,
            "color": color,
            "total_minutes": cat_total,
            "activities": acts,
        })

    return {
        "by_category": by_category,
        "by_activity": by_activity,
    }


def write_json_data(entries: list[dict], config: dict) -> None:
    """Write _derived/har-data.json with structured data for web dashboard."""
    from datetime import datetime, timedelta
    today = date.today()
    
    # Build category info from config
    categories_list = []
    for cat_name, cat_data in sorted(config.items()):
        subcats = sorted(cat_data.get("subcategories", {}).keys())
        categories_list.append({
            "name": cat_name,
            "color": cat_data.get("color", "#6b7280"),
            "subcategories": subcats,
        })

    # Home: current week
    days_since_monday = today.weekday()
    week_start = today - timedelta(days=days_since_monday)
    week_end = week_start + timedelta(days=6)
    week_entries = [e for e in entries if e["date"] and week_start <= date.fromisoformat(e["date"]) <= week_end]
    week_total = sum(e["duration"] or 0 for e in week_entries)
    cat_breakdown = _cat_breakdown(week_entries, config)
    recent = sorted((e for e in entries if e["date"]), key=lambda e: (e["date"], e["time"] or ""), reverse=True)[:8]
    recent_actions = []
    for e in recent:
        notes_preview = (e["notes"][:120] + "…") if len(e["notes"]) > 120 else e["notes"]
        recent_actions.append({
            "date": e["date"],
            "time": e["time"],
            "activity": e["activity"],
            "category": e["category"],
            "duration": e["duration"],
            "stem": e["stem"],
            "notes_preview": notes_preview,
        })
    
    # Time stats ranges
    all_dates = [date.fromisoformat(e["date"]) for e in entries if e["date"]]
    latest_date = max(all_dates) if all_dates else today
    
    def range_data(days):
        if days == 36500:
            window = entries
        else:
            start = latest_date - timedelta(days=days - 1)
            window = [e for e in entries if e["date"] and start <= date.fromisoformat(e["date"]) <= latest_date]
        total = sum(e["duration"] or 0 for e in window)
        return {
            "total_minutes": total,
            "total_entries": len(window),
            "category_breakdown": _cat_breakdown(window, config),
        }
    
    # Notes: activities ranked + by_activity
    notes_entries = [e for e in entries if e["has_notes"]]
    by_family = defaultdict(list)
    for e in notes_entries:
        fam = activity_family(e["activity"])
        by_family[fam].append(e)
    ranked = sorted(by_family.items(), key=lambda x: (-len(x[1]), x[0]))
    activities_ranked = []
    by_activity = {}
    for fam, note_entries in ranked:
        slug = fam.lower().replace(" ", "-").replace("/", "-")
        activities_ranked.append({"activity": fam, "note_count": len(note_entries), "slug": slug})
        sorted_notes = sorted(note_entries, key=lambda e: e["date"], reverse=True)
        by_activity[slug] = [{"date": e["date"], "body": e["notes"]} for e in sorted_notes]

    # Calendar month data
    def month_data(year, month):
        import calendar as cal_mod
        _, days_in_month = cal_mod.monthrange(year, month)
        days = {}
        for d in range(1, days_in_month + 1):
            ds = f"{year}-{month:02d}-{d:02d}"
            day_entries = [e for e in entries if e["date"] == ds]
            plan_path = PLANS_ROOT / f"{ds}.md"
            has_plan = plan_path.exists()
            has_actions = len(day_entries) > 0
            if has_plan and has_actions:
                state = "blue"
            elif has_plan:
                state = "green"
            elif has_actions:
                state = "red"
            else:
                state = "neutral"
            notes_preview = ""
            if day_entries:
                first_notes = day_entries[0]["notes"]
                notes_preview = (first_notes[:80] + "…") if len(first_notes) > 80 else first_notes
            days[ds] = {"state": state, "has_plan": has_plan, "has_actions": has_actions, "notes_preview": notes_preview}
        return {"year": year, "month": month, "days": days}

    # Derive months dynamically from entry dates (replaces hardcoded list)
    months_set = sorted(set(
        (int(e["date"][:4]), int(e["date"][5:7]))
        for e in entries if e.get("date")
    ))
    month_data_list = [month_data(y, m) for y, m in months_set] if months_set else [month_data(2026, 5)]

    # Read plan files for day detail
    plans_summary = {}
    if PLANS_ROOT.exists():
        for plan_file in sorted(PLANS_ROOT.glob("*.md")):
            try:
                plan_text = plan_file.read_text(encoding="utf-8")
                plan_date = plan_file.stem
                plan_body = plan_text.strip()
                plans_summary[plan_date] = {
                    "date": plan_date,
                    "plan": plan_body[:500] + ("…" if len(plan_body) > 500 else ""),
                    "full_plan": plan_body,
                }
            except Exception:
                pass

    # Day detail: for every day with entries
    day_detail = {}
    for e in sorted(entries, key=lambda x: x["date"]):
        ds = e["date"]
        if not ds:
            continue
        if ds not in day_detail:
            plan_for_day = plans_summary.get(ds, {})
            day_detail[ds] = {
                "date": ds,
                "weekday": "",
                "state": "red" if plan_for_day else "neutral",
                "match_score": None,
                "plan": plan_for_day.get("plan") if plan_for_day else None,
                "actuals": [],
            }
        cf = e.get("custom_fields", {})
        day_detail[ds]["actuals"].append({
            "time": e["time"],
            "activity": e["activity"],
            "duration": e["duration"],
            "category": e["category"],
            "stem": e["stem"],
            "custom_fields": cf,
            "computed_stats": _compute_structured_stats([cf]),
            "notes_preview": (e["notes"][:120] + "…") if len(e["notes"]) > 120 else e["notes"],
            "notes_body": e["notes"],
            "public_action_id": e.get("public_action_id", ""),
        })
    # Add plan-only days (future dates with no entries yet)
    for plan_date in plans_summary:
        if plan_date not in day_detail:
            day_detail[plan_date] = {
                "date": plan_date,
                "weekday": date.fromisoformat(plan_date).strftime("%A"),
                "state": "green",
                "match_score": None,
                "plan": plans_summary[plan_date]["plan"],
                "actuals": [],
            }

    for ds, dd in day_detail.items():
        if "weekday" not in dd or not dd["weekday"]:
            try:
                dd["weekday"] = date.fromisoformat(ds).strftime("%A")
            except Exception:
                dd["weekday"] = ""

    # Wiki data: per-activity lifetime stats
    wiki_data = _generate_wiki_data(entries, config)

    data = {
        "meta": {
            "generated": datetime.now().isoformat(),
            "total_entries": len(entries),
            "data_start": min((e["date"] for e in entries if e["date"]), default=""),
            "data_end": max((e["date"] for e in entries if e["date"]), default=""),
            "categories": categories_list,
        },
        "home": {
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "week_total_minutes": week_total,
            "week_total_entries": len(week_entries),
            "category_breakdown": cat_breakdown,
            "recent_actions": recent_actions,
            "latest_stats": {},
        },
        "time_stats": {
            "ranges": {
                "7d": range_data(7),
                "30d": range_data(30),
                "all": range_data(36500),
            }
        },
        "notes": {
            "activities_ranked": activities_ranked,
            "by_activity": by_activity,
        },
        "wikis": wiki_data,
        "calendar": {
            "month_data": month_data_list,
        },
        "plans": {
            "ideal_daily": _read_wiki_preview(REPO_ROOT / "wiki/digital-products/har/what-is-calebs-ideal-daily-routine.md"),
            "ideal_weekly": _read_wiki_preview(REPO_ROOT / "wiki/digital-products/har/what-is-calebs-ideal-weekly-routine.md"),
            "daily_plans": plans_summary,
        },
        "day_detail": day_detail,
    }

    (DERIVED_ROOT / "har-data.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"  Generated har-data.json ({len(entries)} entries)")


def _compute_structured_stats(raw_cf_list: list[dict]) -> list[dict]:
    """Compute structured computed_stats for web dashboard display.
    
    For exercise-based activities (exercises list in YAML), aggregates
    per unique exercise name across all entries. For simple numeric fields,
    sums or averages them.
    """
    from collections import defaultdict
    
    if not raw_cf_list:
        return []
    
    # Check if any entry has exercises
    has_exercises = any(
        isinstance(cf.get("exercises"), list) for cf in raw_cf_list
    )
    
    if has_exercises:
        # Aggregate per exercise name across all entries
        ex_agg = defaultdict(lambda: {"sets": 0, "reps": [], "weight": None, "unit": ""})
        for cf in raw_cf_list:
            exercises = cf.get("exercises", [])
            if not isinstance(exercises, list):
                continue
            for ex in exercises:
                if isinstance(ex, str):
                    desc, _, rep_text = ex.partition(":")
                    parsed_desc = _parse_exercise_descriptor(desc)
                    name = parsed_desc["name"]
                    if not name:
                        continue
                    ex_agg[name]["description"] = name
                    ex_agg[name]["sets"] += int(parsed_desc["sets"] or 0)
                    ex_agg[name]["reps"].extend(_extract_rep_values(rep_text))
                    if parsed_desc["weight"] is not None and ex_agg[name]["weight"] is None:
                        ex_agg[name]["weight"] = parsed_desc["weight"]
                        ex_agg[name]["unit"] = parsed_desc["unit"]
                    continue
                if not isinstance(ex, dict):
                    continue
                # Handle two exercise formats:
                # 1) Standard: {name, sets, reps, weight, unit}
                # 2) Alternative: {"2 sets rock deadlifts (70lb)": "10, 8 reps", ...}
                if "name" in ex:
                    parsed_ex = _exercise_dict_rollup(ex)
                    name = parsed_ex.get("name", "")
                    if not name:
                        continue
                    ex_agg[name]["sets"] += int(parsed_ex.get("sets", 0) or 0)
                    ex_agg[name]["reps"].extend(parsed_ex.get("reps", []))
                    if parsed_ex.get("weight") is not None and ex_agg[name]["weight"] is None:
                        ex_agg[name]["weight"] = parsed_ex["weight"]
                        ex_agg[name]["unit"] = parsed_ex.get("unit", "")
                else:
                    # Alternative format: key is the exercise description, value is reps text
                    for ex_desc, ex_rep_text in ex.items():
                        if not isinstance(ex_rep_text, str):
                            continue
                        parsed_desc = _parse_exercise_descriptor(str(ex_desc))
                        name = parsed_desc["name"]
                        if not name:
                            continue
                        ex_agg[name]["description"] = name
                        ex_agg[name]["sets"] += int(parsed_desc["sets"] or 0)
                        ex_agg[name]["reps"].extend(_extract_rep_values(ex_rep_text))
                        if parsed_desc["weight"] is not None and ex_agg[name]["weight"] is None:
                            ex_agg[name]["weight"] = parsed_desc["weight"]
                            ex_agg[name]["unit"] = parsed_desc["unit"]
        
        # Emit each exercise metric as a separate clean key-value pair
        # This ensures new fields (distance, make/miss %, etc.) auto-appear
        result = []
        for name, data in sorted(ex_agg.items()):
            total_reps = sum(data["reps"]) if data["reps"] else 0
            is_alt = "description" in data
            weight_str = f"{data['weight']} {data['unit']}".strip() if data["weight"] is not None else ""
            if not is_alt and data["sets"]:
                result.append({
                    "name": f"{name} Sets",
                    "value": str(data["sets"]),
                    "type": "exercise",
                })
            if total_reps:
                result.append({
                    "name": f"{name} Reps",
                    "value": str(total_reps),
                    "type": "exercise",
                })
            if weight_str:
                result.append({
                    "name": f"{name} Weight",
                    "value": weight_str,
                    "type": "exercise",
                })
        return result
    
    # Simple numeric fields — aggregate per field name
    numeric_agg = defaultdict(lambda: {"total": 0.0, "count": 0})
    for cf in raw_cf_list:
        for k, v in cf.items():
            if isinstance(v, (int, float)):
                numeric_agg[k]["total"] += float(v)
                numeric_agg[k]["count"] += 1
    
    if not numeric_agg:
        return []
    
    result = []
    for k, data in sorted(numeric_agg.items()):
        label = k.replace("_", " ").title()
        avg = data["total"] / data["count"] if data["count"] > 0 else 0
        if data["count"] > 1:
            # Show total for countable stats (putts_attempted), avg for measurements (wind_speed)
            if k in ("wind_speed", "temperature"):
                result.append(
                    {
                        "name": f"{label} (Avg)",
                        "value": f"{avg:.0f}",
                        "type": "average",
                        "sessions": data["count"],
                    }
                )
            else:
                result.append(
                    {
                        "name": label,
                        "value": f"{int(data['total'])}",
                        "type": "total",
                        "sessions": data["count"],
                    }
                )
        else:
            val = int(data["total"]) if data["total"] == int(data["total"]) else round(data["total"], 1)
            result.append(
                {
                    "name": label,
                    "value": str(val),
                    "type": "simple",
                    "sessions": data["count"],
                }
            )
    return result


def _cat_breakdown(entries_list, config):
    """Build category_breakdown array for a list of entries."""
    from collections import defaultdict
    buckets = defaultdict(lambda: defaultdict(int))
    cat_entry_counts = defaultdict(int)
    act_entry_counts = defaultdict(lambda: defaultdict(int))
    act_custom_fields = defaultdict(lambda: defaultdict(list))
    raw_activity_cf = defaultdict(lambda: defaultdict(list))  # raw custom_fields dicts per (cat, act)
    for e in entries_list:
        if e["duration"] is None:
            continue
        cat = e.get("category", "unknown")
        act_name = activity_family(e["activity"])
        buckets[cat][act_name] += e["duration"]
        cat_entry_counts[cat] += 1
        act_entry_counts[cat][act_name] += 1
        # Collect custom_fields by key for aggregation
        cf = e.get("custom_fields", {}) or {}
        for k, v in cf.items():
            act_custom_fields[cat][act_name].append((k, v))
        raw_activity_cf[cat][act_name].append(cf)
    total = sum(e["duration"] or 0 for e in entries_list)
    result = []
    for cat in sorted(buckets, key=lambda c: -sum(buckets[c].values())):
        color = str(config.get(cat, {}).get("color", "#6b7280"))
        cat_total = sum(buckets[cat].values())
        pct = round((cat_total / total) * 100) if total else 0
        acts = []
        for act_name in sorted(buckets[cat], key=lambda a: -buckets[cat][a]):
            # Aggregate custom_fields across entries for this activity
            act_cf = act_custom_fields[cat].get(act_name, [])
            agg = {}
            for k, v in act_cf:
                if k not in agg:
                    agg[k] = []
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            for sk, sv in item.items():
                                agg[f"{k}_{sk}"] = agg.get(f"{k}_{sk}", []) + ([sv] if isinstance(sv, (int, float)) else [str(sv)])
                        else:
                            agg[k].append(str(item))
                elif isinstance(v, (int, float)):
                    agg[k].append(v)
                elif isinstance(v, str):
                    agg[k].append(v)
            # Sum numeric aggregate fields
            agg_summed = {}
            for k, vals in agg.items():
                if vals and all(isinstance(v, (int, float)) for v in vals):
                    agg_summed[k] = sum(vals)
                elif vals:
                    agg_summed[k] = vals
            # Compute structured stats for web dashboard display
            raw_cfs = raw_activity_cf[cat].get(act_name, [])
            computed = _compute_structured_stats(raw_cfs)
            acts.append({
                "name": act_name,
                "total_minutes": buckets[cat][act_name],
                "entry_count": act_entry_counts[cat][act_name],
                "custom_fields": agg_summed if agg_summed else None,
                "computed_stats": computed if computed else None,
            })
        result.append({
            "category": cat,
            "color": color,
            "total_minutes": cat_total,
            "percentage": pct,
            "entries": cat_entry_counts[cat],
            "activities": acts,
        })
    return result


def main() -> int:
    entries = action_entries()
    config = load_category_config()
    DERIVED_ROOT.mkdir(parents=True, exist_ok=True)
    GRAPH_ROOT.mkdir(parents=True, exist_ok=True)
    JOURNAL_ROOT.mkdir(parents=True, exist_ok=True)

    # Home summary
    (DERIVED_ROOT / "har-home-summary.md").write_text(render_home_summary(entries), encoding="utf-8")
    (DERIVED_ROOT / "har-time-shell-overview.md").write_text(
        render_time_shell_overview(entries),
        encoding="utf-8",
    )

    # Time charts
    for days, name in [(7, "last-7-days"), (30, "last-30-days")]:
        chart = render_chart(f"HAR Time - Last {days} Days", window_entries(entries, days), config)
        (GRAPH_ROOT / f"har-time-{name}-chart.svg").write_text(chart, encoding="utf-8")
    all_chart = render_chart("HAR Time - All Logged", entries, config)
    (GRAPH_ROOT / "har-time-summary-chart.svg").write_text(all_chart, encoding="utf-8")

    # Time windows with aggregated stats
    (DERIVED_ROOT / "har-time-last-7-days.md").write_text(
        render_time_window(entries, "HAR Time & Stats - Last 7 Days", 7, "har-time-last-7-days-chart.svg"),
        encoding="utf-8",
    )
    (DERIVED_ROOT / "har-time-last-30-days.md").write_text(
        render_time_window(entries, "HAR Time & Stats - Last 30 Days", 30, "har-time-last-30-days-chart.svg"),
        encoding="utf-8",
    )
    (DERIVED_ROOT / "har-time-summary.md").write_text(
        render_time_window(entries, "HAR Time & Stats - All Logged Time", 36500, "har-time-summary-chart.svg"),
        encoding="utf-8",
    )

    # Notes summary + activity journals
    notes_summary, act_index, chrono_notes = render_notes_summary(entries)

    # General notes chart
    note_chart_svg = render_general_notes_chart(entries, config)
    (GRAPH_ROOT / "har-general-notes-chart.svg").write_text(note_chart_svg, encoding="utf-8")

    (DERIVED_ROOT / "har-general-notes-summary.md").write_text(notes_summary, encoding="utf-8")
    (DERIVED_ROOT / "har-activity-journals" / "index.md").write_text(act_index, encoding="utf-8")

    # Generate individual activity journal pages (one per activity family)
    all_notes = [e for e in entries if e["has_notes"]]
    families_seen: set[str] = set()
    for e in all_notes:
        fam = activity_family(e["activity"])
        if fam in families_seen:
            continue
        families_seen.add(fam)
        journal_md = render_activity_journal(fam, all_notes, all_notes)
        if journal_md:
            journal_slug = fam.lower().replace(" ", "-").replace("/", "-")
            (JOURNAL_ROOT / f"{journal_slug}.md").write_text(journal_md, encoding="utf-8")
            print(f"  Generated journal: {journal_slug}.md")

    write_json_data(entries, config)
    print("Built HAR derived summaries (range switcher + aggregated stats + journals + JSON).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
