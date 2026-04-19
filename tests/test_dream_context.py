import tempfile
import time
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dream.context import discover_dream_assets, build_dream_system_context


class DreamContextTests(unittest.TestCase):
    def test_discover_dream_assets_prefers_newest_memory_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            older = root / "dream_old.json"
            latest = root / "dream_weather.json"
            log_file = root / "dream.log"
            older.write_text('{"topic":"old","cycle_history":[],"knowledge_base":[]}')
            time.sleep(0.01)
            latest.write_text(
                '{"topic":"meteorology","cycle_history":[{"score":0.8}],"knowledge_base":["fact"],"session_best_score":0.8,"weak_areas":["fronts"],"current_research":{"sources":[{"domain":"weather.gov"}]}}'
            )
            log_file.write_text("2026-04-18 18:00:00 | INFO | dream.session | started\n")

            assets = discover_dream_assets(root)
            self.assertEqual(assets["latest_memory"], latest.resolve())
            self.assertEqual(assets["latest_log"], log_file.resolve())
            self.assertEqual(assets["snapshot"]["topic"], "meteorology")
            self.assertEqual(assets["snapshot"]["research_sources"], 1)

            context = build_dream_system_context(cwd=root, include_schema=False)
            self.assertIn("Dream entrypoint", context)
            self.assertIn(str(latest.resolve()), context)
            self.assertIn(str(log_file.resolve()), context)
            self.assertIn("dream_summary", context)


if __name__ == "__main__":
    unittest.main()
