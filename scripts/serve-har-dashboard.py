#!/usr/bin/env python3
"""
serve-har-dashboard.py — HAR Web Dashboard Server

Serves the HAR web dashboard on port 8093.
Routes:
  /                        — SPA shell (index.html)
  /style.css               — CSS
  /app.js                  — JS
  /api/meta                — Meta info from har-data.json
  /api/home                — Home section
  /api/time-stats?range=X   — Time stats for range (7d/30d/all)
  /api/notes               — All notes; ?activity=slug to filter
  /api/calendar?year=&month= — Calendar month data
  /api/day/YYYY-MM-DD      — Day detail
  /api/plans               — Ideal routines
  /api/save-plan (POST)    — Save a plan for a specific date

Usage:
    python3 scripts/serve-har-dashboard.py

Open http://localhost:8093 in a browser.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

REPO_ROOT = Path(__file__).resolve().parents[1]
HAR_WEB_ROOT = REPO_ROOT / "_har_web"
DATA_PATH = REPO_ROOT / "_derived" / "har-data.json"
PLANS_ROOT = REPO_ROOT / "plans" / "daily"
PORT = 8093

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}

_data: dict[str, Any] | None = None


def load_data() -> dict[str, Any]:
    global _data
    if _data is None:
        _data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return _data


class HARDashboardHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        # Quiet logging — only errors
        pass

    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_error(404, "Not Found")
            return
        body = path.read_bytes()
        ext = path.suffix.lower()
        mime = MIME_TYPES.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, msg: str) -> None:
        self._send_json({"error": msg}, status)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        try:
            data = load_data()
        except Exception as e:
            self._send_error(500, f"Failed to load data: {e}")
            return

        # Static files
        if path == "/":
            self._send_file(HAR_WEB_ROOT / "index.html")
        elif path == "/style.css":
            self._send_file(HAR_WEB_ROOT / "style.css")
        elif path == "/app.js":
            self._send_file(HAR_WEB_ROOT / "app.js")

        # API routes
        elif path == "/api/meta":
            self._send_json(data.get("meta", {}))

        elif path == "/api/home":
            self._send_json(data.get("home", {}))

        elif path == "/api/time-stats":
            range_key = (params.get("range") or ["7d"])[0]
            ranges = data.get("time_stats", {}).get("ranges", {})
            result = ranges.get(range_key)
            if result is None:
                self._send_error(400, f"Unknown range: {range_key}. Use 7d, 30d, or all.")
            else:
                self._send_json(result)

        elif path == "/api/notes":
            notes = data.get("notes", {})
            activity_slug = (params.get("activity") or [None])[0]
            if activity_slug:
                by_activity = notes.get("by_activity", {})
                filtered = by_activity.get(activity_slug)
                if filtered is None:
                    self._send_error(404, f"Activity '{activity_slug}' not found")
                else:
                    self._send_json({"activity": activity_slug, "notes": filtered})
            else:
                self._send_json(notes)

        elif path == "/api/calendar":
            year_str = (params.get("year") or [""])[0]
            month_str = (params.get("month") or [""])[0]
            month_data_list = data.get("calendar", {}).get("month_data", [])
            if year_str and month_str:
                year, month = int(year_str), int(month_str)
                for md in month_data_list:
                    if md.get("year") == year and md.get("month") == month:
                        self._send_json(md)
                        return
                self._send_error(404, f"No data for {year}-{month}")
            else:
                self._send_json(month_data_list)

        elif path.startswith("/api/day/"):
            day_str = path[len("/api/day/"):]
            day_detail = data.get("day_detail", {})
            result = day_detail.get(day_str)
            if result is None:
                self._send_json({
                    "date": day_str,
                    "weekday": "",
                    "state": "neutral",
                    "match_score": None,
                    "plan": None,
                    "actuals": [],
                    "message": "This day hasn't been touched yet",
                })
            else:
                self._send_json(result)

        elif path == "/api/wikis":
            self._send_json(data.get("wikis", {}))

        elif path.startswith("/api/wikis/"):
            slug = path[len("/api/wikis/"):]
            by_activity = data.get("wikis", {}).get("by_activity", {})
            result = by_activity.get(slug)
            if result is None:
                self._send_error(404, f"Activity wiki '{slug}' not found")
            else:
                self._send_json(result)

        elif path == "/api/plans":
            self._send_json(data.get("plans", {}))

        else:
            self._send_error(404, f"Unknown path: {path}")

    # ──────────────────────────────────────────
    # POST /api/save-plan — Save a plan for a specific date
    # ──────────────────────────────────────────
    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/save-plan":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
                payload = json.loads(body)
                date_str = payload.get("date", "")
                plan_text = payload.get("plan", "")

                if not date_str:
                    self._send_error(400, "Missing 'date' field")
                    return
                if not plan_text:
                    self._send_error(400, "Missing 'plan' field")
                    return

                # Save plan as a markdown file
                PLANS_ROOT.mkdir(parents=True, exist_ok=True)
                plan_path = PLANS_ROOT / f"{date_str}.md"
                plan_content = plan_text.strip() + "\n"
                plan_path.write_text(plan_content, encoding="utf-8")

                self._send_json({
                    "status": "ok",
                    "date": date_str,
                    "plan_preview": plan_text[:200] + ("…" if len(plan_text) > 200 else ""),
                })
            except json.JSONDecodeError:
                self._send_error(400, "Invalid JSON body")
            except Exception as e:
                self._send_error(500, f"Failed to save plan: {e}")
        else:
            self._send_error(404, f"Unknown path: {path}")


def main() -> int:
    # Ensure data file exists
    if not DATA_PATH.exists():
        print("Error: har-data.json not found. Run build-har-derived.py first.")
        return 1

    server = HTTPServer(("127.0.0.1", PORT), HARDashboardHandler)
    print(f"HAR Dashboard → http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
