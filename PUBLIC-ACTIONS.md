# Public Actions — Opt-In Public Data Record

> This directory is the **public action record**: an anonymized, aggregated dataset of what people actually do. It lives inside the HAR repo because it's part of the same system — but it's opt-in only.

## What This Is

When you track your day with HAR, your data stays local by default. But if you opt in, anonymized summaries are contributed here. Over time, this becomes a public dataset showing real human behavior patterns — by activity, by location, by time of day.

No notes. No names. No exact times. Just: "someone in Cedar City did project work for 165 minutes on a Thursday morning."

## What Gets Shared

| Field | Shared? | Example |
|-------|---------|---------|
| Action type | ✅ | `project-work` |
| Duration in minutes | ✅ | `165` |
| Day of week | ✅ | `4` (Thursday) |
| Time of day bucket | ✅ | `morning` |
| Location (city/region) | ⬜ Only if you set it | `Cedar City, UT` |

## What Never Gets Shared

- Notes field (entirely stripped)
- Exact activity name ("Project Work with OpenClaw" → `project-work`)
- Exact times (only morning/afternoon/evening/night bucket)
- Any names, places, app names, company names
- Stats (reps, pages, scores)
- Any text content

## How to Opt In

```bash
# 1. Set the environment variable
export HAR_PUBLIC_CONTRIBUTE=true

# Optional: add your location for local stats
export HAR_PUBLIC_LOCATION="Cedar City, UT"

# Optional: choose a display name for first-reporter credit
export HAR_PUBLIC_DISPLAY_NAME="HappyBrain"

# 2. Run the contribution pipeline
python3 scripts/har-contribute.py --dry-run  # See what would be shared
python3 scripts/har-contribute.py --push     # Actually contribute
```

You can also automate this as a cron job or post-capture hook.

## The Action Registry

The `actions/` directory contains one file per unique action type. The first person to report an action gets "first reporter" credit:

| Action | First Reporter | Participants | Locations |
|--------|---------------|-------------|-----------|
| project-work | HappyBrain | 2 | Cedar City, UT |
| morning-rituals | HappyBrain | 2 | Cedar City, UT |
| workout-burst | HappyBrain | 1 | Cedar City, UT |

When you log a new action that doesn't exist yet, you become the first reporter and your chosen name is permanently attached.

## Privacy Guarantees

1. **No hidden data collection.** HAR is local-first. The contribution pipeline only runs when you explicitly enable it and run the script.
2. **No recall.** Once data is contributed to the public repo, it stays. This is by design — removing data would require identifying it, which defeats anonymity.
3. **Location buffer.** Stats for a location only appear when ≥3 contributors are in that area. Full breakdowns at ≥10.
4. **Auditable.** The entire pipeline is open source. You can verify exactly what gets shared by running `--dry-run`.

## The Data Format

### Entry Files (`entries/YYYY/MM/YYYY-MM-DD.jsonl`)

One JSON line per anonymized entry:

```json
{"action_id":"project-work","duration":165,"weekday":4,"time_bucket":"morning","date":"2026-06-19"}
```

### Action Registry (`actions/project-work.md`)

One file per action with aggregate stats:

```yaml
---
action: "project-work"
total_participants: 2
total_entries: 4
total_duration_minutes: 585
per_location:
  "Cedar City, UT":
    total_entries: 4
    total_duration: 585
    unique_dates: ["2026-06-18", "2026-06-19"]
slug: "project-work"
first_reporter: "HappyBrain"
---
```

### Aggregates (`aggregates/latest.json`)

Globally computed stats refreshed on each contribution:

```json
{
  "total_entries": 15,
  "total_duration_minutes": 1140,
  "total_participants": 2,
  "by_action": [...],
  "by_location": [...]
}
```

## Who Owns This Data

You do. HAR the tool is MIT. The public dataset belongs to the contributors. If the dataset becomes valuable enough for a data co-op (revenue sharing with contributors), that will be a separate governance structure — never a sale of the tool or the existing data without contributor consent.

## Examples

### Running the pipeline for the first time

```bash
export HAR_PUBLIC_CONTRIBUTE=true
export HAR_PUBLIC_LOCATION="Cedar City, UT"
export HAR_PUBLIC_DISPLAY_NAME="HappyBrain"
python3 scripts/har-contribute.py --dry-run
python3 scripts/har-contribute.py --push
```

### Setting up automated daily contributions

```bash
# Add to crontab (runs at 11pm daily)
0 23 * * * cd ~/HAR && HAR_PUBLIC_CONTRIBUTE=true python3 scripts/har-contribute.py --since $(date -v-1d +%Y-%m-%d) --push
```
