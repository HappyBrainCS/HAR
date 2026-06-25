# HAR — Human Action Record

> *You talk to an AI. It writes the truth. Over time, a record emerges.*

---

Remembering what you actually did yesterday is hard. Remembering what you did last week is almost impossible. Remembering what you did last month and actually learning from it? Most people don't even try.

Journaling apps ask you to write. Planners ask you to plan. Time trackers ask you to start and stop timers. None of them work because they all require *you* to do the work of structuring your life into boxes. Life doesn't fit in boxes.

HAR is different. **You just talk.** You tell an AI agent what you did — "worked on the dashboard for a couple hours, then made lunch, then went for a walk" — and it writes structured markdown files. A local web dashboard turns that into category breakdowns, calendar views, and stats over time.

No accounts. No subscriptions. No cloud. Your life, in files you own, organized by an AI that actually listens.

---

## Two Parts, One System

### Part 1: The Private Journal

This is the tool you use every day. An AI agent captures what you do in natural language and writes it to markdown files with YAML frontmatter. The agent categorizes everything automatically — you never pick a category, never fill out a form, never start or stop a timer.

You get:

- **A daily log.** Every file is a timestamped record of one activity. Over time, they accumulate into a complete picture of how you spend your time.
- **A web dashboard.** Category breakdowns, calendar views, activity stats, time summaries — all local, all yours. Open `http://localhost:8093` and see your week.
- **An AI you can talk to.** Your agent knows what you did. Ask it anything — *"how much time did I spend on my project this week?"*, *"when was the last time I worked out?"*, *"what did I do on Thursday?"* It reads the files and answers.

**This replaces:** journaling apps, planners, time trackers, habit trackers, bullet journals. Anything that asks you to manually log your life.

**This is not:** a replacement for therapy, a productivity optimizer, or a tool your employer can use to bill clients. It's a record. What you do with it is up to you.

---

### Part 2: The Public Record

> *This is the more important piece.*

A public dataset of real human behavior — anonymized, aggregated, and contributed by people who opted in. What do people actually do? Not what surveys say, not what Instagram shows, not what productivity gurus claim. Real data from real people telling the truth about their day.

**How it works:**

1. You use HAR privately (Part 1). Your agent writes your calendar files.
2. You opt into the public record by setting an environment variable.
3. Your agent runs the contribution pipeline. It anonymizes your entries — strips notes, names, exact times, stats. Keeps only: action type, duration, day of week, time of day, and (if you choose) your city.
4. The anonymized data is added to the public record. Your agent can then query the public record for comparisons — *"how much time do people in my city spend on disc golf?"*, *"what's the average workout duration?"*, *"when do most people start their workday?"*

**You control what gets shared.** Some activities are fine to share (workouts, project work, gaming). Some aren't (personal time). Your agent asks, you decide. Per-entry opt-in, not all-or-nothing.

**No personal data ever leaves your machine.** The pipeline strips everything identifying before writing a single byte to the public record. The code is open source. You can verify exactly what gets shared by running `--dry-run`.

**Why this matters:**

- **For you:** Your agent can answer questions with real data. *"How does my running compare to other people in my city?"* It's not guessing. It's reading the public record.
- **For everyone:** A honest dataset of human behavior — not curated, not performative, not extrapolated from a survey of 200 college students. Real behavior, real patterns, real diversity.
- **For researchers:** Genuinely useful data. If 500 people in a city are contributing, you can ask questions like *"what time do people actually go to sleep?"* and get an answer that means something.

The public record is currently small (one contributor, ~50 entries). It grows one honest entry at a time.

---

## Quick Start

### 1. Give your AI agent these instructions

The file you're looking for is **`AGENTS.md`** in this repo. Give it to your AI agent (OpenClaw, Claude Code, ChatGPT, etc.). The agent reads it and handles everything — capture, categorization, file writing, dashboard building.

If you're reading this on GitHub, open `AGENTS.md` and give the entire contents to your agent.

### 2. Set up the tooling

```bash
git clone https://github.com/HappyBrainCS/HAR.git ~/HAR
cd ~/HAR
python3 scripts/build-har-derived.py   # generate the dashboard data
python3 scripts/serve-har-dashboard.py  # start the web dashboard
```

Open `http://localhost:8093` in your browser. It'll be empty — that's fine. You fill it by talking to your agent.

### 3. Start talking

Your agent will ask you what you did today. Tell it. That's it.

Over time, your agent learns your routine and starts prompting proactively: *"You usually do push+quad on Thursdays — get that in this morning?"*

### 4. (Optional) Opt into the public record

```bash
export HAR_PUBLIC_CONTRIBUTE=true
export HAR_PUBLIC_LOCATION="Your City, ST"
python3 scripts/har-contribute.py --dry-run  # See what would be shared
python3 scripts/har-contribute.py --contributor your-username
```

Your agent will walk you through the process — what gets shared, what stays private, how the matching works.

---

## What a Captured Entry Looks Like

```markdown
---
type: action
date: 2026-05-17
weekday: Sunday
time: "09:15"
activity: Disc Golf Practice Putting
duration: 30
category: health
subcategory: disc-golf
source: scribe
capture_mode: conversational
public_action_id: disc-golf-putting
---

From 9:15 to 9:45 I practiced putting at the basket in the back yard. Did
about 80 putts. Rocking the straight-back motion improved accuracy noticeably.
```

Every entry has this format. The agent writes it. You never touch the file. It just appears in your calendar directory and the dashboard picks it up.

---

## Project Structure

```
~/HAR/
├── calendar/            # YOUR data — markdown files, one per activity
├── _derived/            # Generated dashboard data (rebuild after new entries)
├── _har_web/            # Dashboard frontend (HTML, CSS, JS)
├── public-actions/      # Opt-in public record (see PUBLIC-ACTIONS.md)
│   ├── actions/         # Registry of action types with aggregate stats
│   ├── entries/         # Anonymized contribution data (per-contributor)
│   └── aggregates/      # Computed global stats
├── scripts/
│   ├── build-har-derived.py    # Build dashboard data from calendar files
│   ├── serve-har-dashboard.py  # Start the web dashboard
│   ├── har-contribute.py       # Anonymize and contribute to public record
│   └── har-reindex.py          # Rebuild aggregates from all contributors
├── maps/                # Category config, frontmatter schema
├── AGENTS.md            # ⬅ Give this to your AI agent
├── PUBLIC-ACTIONS.md    # Full opt-in public record documentation
└── README.md            # This file
```

## License

MIT — do whatever you want. Fork it, extend it, build something on top. The public dataset belongs to the contributors.

---

*Built for myself. Shared in case it's useful to you. — Caleb*
