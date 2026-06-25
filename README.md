# HAR — Human Action Record

> I wanted a way to be honest about how I spend my time. I talk to an AI agent, it writes the truth into files. Here's the system. Free, local, no subscription, no cloud.

**HAR is a local-first personal action tracking system.** You tell an AI agent what you did — "worked on the dashboard for a couple hours, then made lunch, then went for a walk" — and it writes structured markdown files. A local web dashboard turns that into category breakdowns, calendar views, activity journals, and stats over time.

## Philosophy

Most time-tracking tools are built for one of two audiences:

- **Employers** who want to bill clients or monitor productivity
- **Self-help apps** that gamify your life and charge $50/year for cloud storage

HAR is for neither. HAR is for **you**, on your machine, in files you own. The AI agent does the heavy lifting of structuring what you report. You just talk.

- **No accounts.** No signup. No subscription.
- **No cloud.** Your data lives in flat markdown files in `~/HAR/calendar/`.
- **No lock-in.** Markdown files are universal. You can read them with any text editor, grep them, or write your own tools.
- **No forms.** You report conversationally. The agent writes the structured files.

## Quick Start

### Prerequisites
- An AI agent capable of running scripts and writing files (OpenClaw, Claude Code, ChatGPT, etc.)
- Python 3
- A web browser (for the dashboard)

### Setup
```bash
git clone https://github.com/HappyBrainCS/HAR.git ~/HAR
cd ~/HAR
python3 scripts/build-har-derived.py   # build the derived data
python3 scripts/serve-har-dashboard.py  # start the dashboard
# Open http://localhost:8093
```

### Start Logging
Give your AI agent the `AGENTS.md` file in this repo. The agent reads it and handles everything — capture format, file naming, category mapping, discreet activity names. You just talk about your day.

## How It Works

HAR has three layers:

### Layer 1 — Capture
You tell an AI agent what you did. The agent extracts:
- **What** you did (activity name)
- **When** (start/stop time)
- **Stats** (optional — reps, distance, pages read, etc.)
- **Notes** (optional — context, observations)

The agent categorizes everything automatically (work, health, creative, personal, gaming, social) and writes a clean markdown file with YAML frontmatter.

### Layer 2 — Derivation
A build script reads all calendar markdown files and produces structured JSON, time summaries, activity journals, and SVG charts.

### Layer 3 — Review
A local web dashboard shows your data: category breakdown, time stats, activity wikis, calendar view. Everything is served from `localhost:8093`.

## Project Structure
```
~/HAR/
├── calendar/            # Source of truth — flat markdown files
│   └── YYYY/YYYY-MM/   # Organized by year and month
├── _derived/            # Built from calendar files (generated)
│   ├── har-data.json    # Structured JSON for the dashboard
│   ├── har-time-*.md    # Time summary reports
│   └── har-activity-journals/  # One page per unique activity
├── _har_web/            # Dashboard frontend (HTML, CSS, JS)
├── public-actions/      # Opt-in public action record (see PUBLIC-ACTIONS.md)
│   ├── actions/         # Action registry — one file per activity type
│   ├── entries/         # Anonymized contribution data (JSONL)
│   └── aggregates/      # Computed stats by action and location
├── scripts/
│   ├── build-har-derived.py    # Build the derived data
│   ├── serve-har-dashboard.py  # Start the web dashboard
│   └── har-contribute.py       # Anonymize and contribute to public record
├── maps/
│   ├── action-categories.yaml   # Category mapping config
│   └── frontmatter-schema.yaml  # Canonical frontmatter schema
├── plans/               # Daily plans (optional)
├── start.sh             # Launches the dashboard
├── README.md            # This file
├── AGENTS.md            # Copy-paste agent instructions
└── PUBLIC-ACTIONS.md    # Opt-in public record documentation
```

## Opt-In Public Record

HAR includes an optional public action record. If you opt in, anonymized summaries of your activities are contributed to `public-actions/`. Over time, this creates a public dataset showing real human behavior patterns — by activity, by location, by time of day.

**No personal data is ever shared.** See `PUBLIC-ACTIONS.md` for full details on what gets shared, what doesn't, privacy guarantees, and how to opt in.

## Dashboard Pages
- **Home** — Week so far: total time, category breakdown, recent actions
- **Time & Stats** — Bar charts with date range picker (7d/30d/all), per-activity stats
- **Activity Wikis** — Every unique activity with lifetime history and notes timeline
- **Calendar** — Color-coded day view

## Who Is HAR For?
- Solo developers and indie makers who talk to AI agents daily
- People who want structured time data without manual logging
- Anti-subscription users who want local-first tools
- ADHD-friendly logging — you don't have to remember to start/stop timers
- Quantified-self enthusiasts who want their data in files they actually own

## Who Is HAR Not For?
- Teams needing time billing or invoicing
- People who want fully automatic tracking (no input at all)
- Non-technical users who can't set up an AI agent

## Design Decisions
- **Flat files as source of truth** — no database. Calendar entries are standalone markdown.
- **Agent picks categories** — you never manually assign a category. The agent infers from activity name and context.
- **Conversational capture** — no forms, no structured input. Just talk naturally.
- **Local-first** — everything runs on your machine. No servers, no accounts, no subscriptions.
- **Opt-in public record** — sharing is never the default. The anonymization pipeline is auditable, open source, and only runs when you explicitly enable it.

## License
MIT — do whatever you want with it. Fork it, extend it, build something else on top.

---

*Built for myself. Shared in case it's useful to you. — Caleb*
