# Public Actions

This directory contains anonymized, aggregated human action data contributed by opt-in HAR users. No personal identifiers. No text content. No exact times.

See [PUBLIC-ACTIONS.md](../PUBLIC-ACTIONS.md) in the repo root for full documentation on how the opt-in system works, privacy guarantees, and how to contribute.

## Current Stats

See `aggregates/latest.json` for the latest computed aggregates.

## Structure

```
public-actions/
├── actions/         # Action registry — one file per unique action type
│   ├── project-work.md
│   ├── running.md
│   └── ...
├── entries/         # Anonymized contribution data
│   └── YYYY/MM/
│       └── YYYY-MM-DD.jsonl
├── aggregates/      # Pre-computed stats
│   ├── latest.json
│   └── index.json
└── README.md
```

## Data Format

### Action Registry
One file per action with aggregate stats — total participants, entries, duration, per-location breakdowns. Includes first-reporter credit.

### Entry Files
JSONL (one JSON object per line), organized by date:
```json
{"action_id":"project-work","duration":165,"weekday":4,"time_bucket":"morning","date":"2026-06-19"}
```

### Aggregates
Global and per-location stats computed from all entries.
