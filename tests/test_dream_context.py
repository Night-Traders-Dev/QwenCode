import tempfile
import time
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dream.context import (
    discover_dream_assets,
    build_dream_system_context,
    build_dream_recall_context,
    enrich_user_with_dream_recall,
    is_research_heavy_prompt,
)


class FakeMemoryStore:
    def __init__(self, rows_by_category=None):
        self.rows_by_category = rows_by_category or {}
        self.search_calls = []

    def search_knowledge(self, query, limit=5, category=None):
        self.search_calls.append((query, limit, category))
        rows = list(self.rows_by_category.get(category, []))
        return rows[:limit]

    def list_knowledge(self, category=None, limit=5):
        rows = list(self.rows_by_category.get(category, []))
        return rows[:limit]


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

    def test_research_heavy_prompt_detection(self) -> None:
        self.assertTrue(is_research_heavy_prompt("Explain the latest weather forecast and sources for Kentucky"))
        self.assertFalse(is_research_heavy_prompt("hi"))

    def test_build_dream_recall_context_prefers_matching_categories(self) -> None:
        memory_store = FakeMemoryStore(
            {
                "dream_knowledge": [
                    {"content": "Cold fronts often produce sharp wind shifts and convective instability."}
                ],
                "dream_source": [
                    {
                        "content": '{"title":"Severe Weather Outlook","domain":"weather.gov","url":"https://weather.gov/example","snippet":"Storm risk increases late afternoon with hail and gusty winds."}'
                    }
                ],
            }
        )
        recall = build_dream_recall_context(
            "Analyze the latest severe weather outlook for Kentucky with sources",
            memory_store=memory_store,
        )
        self.assertIn("dream_knowledge", recall)
        self.assertIn("weather.gov", recall)
        self.assertTrue(memory_store.search_calls)

    def test_enrich_user_with_dream_recall_adds_context_block(self) -> None:
        memory_store = FakeMemoryStore(
            {
                "dream_source": [
                    {
                        "content": '{"title":"Forecast Discussion","domain":"weather.gov","url":"https://weather.gov/fd","snippet":"Confidence remains high for warm, dry conditions."}'
                    }
                ]
            }
        )
        enriched = enrich_user_with_dream_recall(
            "Tell me about the weather trend this week",
            memory_store=memory_store,
        )
        self.assertIn("Relevant Dream memory recalled", enriched)
        self.assertIn("User request:", enriched)
        self.assertIn("weather trend this week", enriched)


if __name__ == "__main__":
    unittest.main()
