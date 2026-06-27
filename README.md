# HAR — Human Action Record

> *You talk to an agent. It writes it down. Over time, a record emerges.*

---

Remembering what you actually did yesterday is hard. Remembering last week is almost impossible. Remembering last month and actually learning from it? Most people don't even try.

Journaling apps ask you to write. Planners ask you to plan. Time trackers ask you to start and stop timers. They all require *you* to do the work of structuring your life into boxes. Life doesn't fit in boxes.

HAR is different. **You just talk.** You tell an AI agent what you did — "went for a run, grabbed lunch, then worked on the project for a few hours" — and it writes structured markdown files. A local web dashboard turns that into category breakdowns, calendar views, and stats over time.

No accounts. No subscriptions. No cloud. Your life, in files you own, organized by an agent that actually listens.

---

## Two Parts, One System

### Part 1: The Private Journal

This is the tool you use every day. An AI agent captures what you do in natural language and writes it to markdown files with YAML frontmatter. The agent categorizes everything automatically — you never pick a category, never fill out a form, never start or stop a timer.

You get:

- **A daily log.** Every file is a timestamped record of one activity. Over time, they accumulate into a complete picture of how you spend your time.
- **A web dashboard.** Category breakdowns, calendar views, activity stats, time summaries — all local, all yours. Open `http://localhost:8093` and see your week.
- **An agent you can talk to.** It knows what you did. Ask it anything — *"how much time did I spend on my project this week?"*, *"when was the last time I worked out?"*, *"what did I do on Thursday?"* It reads the files and answers.

**This replaces:** journaling apps, planners, time trackers, habit trackers, bullet journals. Anything that asks you to manually log your life.

**This is about:** being honest with yourself about how you spend your time. Not optimizing, not gamifying, not performing for anyone. Just keeping a record that you can actually look back on and learn from.

---

### Part 2: The Public Record

Think about what people actually do where you live. Not what Instagram shows. Not what surveys claim. Not what AI hallucinates when you ask it. What do real people actually spend their time on?

You don't know. Nobody knows. Because that data doesn't exist anywhere.

Surveys are small and biased. Social media is performative. AI models guess based on internet text, which is mostly nonsense. There is no honest, large-scale record of real human behavior. HAR's public record exists to change that.

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

## Getting Started

HAR is designed to be set up by an **AI agent that can read a repo and run Python scripts**. A normal chat won't cut it — you need a tool that works in your terminal or development environment.

**1. Give your agent this repo's URL.**

Paste `https://github.com/HappyBrainCS/HAR` to your agent. It reads `AGENTS.md` and handles everything — cloning, dependencies, setup, capture, dashboard.

**2. Recommended agents:**

- **Claude Code** (Anthropic) — terminal-native, great for setup and capture.
- **Codex CLI** (OpenAI) — terminal-based, works well for setup and scripting.
- **OpenClaw + DeepSeek V4 Flash** — most affordable option. Install [OpenClaw](https://openclaw.ai), use DeepSeek V4 Flash, and give it this repo URL.

You just talk to it.

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

Every entry has this format. Your agent writes it. You never touch the file. It just appears in your calendar directory and the dashboard picks it up.

## Project Structure

```
~/HAR/
├── calendar/            # YOUR data — markdown files, one per activity
├── _derived/            # Generated dashboard data (rebuild after new entries)
├── _har_web/            # Dashboard frontend (HTML, CSS, JS)
├── public-actions/      # Opt-in public record
│   ├── actions/         # Registry of action types with aggregate stats
│   ├── entries/         # Anonymized contribution data (per-contributor)
│   └── aggregates/      # Computed global stats
├── scripts/             # Build, serve, contribute, reindex
├── maps/                # Category config, frontmatter schema
├── AGENTS.md            # ⬅ This is what your agent reads
├── PUBLIC-ACTIONS.md    # Full opt-in public record documentation
└── README.md            # This file
```

## Future: Data Co-op

If HAR reaches **500+ contributors** with meaningful data, the plan is to spin up an **opt-in data co-op** where contributors share revenue from licensed aggregate insights. No personal data is ever sold — only anonymized, aggregated datasets. The project takes a transparent small cut for operations; most revenue goes back to contributors.

Right now, that's a future goal. But it's why HAR exists — to create an ethical alternative to the data brokers who profit from your behavior without asking. If you want in, you join because the public record is worth building. If we grow big enough, there's a path to make it financially meaningful too.

## License

MIT — do whatever you want. Fork it, extend it, build something on top. The public dataset belongs to the contributors.

---

*Built for myself. Shared in case it's useful to you. — Caleb*
