import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path("/Users/calebsmacmini/HAR/scripts/build-har-derived.py")
SPEC = importlib.util.spec_from_file_location("build_har_derived", MODULE_PATH)
build_har_derived = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(build_har_derived)


class ReadEntryTests(unittest.TestCase):
    def test_read_entry_uses_frontmatter_notes_when_body_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            entry_path = Path(tmpdir) / "2026-06-18-evening-relaxation.md"
            entry_path.write_text(
                "\n".join(
                    [
                        "---",
                        'type: "action"',
                        'date: "2026-06-18"',
                        'weekday: "Thursday"',
                        'time: "18:10"',
                        'activity: "Dinner and Relaxation"',
                        "duration: 80",
                        'category: "personal"',
                        'subcategory: "general"',
                        'source: "scribe"',
                        'notes: Made dinner, ate, smoked. Played guitar while relaxing.',
                        "---",
                        "",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            entry = build_har_derived.read_entry(entry_path)

            self.assertTrue(entry["has_notes"])
            self.assertEqual(
                entry["notes"],
                "Made dinner, ate, smoked. Played guitar while relaxing.",
            )


class ShellOverviewHtmlTests(unittest.TestCase):
    def test_signature_rows_skip_empty_time_chrome(self) -> None:
        html = build_har_derived._shell_overview_signature_rows_html(
            "Activity Read",
            [
                {
                    "title": "Dinner and Relaxation",
                    "detail": "Made dinner, ate, smoked.",
                    "duration": "",
                    "meta": "",
                }
            ],
        )

        self.assertNotIn(
            "har-time-shell-category-strip-pill-breakdown-row-time",
            html,
        )
        self.assertIn("Made dinner, ate, smoked.", html)

    def test_signature_rows_drop_redundant_no_detail_badge(self) -> None:
        badge = build_har_derived._meaning_row_state_badge(
            {
                "title": "Mid-morning Reset",
                "state": "no detail captured",
                "detail": "No notes or stat names captured",
            }
        )

        html = build_har_derived._shell_overview_signature_rows_html(
            "Activity Read",
            [
                {
                    "title": "Mid-morning Reset",
                    "state_badge": badge,
                    "detail": "No notes or stat names captured",
                }
            ],
        )

        self.assertEqual(badge, "")
        self.assertNotIn("har-time-shell-category-strip-pill-breakdown-row-state", html)
        self.assertIn("No notes or stat names captured", html)


if __name__ == "__main__":
    unittest.main()
