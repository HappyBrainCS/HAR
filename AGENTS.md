# HAR AGENTS.md — Copy-Paste Agent Instructions

Give this file to any AI agent (OpenClaw, Claude Code, ChatGPT, etc.) to set up and use HAR. The agent reads this, understands the system, and starts capturing your time.

---

## About HAR

**HAR (Human Action Record)** is a local-first personal action tracking system. The philosophy is simple: you talk to an AI agent about what you did, the agent writes structured markdown files, and a local web dashboard turns that into category breakdowns, calendar views, and stats over time. No accounts, no subscriptions, no cloud — just flat files you own. HAR exists because most time-tracking tools are built for employers or gamified productivity apps. HAR is built for *you* — honest time tracking through conversational capture, stored in markdown files on your machine.

## Quick Start

1. **Clone the repo** — `git clone https://github.com/YOUR_USERNAME/HAR.git ~/HAR`
2. **Read this file** — you're doing it now. Give this entire file to your AI agent.
3. **Learn the user's routine** — ask the user about their routine, typical schedule, timezone, and location. Use that context.
4. **Start capturing** — ask the user what they've done today, then write structured markdown files (details below).
5. **Build and serve the dashboard** — `cd ~/HAR && python3 scripts/build-har-derived.py && python3 scripts/serve-har-dashboard.py`

## The 4-Field Capture Format

When you ask the user what they did, extract these four fields:

| Field | Required | Description | Example |
|---|---|---|---|
| Activity name | ✅ | What they did | "disc golf putting practice" |
| Start-stop time | ✅ | When | "9:15–9:30am" |
| Stats | ⬜ | Quantifiable data | "80 putts" |
| Notes | ⬜ | Context | "rocking straight back improved accuracy" |

Capture is conversational — you don't present a form. You just ask "what did you do?" and extract these fields from the answer.

## File Format

Every action gets a markdown file at:

```
calendar/YYYY/YYYY-MM/YYYY-MM-DD-activity-slug.md
```

### Full YAML Frontmatter

```yaml
---
type: action                   # Always "action"
date: 2026-05-17               # YYYY-MM-DD
weekday: Sunday                # Monday-Sunday
time: "13:30"                  # HH:MM start time (24h, quoted)
activity: Disc Golf Practice Putting  # Human-readable name
duration: 30                   # Integer minutes
category: health               # One of: health, work, creative, personal, social, gaming
subcategory: disc-golf         # Free-form within category
source: scribe                 # Always "scribe"
capture_mode: conversational   # Always "conversational"
custom_fields:                 # Optional — stat tracking
  putts_attempted: 100
public_action_id: disc-golf-putting-practice  # Optional — see Opt-In section
---
```

### Notes Section

After the frontmatter, a blank line, then free-text notes:

```markdown
From 1:30–2:00 I practiced putting. Tried 100 putts with the straight-back
motion. Made about 50% from 15ft. Rocking straight back improved accuracy
noticeably.
```

### Complete Example

```markdown
---
type: action
date: 2026-05-17
weekday: Sunday
time: "09:15"
activity: Morning Workout — Push and Quad Focus
duration: 55
category: health
subcategory: strength-training
source: scribe
capture_mode: conversational
custom_fields:
  exercises:
    - name: Pike Pushups
      sets:
        - reps: 10
        - reps: 8
        - reps: 8
    - name: Pistol Squats
      sets:
        - reps: 8
        - reps: 6
  total_sets: 5
  total_reps: 40
public_action_id: push-and-quad-workout
---

Pike pushups felt strong today — controlled negatives. Pistol squats were 
tough on the right knee, kept depth conservative. Overall good session.
```

## Category Mapping

The agent **infers** the category from the activity name and context. The user never picks a category.

### Categories (from `maps/action-categories.yaml`)

| Category | Display Name | Keywords |
|---|---|---|
| `health` | Health & Fitness | workout, exercise, disc golf, practice, walk, run, stretch, yoga |
| `work` | Work & Projects | context, har, dev, development, code, writing, project, work, research, planning |
| `creative` | Creative & Music | music, guitar, production, song, creative, art |
| `personal` | Personal & Life | lunch, breakfast, dinner, relaxation, personal, decompression, reset, rituals, wind-down, hang out, social |
| `social` | Social & Family | hang out, friend, family, sister, social, phone, call |
| `gaming` | Gaming & Entertainment | game, pokopia, nintendo, elden ring, play, entertainment |

### Subcategories

Subcategories are free-form within a category. Examples:
- `work` → `context-game`, `har`, `disc-golf-rpg`, `development`
- `health` → `disc-golf`, `strength-training`, `cardio`, `mobility`
- `personal` → `food`, `general`, `self-care`

Subcategories are implied by the activity context. The agent picks them, not the user.

## Discreet Activity Naming

Some activities benefit from covering names that appear appropriate in summaries. Use these conventions:

| Activity Name | What It Means | When to Use |
|---|---|---|
| "Morning Rituals" / "Morning Rituals and Wind-down" | Morning personal time | Morning decompression, personal routine after waking |
| "Mid-morning Reset" | Mid-day break | Short break between work blocks |
| "Afternoon Reset" | Mid-day break | Afternoon decompression |

The activity name should be appropriate if someone reads the file or dashboard summary. Sensitive or private activities get professional-sounding names.

## Routine Cross-Reference

Before capturing, check if the user has a routine or fitness schedule available:

- Look for a `fitness-routine.md` or routine-related file in the user's knowledge base or wiki
- Ask the user about their typical schedule when you're new

**Proactive prompting example:**

> "It's Thursday — did you get push+quad in this morning?"

This makes capturing feel natural and proactive rather than reactive.

## Opt-In Public Record (if configured)

If the user has opted in to public contributions, the agent participates in a public action registry.

### Environment Variables

```
HAR_PUBLIC_CONTRIBUTE=true          # Set to opt in
HAR_PUBLIC_LOCATION="Cedar City, UT"  # Location for anonymized data
HAR_PUBLIC_DISPLAY_NAME="HappyBrain"  # First-reporter credit (optional)
```

### How It Works

1. **Before writing an entry**, check the public action registry (a public GitHub repo with action slugs).
2. **Match the activity** to an existing action slug. If the activity maps to an existing slug (e.g., "disc golf putting practice" → `disc-golf-putting-practice`), use that `public_action_id`.
3. **If no match exists**, create a new action slug. The user becomes the first reporter for that activity.
4. **After writing the entry**, run `python3 scripts/har-contribute.py` to push anonymized data to the public registry.

### What Gets Contributed

Anonymized snapshot data — the activity name, duration, category, and location. No personal identifiers, no full notes, no identifiable timestamps beyond date. See `action-registry-spec.md` for the full schema.

## Build & Serve

```bash
# Rebuild dashboard data from calendar files
python3 scripts/build-har-derived.py

# Start the dashboard on port 8093
python3 scripts/serve-har-dashboard.py

# Or both at once
./start.sh
```

Open `http://localhost:8093` in a browser to view the dashboard.

## Directory Structure

```
~/HAR/
├── calendar/            # Source of truth — flat markdown files
│   └── YYYY/YYYY-MM/   # Organized by year and month
├── _derived/            # Built from calendar files (generated)
│   ├── har-data.json    # Structured JSON for the dashboard
│   ├── har-time-*.md    # Time summary reports
│   └── har-activity-journals/  # One page per unique activity
├── _har_web/            # Dashboard frontend (HTML, CSS, JS)
├── scripts/
│   ├── build-har-derived.py    # Build the derived data
│   └── serve-har-dashboard.py  # Start the web dashboard
├── maps/
│   ├── action-categories.yaml  # Category mapping config
│   └── frontmatter-schema.yaml # Canonical frontmatter schema
├── plans/               # Daily plans (optional)
├── start.sh             # Launches the dashboard
├── README.md            # Full project philosophy and setup
└── AGENTS.md            # This file — agent instructions
```

## Tips for the Agent

- **Be conversational.** Ask "what did you do?" not "please fill in the form."
- **Infer everything.** Category, subcategory, duration — derive from context, don't ask.
- **Be proactive.** Reference the user's routine to prompt before they tell you.
- **Write immediately.** When the user tells you what they did, write the file right then.
- **Build after capture.** Run the build script after each session so the dashboard stays current.
- **Stats are optional.** If the user didn't track numbers, don't force them. "No stats to report" is a valid state.
- **Be discreet.** Use covering names for sensitive activities. The file should be appropriate for a summary view.
- **Read existing entries first.** Before writing, skim the current month's directory to match naming conventions and catch duplicates.
