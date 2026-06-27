# HAR AGENTS.md — Copy-Paste Agent Instructions

Give this file to any AI agent (OpenClaw, Claude Code, ChatGPT, etc.) to set up and use HAR. The agent reads this, understands the system, and starts capturing your time.

---

## About HAR

**HAR (Human Action Record)** is a local-first personal action tracking system. The philosophy is simple: you talk to an AI agent about what you did, the agent writes structured markdown files, and a local web dashboard turns that into category breakdowns, calendar views, and stats over time. No accounts, no subscriptions, no cloud — just flat files you own. HAR exists because most time-tracking tools are built for employers or gamified productivity apps. HAR is built for *you* — honest time tracking through conversational capture, stored in markdown files on your machine.

## Quick Start

1. **Clone the repo** — `git clone https://github.com/YOUR_USERNAME/HAR.git ~/HAR`
2. **Read this file** — you're doing it now. Give this entire file to your AI agent.
3. **Install dependencies** — `cd ~/HAR && pip3 install -r requirements.txt`
4. **Learn the user's routine** — ask the user about their routine, typical schedule, timezone, and location. Use that context.
5. **Start capturing** — ask the user what they've done today, then write structured markdown files (details below).
6. **Build and serve the dashboard** — `cd ~/HAR && python3 scripts/build-har-derived.py && python3 scripts/serve-har-dashboard.py`

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

If the user has opted in to public contributions, HAR can contribute anonymized data
to a public action record. This creates a crowdsourced dataset of real human behavior
— by action type, location, and time of day.

### How to Opt In

```bash
# Set the environment variable
HAR_PUBLIC_CONTRIBUTE=true

# Optional: add your location for local stats
HAR_PUBLIC_LOCATION="Cedar City, UT"

# Optional: choose a display name for first-reporter credit
# Most users skip this — location-only contribution is the default
HAR_PUBLIC_DISPLAY_NAME="YourName"

# Run the pipeline
python3 scripts/har-contribute.py --dry-run  # See what would be shared
python3 scripts/har-contribute.py --push     # Actually contribute
```

### What Gets Shared vs. What Stays Private

| Shared | Not Shared |
|--------|------------|
| Action slug (`disc-golf-putting`) | Your name (unless you opt in) |
| Duration in minutes (`30`) | Exact activity name (`Disc Golf Practice Putting`) |
| Day of week (integer) | Notes field (entirely stripped) |
| Time-of-day bucket (`morning`) | Stats (reps, scores, custom fields) |
| Location (if you set it) | Any text content |

### Per-Entry Opt-In

The agent assigns a `public_action_id` to every entry it writes. This is how
the pipeline knows which activities to contribute. You have full control:

- **Most entries are safe to share** — workouts, project work, gaming, eating, sleeping, social time.
  These make the dataset honest and valuable.
- **Sensitive entries get excluded** — if an activity involves genuinely private behavior
  (specific personal activities), the agent simply doesn't assign a `public_action_id`.
  The pipeline only contributes entries that have one.
- **Ask the user on first capture** — "Do you want this activity in the public record?"
- **Use discretion** — discreet naming ("Morning Rituals and Wind-down") is fine for the
  local calendar, but if the activity is something the user wouldn't want linked to their
  location or data, drop the `public_action_id` entirely.

### How the Agent Matches Activities

Before assigning a `public_action_id` to an entry, the agent should check if
a matching action already exists in the public record. This is how activity
names converge over time and location-based comparisons become meaningful.

**Fetch the lightweight action registry index:**

```bash
# One HTTP GET — returns JSON with slug, title, entry count, locations, exercises
python3 scripts/har-contribute.py --fetch-index
```

This is cheaper than reading every action file. The index tells you:
- What actions exist (slug + human-readable title)
- How many entries each has (popularity hint)
- Which locations have contributors (for place-based matching)
- Exercise names associated with workout actions

**Matching logic (agent does this, not the script):**

1. Fetch the index (`--fetch-index` or read `public-actions/aggregates/action-registry-index.json`)
2. For the user's activity, check if a matching slug exists:
   - **Direct name match** — "Disc Golf Practice Putting" → search index for "disc-golf", "putting"
   - **Same location** — if 3+ people in Cedar City log "disc-golf-putting", suggest that name
   - **Exercise tag match** — if the entry has specific exercises ("Pike Pushups"),
     check the index for actions with those exercise tags
   - **New activity** — if nothing matches, create a new slug. The user becomes the first reporter.
3. Ask the user if they want to use an existing slug or create a new one
4. Set `public_action_id` to the agreed slug

**Example conversation:**

> *"You did push-ups, squats, and pull-ups. The public record has 'workout-burst'
> with entries in Cedar City. Three people use that name. Want to add your workout
> to that action, or create a new one like 'bodyweight-circuit'?"*

This keeps the agent lightweight — it reads one small JSON file instead of
scanning the entire action registry.

### How the Pipeline Works

The script (`python3 scripts/har-contribute.py`):

1. Reads all calendar entries that have a `public_action_id` set
2. Anonymizes each entry — strips notes, stats, exact times, activity names
3. Extracts exercise names from `custom_fields` as tags (stored in action registry)
4. Deduplicates against existing entries (by date + action_id + duration + time_bucket)
5. Writes JSONL lines to `entries/{contributor_id}/YYYY/MM/YYYY-MM-DD.jsonl`
6. Runs `har-reindex.py` to rebuild action registry and aggregates from all contributors
7. Commits and pushes to GitHub

### Per-Contributor Entry Structure

Every contributor gets their own directory under `entries/`:

```
entries/
  happybrain/          ← Your contributor ID
    2026/06/
      2026-06-25.jsonl  ← Your anonymized entries for that date
  player2/             ← Another contributor (added via PR)
    2026/07/
      2026-07-01.jsonl
```

This means **no one ever shares a file**. Fork-and-PR contributions never
cause merge conflicts.

### Pipeline Commands

```bash
# Dry run — see what would be contributed (no writes)
python3 scripts/har-contribute.py --contributor your-github-username --dry-run

# Full contribution + push (repo owner only)
python3 scripts/har-contribute.py --contributor happybrain --push

# Fetch the action registry index as JSON (for agent matching)
python3 scripts/har-contribute.py --fetch-index

# Contribute only entries since a specific date
python3 scripts/har-contribute.py --contributor happybrain --since 2026-06-01 --push

# Rebuild aggregates from all contributors (after pulling latest from upstream)
python3 scripts/har-reindex.py
```

### Fork + PR Workflow (for External Contributors)

Since contributors don't have write access to the main repo, they use a fork:

1. Fork `HappyBrainCS/HAR` to your GitHub account
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/HAR.git ~/HAR`
3. Set up your private calendar (same as Quick Start above)
4. Set env vars and opt into the public record
5. Run the pipeline pointed at your fork:
   ```bash
   python3 scripts/har-contribute.py --contributor YOUR_GITHUB_USERNAME --data-dir ~/HAR/public-actions
   ```
6. Push to your fork:
   ```bash
   cd ~/HAR
   git add public-actions/entries/YOUR_GITHUB_USERNAME/
   git commit -m "Contribute anonymized entries"
   git push
   ```
7. Create a Pull Request from your fork to `HappyBrainCS/HAR`
8. When merged, the Reindex GitHub Action automatically rebuilds the
   action registry and aggregates — you don't need to do anything else

**Your fork is just a tool.** It sits on your GitHub account and you use it
whenever you want to contribute new data. No one else touches your files.

### Evidence Links

Users can optionally include evidence links in their entries to back up public claims.
Evidence is always URLs — never uploaded files. The philosophy is simple:

> *To keep HAR free and scalable, evidence is link-based. If you want to show
> a photo, video, scorecard, or tournament result, host it yourself and link it.*

Evidence links go in the entry's frontmatter:

```yaml
custom_fields:
  evidence:
    - type: url
      label: "UDisc Scorecard — Thunderbird Garden Round"
      url: "https://udisc.com/rounds/abc123"
```

Evidence is **not** contributed to the public record (it's too identifying). It lives
in your personal calendar files and can be referenced by your agent when you ask
questions or make claims.

### Onboarding a New Contributor

When a user first opts in, the agent should:

1. **Explain what gets shared** — show the table above. Be honest, not salesy.
2. **Ask about existing data** — "You already have N entries. Do you want to contribute
   them to the public record? I'll walk through the activity types."
3. **Flag anything sensitive** — "Activities like [X] are set to contribute. Does
   anything here feel private? I can leave those out."
4. **Remind about location and name** — "I'll use [location] for location data.
   Your name stays out unless you want first-reporter credit on specific actions."
5. **Run the pipeline** — dry-run first, then the real thing.

### Environment Variables

```
HAR_PUBLIC_CONTRIBUTE=true          # Required — opt in
HAR_PUBLIC_LOCATION="Cedar City, UT"  # Optional — for location breakdowns
HAR_PUBLIC_DISPLAY_NAME="HappyBrain"  # Optional — first-reporter credit
```

## Build & Serve

```bash
# Install dependencies (required before first build)
pip3 install -r requirements.txt

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
├── public-actions/      # Opt-in public action record (see PUBLIC-ACTIONS.md)
│   ├── actions/         # Action registry — one file per activity type
│   ├── entries/         # Anonymized contribution data (JSONL)
│   └── aggregates/      # Computed stats by action and location
├── scripts/
│   ├── build-har-derived.py    # Build the derived data
│   ├── serve-har-dashboard.py  # Start the web dashboard
│   └── har-contribute.py       # Anonymize and contribute to public record
├── maps/
│   ├── action-categories.yaml  # Category mapping config
│   └── frontmatter-schema.yaml # Canonical frontmatter schema
├── plans/               # Daily plans (optional)
├── start.sh             # Launches the dashboard
├── README.md            # Full project philosophy and setup
├── AGENTS.md            # This file — agent instructions
└── PUBLIC-ACTIONS.md    # Opt-in public record documentation
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
